#!/usr/bin/env python3
"""Exercise Wine's exact patched WGL resolver with isolated C dispatch fixtures.

The source must already contain Gluon's patches. --source-only extracts and
prepares the fixtures without a compiler or executable, and is safe locally.
Compilation and execution require a Linux GitHub Actions runner. No Wine process,
graphics API, game, or native driver is used.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


FUNCTIONS = ("string_array_cmp", "is_extension_supported", "is_any_extension_supported",
             "registry_entry_cmp", "wrap_wglGetProcAddress")
ENTRIES = ("glDrawArraysInstanced", "wglCreateContextAttribsARB",
           "wglGetExtensionsStringARB", "wglGetExtensionsStringEXT")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def function(source, name):
    """Copy a whole function, treating braces in comments/strings as data."""
    match = re.search(r"(?m)^(?:static\s+)?(?:int|BOOL|PROC)\s+" + re.escape(name) + r"\s*\(", source)
    require(match is not None, "Missing Wine function: " + name)
    opening = source.find("{", match.end())
    require(opening >= 0 and ";" not in source[match.end():opening],
            "Missing Wine function body: " + name)
    tokens = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', re.S)
    depth = 0
    for token in tokens.finditer(source, opening):
        if token[0] == "{":
            depth += 1
        elif token[0] == "}":
            depth -= 1
            if not depth:
                return source[match.start():token.end()]
    raise ValueError("Unclosed Wine function body: " + name)


PRELUDE = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define TRUE 1
#define FALSE 0
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define WARN(...) ((void)0)
#define TRACE(...) ((void)0)
typedef int BOOL;
typedef void *PROC;
typedef const char *LPCSTR;
typedef uintptr_t UINT_PTR;
typedef struct { void *glTable; } TEB;
struct context {
    int major_version, minor_version;
    const char **extension_array;
    size_t extension_count;
};
struct opengl_funcs {
    PROC (*p_wglGetProcAddress)(const char *name);
    PROC p_glDrawArraysInstanced;
    PROC p_wglCreateContextAttribsARB;
    PROC p_wglGetExtensionsStringARB;
    PROC p_wglGetExtensionsStringEXT;
    PROC p_wglMissingFixture;
};
struct registry_entry { const char *name, *extension; size_t offset; };
static struct context *current_context;
static unsigned int driver_calls;
static struct context *get_current_context(TEB *teb, void *draw, void *read)
{
    (void)teb; (void)draw; (void)read;
    return current_context;
}
static PROC fixture_driver(const char *name)
{
    driver_calls++;
    /* Wine's win32u driver resolves WGL through prefilled slots, never dlsym. */
    if (!strncmp(name, "wgl", 3)) return NULL;
    return (void *)(UINT_PTR)0x1234;
}
'''


HARNESS = r'''
static unsigned int failures;
#define CHECK(condition, label) do { if (!(condition)) { \
    fprintf(stderr, "FAIL: %s\n", label); failures++; } } while (0)
int main(void)
{
    const char *empty_extensions[] = {NULL};
    struct context context = {3, 1, empty_extensions, 0};
    struct opengl_funcs funcs = {
        .p_wglGetProcAddress = fixture_driver,
        .p_wglCreateContextAttribsARB = (void *)1,
        .p_wglGetExtensionsStringARB = (void *)1,
        .p_wglGetExtensionsStringEXT = (void *)1,
    };
    TEB teb = {&funcs};
    size_t i;
    for (i = 1; i < extension_registry_size; i++)
        CHECK(strcmp(extension_registry[i - 1].name, extension_registry[i].name) < 0,
              "fixture registry is sorted");

    current_context = &context;
    CHECK(wrap_wglGetProcAddress(&teb, "glDrawArraysInstanced") == (void *)0,
          "supported GL function accepts registry index zero");
    CHECK(funcs.p_glDrawArraysInstanced == (void *)(UINT_PTR)0x1234 && driver_calls == 1,
          "supported GL lookup warms the shared native cache");

    context.major_version = 2;
    context.minor_version = 1;
    CHECK(wrap_wglGetProcAddress(&teb, "glDrawArraysInstanced") == (void *)-1,
          "cached GL function is rejected in an unsupported legacy context");
    CHECK(driver_calls == 1, "unsupported cached GL never reaches driver lookup");

    /* These requirements are WGL strings, absent from this GL extension array.
     * Their prefilled Wine dispatch slots must still return the real PE index. */
    for (i = 1; i <= 3; i++)
        CHECK(wrap_wglGetProcAddress(&teb, extension_registry[i].name) == (void *)(UINT_PTR)i,
              extension_registry[i].name);
    CHECK(driver_calls == 1, "prefilled WGL dispatch does not call native lookup");
    CHECK(wrap_wglGetProcAddress(&teb, "wglMissingFixture") == (void *)-1,
          "missing WGL dispatch remains unavailable");
    CHECK(driver_calls == 2, "missing WGL slot reaches the rejecting driver lookup");

    context.major_version = 3;
    context.minor_version = 1;
    CHECK(wrap_wglGetProcAddress(&teb, "glDrawArraysInstanced") == (void *)0,
          "supported cached GL still accepts registry index zero");
    CHECK(driver_calls == 2, "supported cached GL does not repeat native lookup");

    current_context = NULL;
    CHECK(wrap_wglGetProcAddress(&teb, "wglCreateContextAttribsARB") == (void *)-1,
          "WGL lookup without an active context stays unavailable");
    CHECK(wrap_wglGetProcAddress(&teb, "glDrawArraysInstanced") == (void *)-1,
          "cached GL lookup without an active context stays unavailable");
    CHECK(driver_calls == 2, "no-context lookups never reach the driver");
    if (failures) return 1;
    puts("Exact Wine resolver namespace, context and shared-cache fixtures passed");
    return 0;
}
'''


def fixtures(wine_source):
    wrapper = (wine_source / "dlls/opengl32/unix_wgl.c").read_text()
    registry = (wine_source / "dlls/opengl32/unix_thunks.c").read_text()
    extracted = {name: function(wrapper, name) for name in FUNCTIONS}
    entries = []
    for name in ENTRIES:
        matches = re.findall(r'\{\s*"' + name + r'"\s*,\s*"[^"\n]*"\s*,\s*'
                             r'offsetof\(struct opengl_funcs,\s*p_' + name + r'\)\s*\}', registry)
        require(len(matches) == 1, "Expected one actual Wine registry entry: " + name)
        entries.append(matches[0])
    entries.append('{ "wglMissingFixture", "WGL_fixture_missing\\0", '
                   'offsetof(struct opengl_funcs, p_wglMissingFixture) }')
    table = "\nstatic const struct registry_entry extension_registry[] = {\n" + \
            ",\n".join(entries) + "\n};\n" + \
            "static const size_t extension_registry_size = ARRAY_SIZE(extension_registry);\n"

    def render(functions):
        return PRELUDE + table + "\n".join(functions[name] for name in FUNCTIONS) + HARNESS

    actual = render(extracted)
    changed = dict(extracted)
    changed["wrap_wglGetProcAddress"], count = re.subn(
        r'!strncmp\s*\(\s*name\s*,\s*"gl"\s*,\s*2\s*\)\s*&&\s*', "",
        changed["wrap_wglGetProcAddress"])
    require(count == 1, "Expected exactly one GL namespace guard for the regression mutation")
    return actual, render(changed)


def compile_and_run(source, directory, name):
    path = directory / (name + ".c")
    binary = directory / name
    path.write_text(source)
    subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-O1",
                    str(path), "-o", str(binary)], check=True, timeout=20)
    return subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wine_source", type=Path)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        actual, regression = fixtures(args.wine_source)
        print("Prepared exact Wine dispatch fixture SHA-256 " + hashlib.sha256(actual.encode()).hexdigest())
        if args.source_only:
            print("Source-only check passed; dispatch fixtures were not compiled or executed")
            if args.self_test:
                print("Prepared unconditional-GL-gate regression fixture; execution requires Actions")
            return 0
        if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
            parser.error("fixture compilation/execution requires Linux GitHub Actions; use --source-only locally")
        with tempfile.TemporaryDirectory(prefix="gluon-dispatch-check-") as directory:
            root = Path(directory)
            result = compile_and_run(actual, root, "actual-resolver")
            require(result.returncode == 0, "Actual resolver fixture failed:\n" + result.stdout + result.stderr)
            print(result.stdout, end="")
            if args.self_test:
                result = compile_and_run(regression, root, "unconditional-gl-gate")
                require(result.returncode != 0 and all("FAIL: " + name in result.stderr for name in ENTRIES[1:]),
                        "Unconditional GL gate was not rejected by all three WGL fixtures:\n" +
                        result.stdout + result.stderr)
                print("Rejected unconditional GL gate through all three WGL dispatch fixtures")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("OpenGL dispatch validation failed: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

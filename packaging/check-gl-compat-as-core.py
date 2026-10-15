#!/usr/bin/env python3
"""Check Gluon's opt-in macdrv compatibility-as-core OpenGL mapping without Wine.

--source-only is safe for local review. Compilation and execution of the isolated
opt-in decision helper require a GitHub Actions Linux host; no Wine module, CGL
or other graphics API is linked or called.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


CORE, COMPAT, FWD = 0x1, 0x2, 0x2
# (GLUON_GL_COMPAT_AS_CORE value or None, major, minor, profile, flags, mapped)
CASES = (
    (None, 3, 3, COMPAT, 0, False),
    (None, 3, 0, CORE, 0, False),
    (None, 3, 2, CORE, FWD, False),
    ("0", 3, 3, COMPAT, 0, False),
    ("", 3, 3, COMPAT, 0, False),
    ("true", 3, 3, COMPAT, 0, False),
    ("10", 3, 3, COMPAT, 0, False),
    ("1", 3, 3, COMPAT, 0, True),     # Primordialis
    ("1", 3, 3, CORE, 0, True),
    ("1", 3, 3, COMPAT, FWD, True),
    ("1", 3, 3, CORE, FWD, False),    # already a supported core request
    ("1", 3, 2, COMPAT, 0, True),
    ("1", 4, 0, COMPAT, 0, True),
    ("1", 4, 1, CORE, 0, True),
    ("1", 4, 1, CORE, FWD, False),
    ("1", 3, 0, CORE, 0, True),
    ("1", 3, 0, CORE, FWD, True),
    ("1", 3, 1, COMPAT, FWD, True),
    ("1", 3, 4, COMPAT, 0, False),
    ("1", 4, 2, COMPAT, 0, False),
    ("1", 4, 6, CORE, FWD, False),
    ("1", 5, 0, COMPAT, 0, False),
    ("1", 2, 1, COMPAT, 0, False),
    ("1", 1, 0, CORE, 0, False),
)

REQUIRED = (
    # Context state and optional entry points.
    "BOOL compat_as_core; GLuint default_vao; };",
    "static void (*pglGenVertexArrays)(GLsizei n, GLuint *arrays);",
    "static void (*pglBindVertexArray)(GLuint array);",
    'pglGenVertexArrays = dlsym(opengl_handle, "glGenVertexArrays");',
    'pglBindVertexArray = dlsym(opengl_handle, "glBindVertexArray");',
    # The opt-in branch comes first and keeps the original branches after it.
    "BOOL core = FALSE, compat_as_core = FALSE;",
    "if (gluon_compat_as_core_request(major, minor, profile, flags)) {",
    "if (major > gl_info.max_major || (major == gl_info.max_major && minor > gl_info.max_minor))"
    " { WARN(\"This GL implementation does not support the requested GL version %u.%u\\n\", major, minor);"
    " RtlSetLastWin32Error(ERROR_INVALID_VERSION_ARB); return FALSE; }",
    "if (!pglGenVertexArrays || !pglBindVertexArray) {",
    "compat_as_core = TRUE; core = TRUE; } else if ((major == 3 && (minor == 2 || minor == 3)) ||"
    " (major == 4 && (minor == 0 || minor == 1))) { /* CW Hack 24834 */",
    # Without the opt-in, every original rejection remains.
    'WARN("OS X only supports forward-compatible 3.2+ contexts\\n");',
    'WARN("Compatibility profiles for GL version >= 3.2 not supported\\n");',
    'WARN("Profile version %u.%u not supported\\n", major, minor);',
    "context->compat_as_core = compat_as_core;"
    " if (!create_context(context, share_context ? share_context->cglcontext : NULL, major))",
    # A mapped context draws through its own default vertex array object.
    "make_context_current(context, FALSE); NtCurrentTeb()->glReserved2 = context;",
    "if (context->compat_as_core && !context->default_vao) {"
    " pglGenVertexArrays(1, &context->default_vao); pglBindVertexArray(context->default_vao);",
)


def normalized(value):
    return re.sub(r"\s+", "", value)


def extract_helper(source):
    matches = re.findall(
        r"^static BOOL gluon_compat_as_core_request\(int major, int minor, int profile, int flags\)\n\{.*?^\}",
        source, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError("expected exactly one gluon_compat_as_core_request helper")
    return matches[0]


def check_source(source):
    # Behavior of the decision helper is checked by compiling this exact
    # function below; these fragments pin how macdrv uses its result.
    flat = normalized(source)
    for fragment in REQUIRED:
        if normalized(fragment) not in flat:
            raise ValueError("macdrv compatibility-as-core mapping differs from the checked contract: "
                             + fragment[:70])
    if flat.count(normalized("gluon_compat_as_core_request(major, minor, profile, flags)")) != 1:
        raise ValueError("the opt-in decision must be used exactly once")
    helper = extract_helper(source)
    if 'getenv("GLUON_GL_COMPAT_AS_CORE")' not in helper:
        raise ValueError("the mapping must stay behind GLUON_GL_COMPAT_AS_CORE")


def fixture_source(helper):
    def literal(value):
        return "NULL" if value is None else '"%s"' % value
    cases = ",\n".join("    {%s, %d, %d, %#x, %#x, %d}" % (literal(value), major, minor, profile, flags, mapped)
                       for value, major, minor, profile, flags, mapped in CASES)
    return """#define _POSIX_C_SOURCE 200112L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#define WGL_CONTEXT_CORE_PROFILE_BIT_ARB 0x00000001
#define WGL_CONTEXT_COMPATIBILITY_PROFILE_BIT_ARB 0x00000002
#define WGL_CONTEXT_FORWARD_COMPATIBLE_BIT_ARB 0x00000002
""" + helper + """

int main(void)
{
    static const struct { const char *value; int major, minor, profile, flags, mapped; } cases[] = {
""" + cases + """
    };
    size_t i;
    unsigned failed = 0;
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        BOOL result;
        if (cases[i].value) setenv("GLUON_GL_COMPAT_AS_CORE", cases[i].value, 1);
        else unsetenv("GLUON_GL_COMPAT_AS_CORE");
        result = gluon_compat_as_core_request(cases[i].major, cases[i].minor,
                                              cases[i].profile, cases[i].flags);
        if (!!result != cases[i].mapped)
        {
            fprintf(stderr, "case %zu: switch %s, GL %d.%d profile %#x flags %#x -> %d (expected %d)\\n",
                    i, cases[i].value ? cases[i].value : "<unset>", cases[i].major, cases[i].minor,
                    cases[i].profile, cases[i].flags, !!result, cases[i].mapped);
            failed++;
        }
    }
    return !!failed;
}
"""


def run_fixture(helper, directory, name):
    source = directory / (name + ".c")
    binary = directory / name
    source.write_text(fixture_source(helper))
    subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-O1",
                    str(source), "-o", str(binary)], check=True, timeout=20)
    return subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wine_source", type=Path)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    source = (args.wine_source / "dlls/winemac.drv/opengl.c").read_text()
    check_source(source)
    helper = extract_helper(source)
    if args.self_test:
        for broken in (
            source.replace("pglBindVertexArray(context->default_vao);", ""),
            source.replace("context->compat_as_core = compat_as_core;", ""),
            source.replace('getenv("GLUON_GL_COMPAT_AS_CORE")', 'getenv("CX_FWD_COMPAT_GL_CTX")'),
            source.replace('WARN("Compatibility profiles for GL version >= 3.2 not supported\\n");', ""),
            source.replace("    else if ((major == 3 && (minor == 2 || minor == 3)) ||",
                           "    if ((major == 3 && (minor == 2 || minor == 3)) ||"),
        ):
            if broken == source:
                raise ValueError("a self-test mutation did not change the source")
            try:
                check_source(broken)
            except ValueError:
                continue
            raise ValueError("source check accepted an invalid compatibility-as-core mutation")
    if args.source_only:
        print("macdrv compatibility-as-core source contract passed; helper execution was not requested")
        return 0
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("helper compilation/execution requires GitHub Actions on Linux; use --source-only locally")
    with tempfile.TemporaryDirectory(prefix="gluon-gl-compat-") as temp:
        directory = Path(temp)
        result = run_fixture(helper, directory, "gl-compat-as-core")
        if result.returncode:
            raise ValueError("compatibility-as-core fixture failed:\n" + result.stderr)
        if args.self_test:
            mutations = (
                # Ignoring the switch would change every game's contexts.
                (helper.replace('if (!value || strcmp(value, "1")) return FALSE;', "(void)value;"),
                 "switch <unset>"),
                # Mapping requests macdrv already supports would bypass its checks.
                (helper.replace("return profile != WGL_CONTEXT_CORE_PROFILE_BIT_ARB || "
                                "!(flags & WGL_CONTEXT_FORWARD_COMPATIBLE_BIT_ARB);",
                                "(void)profile; (void)flags; return TRUE;"),
                 "flags 0x2 -> 1 (expected 0)"),
            )
            for name, (mutation, expected) in enumerate(mutations):
                if mutation == helper:
                    raise ValueError("a fixture mutation did not change the helper")
                result = run_fixture(mutation, directory, "gl-compat-as-core-mutation-%d" % name)
                if result.returncode != 1 or expected not in result.stderr:
                    raise ValueError("compatibility-as-core fixture accepted mutation %d" % name)
    print("macdrv compatibility-as-core helper passed %d cases%s" % (
        len(CASES), "; switch and supported-request mutations rejected" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("compatibility-as-core check: %s" % error, file=sys.stderr)
        sys.exit(1)

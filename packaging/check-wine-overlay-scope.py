#!/usr/bin/env python3
"""Check that Wine gives Steam's overlay only to game processes, never the server.

--source-only is safe for local review. Compiling the exact patched functions
with stubbed process-start calls requires a GitHub Actions Linux host; no Wine
module is linked and nothing is executed besides the fixture.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


OVERLAY = "/Steam/steamloader.dylib:/Steam/gameoverlayrenderer.dylib"
# (argv[2] program, image path, overlay expected)
CASES = (
    (r"/Users/me/Steam/steamapps/common/Game/Game.exe", r"/runtime/lib/wine/x86_64-unix/wine", True),
    (r"c:\windows\system32\steam.exe", r"/bundle/Game.app/Contents/MacOS/wine", False),
    (r'"Game.exe"', r"Z:\Users\me\Steam\steamapps\common\Game\Game.exe", True),
    (r"services.exe", r"C:\windows\system32\services.exe", False),
    (r"winedevice.exe", r"C:\WINDOWS\System32\winedevice.exe", False),
    (r"Launcher.exe", r"C:\Program Files\Game\Launcher.exe", True),
    (r"x.exe", r"\??\C:\windows\syswow64\x.exe", False),
    (r"wineboot", r"/runtime/lib/wine/x86_64-unix/wine", False),
    (r"/prefix/drive_c/windows/system32/explorer.exe", r"/runtime/wine", False),
)


def normalized(value):
    return re.sub(r"\s+", "", value)


def extract(source, signature):
    matches = re.findall(r"^" + re.escape(signature) + r"\n\{.*?^\}", source, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError("expected exactly one " + signature)
    return matches[0]


def check_source(source):
    game = extract(source, "static BOOL is_game_image( const char *program, const char *image_path )")
    scope = extract(source, "static void scope_steam_overlay( const char *program, const char *image_path )")
    spawn = extract(source, "static int build_path_and_exec( pid_t *pid, const char *dir, const char *name, char **argv )")
    preloader = extract(source, "static void preloader_exec( char **argv, const char *image_path )")
    required = (
        (game, 'game = !strstr( lower, ":\\\\windows\\\\" ) && !strstr( lower, "/drive_c/windows/" );'),
        (scope, 'if (*overlay && is_game_image( program, image_path )) setenv( "DYLD_INSERT_LIBRARIES", overlay, 1 ); '
                'else unsetenv( "DYLD_INSERT_LIBRARIES" );'),
        (spawn, 'if (strncmp( environ[count], "DYLD_INSERT_LIBRARIES=", 22 )) env[kept++] = environ[count];'),
        (spawn, "ret = posix_spawn( pid, argv[0], NULL, NULL, argv, env );"),
    )
    for text, fragment in required:
        if normalized(fragment) not in normalized(text):
            raise ValueError("overlay scope patch differs from the checked contract: " + fragment[:60])
    if not normalized(preloader).startswith(normalized("static void preloader_exec( char **argv, const char *image_path )"
                                                        "{ scope_steam_overlay( argv[2], image_path );")):
        raise ValueError("every program start must scope the overlay first")
    return game, scope, spawn


def fixture_source(game, scope, spawn):
    def literal(value):
        return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')
    cases = ",\n".join("    {%s, %s, %d}" % (literal(program), literal(image), expected)
                       for program, image, expected in CASES)
    return """#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
typedef int BOOL;
#define TRUE 1
#define FALSE 0
extern char **environ;
static char **spawned_env;
static char *build_path(const char *dir, const char *name)
{
    char *path;
    return asprintf(&path, "%s/%s", dir, name) < 0 ? NULL : path;
}
static int fixture_spawn(pid_t *pid, const char *path, const void *actions, const void *attributes,
                         char *const argv[], char *const env[])
{
    size_t count = 0, i;
    (void)path; (void)actions; (void)attributes; (void)argv;
    *pid = 1;
    while (env[count]) count++;
    spawned_env = calloc(count + 1, sizeof(*spawned_env));
    for (i = 0; i < count; i++) spawned_env[i] = strdup(env[i]);
    return 0;
}
#define posix_spawn fixture_spawn
""" + game + "\n\n" + scope + "\n\n" + spawn + """

static int has_overlay(char **env)
{
    for (; *env; env++) if (!strncmp(*env, "DYLD_INSERT_LIBRARIES=", 22)) return 1;
    return 0;
}

int main(void)
{
    static const struct { const char *program, *image; int overlay; } cases[] = {
""" + cases + """
    };
    size_t i;
    unsigned failed = 0;
    char *argv[3] = {NULL, NULL, NULL};
    pid_t pid;

    unsetenv("GLUON_OVERLAY_INSERT");
    setenv("DYLD_INSERT_LIBRARIES", "/unrelated.dylib", 1);
    scope_steam_overlay(cases[1].program, cases[1].image);
    if (!getenv("DYLD_INSERT_LIBRARIES")) { fprintf(stderr, "unset without Gluon's overlay variable\\n"); failed++; }

    setenv("GLUON_OVERLAY_INSERT", \"""" + OVERLAY + """\", 1);
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        const char *value;
        setenv("DYLD_INSERT_LIBRARIES", "/stale.dylib", 1);
        scope_steam_overlay(cases[i].program, cases[i].image);
        value = getenv("DYLD_INSERT_LIBRARIES");
        if (cases[i].overlay ? !value || strcmp(value, \"""" + OVERLAY + """\") : value != NULL)
        {
            fprintf(stderr, "case %zu: %s / %s -> %s\\n", i, cases[i].program, cases[i].image, value ? value : "<unset>");
            failed++;
        }
    }

    setenv("DYLD_INSERT_LIBRARIES", \"""" + OVERLAY + """\", 1);
    if (build_path_and_exec(&pid, "/runtime/bin", "wineserver", argv) || has_overlay(spawned_env))
    {
        fprintf(stderr, "server received the overlay\\n");
        failed++;
    }
    if (!has_overlay(environ)) { fprintf(stderr, "the caller lost its own environment\\n"); failed++; }
    return !!failed;
}
"""


def run_fixture(parts, directory, name):
    source, binary = directory / (name + ".c"), directory / name
    source.write_text(fixture_source(*parts))
    subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-O1", str(source), "-o", str(binary)],
                   check=True, timeout=20)
    return subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wine_source", type=Path)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    source = (args.wine_source / "dlls/ntdll/unix/loader.c").read_text()
    parts = check_source(source)
    if args.self_test:
        for broken in (
            source.replace("    scope_steam_overlay( argv[2], image_path );\n", ""),
            source.replace('if (strncmp( environ[count], "DYLD_INSERT_LIBRARIES=", 22 )) env[kept++] = environ[count];',
                           "env[kept++] = environ[count];"),
            source.replace('&& !strstr( lower, "/drive_c/windows/" )', ""),
        ):
            if broken == source:
                raise ValueError("a self-test mutation did not change the source")
            try:
                check_source(broken)
            except ValueError:
                continue
            raise ValueError("source check accepted an invalid overlay scope mutation")
    if args.source_only:
        print("Wine overlay scope source contract passed; fixture execution was not requested")
        return 0
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("fixture compilation/execution requires GitHub Actions on Linux; use --source-only locally")
    with tempfile.TemporaryDirectory(prefix="gluon-overlay-scope-") as temp:
        directory = Path(temp)
        result = run_fixture(parts, directory, "overlay-scope")
        if result.returncode:
            raise ValueError("overlay scope fixture failed:\n" + result.stderr)
        if args.self_test:
            game, scope, spawn = parts
            everywhere = game.replace("    return game;\n", "    (void)game;\n    return TRUE;\n")
            leaky = spawn.replace("if (strncmp( environ[count], \"DYLD_INSERT_LIBRARIES=\", 22 )) env[kept++]",
                                  "env[kept++]")
            for name, mutation in (("everywhere", (everywhere, scope, spawn)), ("leaky", (game, scope, leaky))):
                if mutation == parts:
                    raise ValueError("a fixture mutation did not change the functions")
                result = run_fixture(mutation, directory, "overlay-scope-" + name)
                if result.returncode != 1:
                    raise ValueError("overlay scope fixture accepted the %s mutation" % name)
    print("Wine overlay scope passed %d program cases and the server environment%s" % (
        len(CASES), "; overlay everywhere and a leaking server rejected" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("overlay scope check: %s" % error, file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Check Steam overlay scoping using the exact patched Wine functions.

--source-only is safe for local review. Compiling the extracted functions with
stubbed process-start calls requires a GitHub Actions Linux host; no Wine module
is linked and nothing is executed besides the fixture.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


OVERLAY = "/Steam/steamloader.dylib:/Steam/gameoverlayrenderer.dylib"
CHROMIUM_SCOPE = "in-process-gpu-v1"
# (argv[2] program, image path, remaining argv, launcher marker, overlay expected)
GAME = r"/Users/me/Steam/steamapps/common/Game/Game.exe"
LOADER = r"/runtime/lib/wine/x86_64-unix/wine"
GAME_IMAGE = r"Z:\Users\me\Steam\steamapps\common\Game\Game.exe"
CASES = (
    (GAME, GAME_IMAGE, (), None, True),
    (r"c:\windows\system32\steam.exe", r"/bundle/Game.app/Contents/MacOS/wine", (), None, False),
    (r'"Game.exe"', r"Z:\Users\me\Steam\steamapps\common\Game\Game.exe", (), None, True),
    (r"services.exe", r"C:\windows\system32\services.exe", (), None, False),
    (r"services.exe", "C:/Windows/system32/services.exe", (), None, False),
    (r"services.exe", r"\\?\C:\Windows\system32\services.exe", (), None, False),
    (r"winedevice.exe", r"C:\WINDOWS\System32\winedevice.exe", (), None, False),
    (r"Launcher.exe", r"C:\Program Files\Game\Launcher.exe", (), None, True),
    (r"x.exe", r"\??\C:\windows\syswow64\x.exe", (), None, False),
    (r"wineboot", LOADER, (), None, False),
    (r"/prefix/drive_c/windows/system32/explorer.exe", r"/runtime/wine", (), None, False),
    # Proven Chromium background children do not present game frames.
    (GAME, GAME_IMAGE, ("--type=renderer",), CHROMIUM_SCOPE, False),
    (GAME, GAME_IMAGE, ("--type=utility", "--utility-sub-type=network.mojom.NetworkService"), CHROMIUM_SCOPE, False),
    (GAME, GAME_IMAGE, ("--type=crashpad-handler",), CHROMIUM_SCOPE, False),
    # Browser/GPU, unfamiliar roles and ambiguous command lines keep injection.
    (GAME, GAME_IMAGE, (), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--in-process-gpu",), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=gpu-process",), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=zygote",), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=renderer-worker",), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=",), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=Renderer",), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type", "renderer"), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=renderer", "--type=gpu-process"), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=gpu-process", "--type=renderer"), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=renderer", "--type=renderer"), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--", "--type=renderer"), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("--type=renderer", "--", "--type=gpu-process"), CHROMIUM_SCOPE, False),
    (GAME, GAME_IMAGE, ("--user-data-dir=C:\\data\\--type=renderer",), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ("ordinary --type=renderer argument",), CHROMIUM_SCOPE, True),
    (GAME, GAME_IMAGE, ('"--type=renderer"',), CHROMIUM_SCOPE, True),
    (r"/games/--type=renderer/game.exe", LOADER, (), CHROMIUM_SCOPE, True),
    # A role token alone cannot opt another game into this optimization.
    (GAME, GAME_IMAGE, ("--type=renderer",), None, True),
    (GAME, GAME_IMAGE, ("--type=utility",), "", True),
    (GAME, GAME_IMAGE, ("--type=renderer",), "0", True),
    (GAME, GAME_IMAGE, ("--type=renderer",), "in-process-gpu-v10", True),
    (r"wineboot", LOADER, ("--type=gpu-process",), CHROMIUM_SCOPE, False),
    (r"services.exe", r"C:\windows\system32\services.exe", ("--type=renderer",), CHROMIUM_SCOPE, False),
)
# All normal cases bind to the launcher's resource-identified executable.
CASES = tuple((program, image, arguments, marker, "game", expected)
              for program, image, arguments, marker, expected in CASES) + (
    (GAME, GAME_IMAGE, ("--type=renderer",), CHROMIUM_SCOPE, None, True),
    (GAME, GAME_IMAGE, ("--type=utility",), CHROMIUM_SCOPE, "missing", True),
    (GAME, GAME_IMAGE, ("--type=utility",), CHROMIUM_SCOPE, "relative", True),
    (GAME, GAME_IMAGE, ("--type=utility",), CHROMIUM_SCOPE, "directory", True),
    # argv cannot stand in for a different or unresolved actual image.
    (GAME, r"Z:\Users\me\Steam\steamapps\common\Game\Other.exe", ("--type=utility",), CHROMIUM_SCOPE, "game", True),
    (GAME, r"Z:\Users\me\Steam\steamapps\common\Other\Game.exe", ("--type=utility",), CHROMIUM_SCOPE, "game", True),
    (GAME, r"Z:\Users\me\Steam\steamapps\common\Game\Missing.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, GAME_IMAGE, ("--type=renderer",), CHROMIUM_SCOPE, "other", True),
    (GAME, LOADER, ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, "Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, "Z:Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, "", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, "Z", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, "Z:", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, "\\??\\", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, "\\\\?\\", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, r"\??\Z:", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, r"\??\Z:Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    ("wineboot", "Z:Game.exe", ("--type=utility",), CHROMIUM_SCOPE, "game", False),
    (r"C:\Windows\system32\services.exe", "Z:Game.exe", ("--type=utility",), CHROMIUM_SCOPE, "game", False),
    (GAME, None, ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, r"\\server\share\Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, r"\??\UNC\server\share\Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", True),
    (GAME, "Z:/Users/me/Steam/steamapps/common/Game/Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", False),
    (GAME, "z:/Users/me/Steam/steamapps/common/Game/Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", False),
    (GAME, r"\??\Z:\Users\me\Steam\steamapps\common\Game\Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", False),
    (GAME, r"\\?\Z:\Users\me\Steam\steamapps\common\Game\Game.exe", ("--type=renderer",), CHROMIUM_SCOPE, "game", False),
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
    identity = extract(source, "static BOOL is_chromium_game_image( const char *image_path )")
    background = extract(source, "static BOOL is_chromium_background_process( char **argv, const char *image_path )")
    scope = extract(source, "static void scope_steam_overlay( char **argv, const char *image_path )")
    spawn = extract(source, "static int build_path_and_exec( pid_t *pid, const char *dir, const char *name, char **argv )")
    preloader = extract(source, "static void preloader_exec( char **argv, const char *image_path )")
    required = (
        (game, 'image[1] == \':\' && (image[2] == \'\\\\\' || image[2] == \'/\')) path = image;'),
        (game, 'game = !strstr( lower, ":/windows/" ) && !strstr( lower, "/drive_c/windows/" );'),
        (identity, 'expected.st_dev == actual.st_dev && expected.st_ino == actual.st_ino;'),
        (background, 'if (!marker || strcmp( marker, "in-process-gpu-v1" )) return FALSE;'),
        (background, '&& is_chromium_game_image( image_path );'),
        (background, 'for (i = 3; argv[i]; i++)'),
        (background, 'if (!strcmp( argv[i], "--" )) break;'),
        (background, 'if (!strcmp( argv[i], "--type" )) return FALSE;'),
        (background, 'if (role) return FALSE;'),
        (scope, 'if (*overlay && is_game_image( argv[2], image_path ) && !is_chromium_background_process( argv, image_path )) '
                'setenv( "DYLD_INSERT_LIBRARIES", overlay, 1 ); else unsetenv( "DYLD_INSERT_LIBRARIES" );'),
        (spawn, 'if (strncmp( environ[count], "DYLD_INSERT_LIBRARIES=", 22 )) env[kept++] = environ[count];'),
        (spawn, "ret = posix_spawn( pid, argv[0], NULL, NULL, argv, env );"),
    )
    for text, fragment in required:
        if normalized(fragment) not in normalized(text):
            raise ValueError("overlay scope patch differs from the checked contract: " + fragment[:60])
    if not normalized(preloader).startswith(normalized("static void preloader_exec( char **argv, const char *image_path )"
                                                        "{ scope_steam_overlay( argv, image_path );")):
        raise ValueError("every program start must scope the overlay first")
    return game, identity, background, scope, spawn


def fixture_source(game, identity, background, scope, spawn):
    def literal(value):
        if value is None:
            return "NULL"
        return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')
    cases = ",\n".join("    {%s, %s, {%s}, %s, %s, %d}" % (
        literal(program), literal(image), ", ".join(map(literal, (*arguments, None))), literal(marker), literal(binding), expected)
        for program, image, arguments, marker, binding, expected in CASES)
    return """#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/stat.h>
typedef int BOOL;
#define TRUE 1
#define FALSE 0
extern char **environ;
static char **spawned_env;
static const char *config_dir;
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
""" + game + "\n\n" + identity + "\n\n" + background + "\n\n" + scope + "\n\n" + spawn + """

static int has_overlay(char **env)
{
    for (; *env; env++) if (!strncmp(*env, "DYLD_INSERT_LIBRARIES=", 22)) return 1;
    return 0;
}

static void free_spawned_env(void)
{
    size_t i;
    for (i = 0; spawned_env[i]; i++) free(spawned_env[i]);
    free(spawned_env);
}

int main(void)
{
    static const struct { const char *program, *image, *args[4], *marker, *binding; int overlay; } cases[] = {
""" + cases + """
    };
    size_t i, j;
    unsigned failed = 0;
    char *argv[8] = {NULL, NULL, NULL};
    pid_t pid;
    char *binding = NULL;

    config_dir = getenv("GLUON_OVERLAY_FIXTURE_PREFIX");
    if (!config_dir) return 2;
    argv[2] = (char *)cases[1].program;
    unsetenv("GLUON_OVERLAY_INSERT");
    setenv("DYLD_INSERT_LIBRARIES", "/unrelated.dylib", 1);
    scope_steam_overlay(argv, cases[1].image);
    if (!getenv("DYLD_INSERT_LIBRARIES")) { fprintf(stderr, "unset without Gluon's overlay variable\\n"); failed++; }
    if (build_path_and_exec(&pid, "/runtime/bin", "wineserver", argv) || !has_overlay(spawned_env))
    {
        fprintf(stderr, "server environment changed without Gluon's overlay variable\\n");
        failed++;
    }
    free_spawned_env();

    setenv("GLUON_OVERLAY_INSERT", \"""" + OVERLAY + """\", 1);
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        const char *value;
        argv[2] = (char *)cases[i].program;
        for (j = 0; j < sizeof(cases[i].args) / sizeof(cases[i].args[0]); j++)
            argv[j + 3] = (char *)cases[i].args[j];
        if (cases[i].marker) setenv("GLUON_CHROMIUM_OVERLAY_SCOPE", cases[i].marker, 1);
        else unsetenv("GLUON_CHROMIUM_OVERLAY_SCOPE");
        if (!cases[i].binding) unsetenv("GLUON_CHROMIUM_OVERLAY_IMAGE");
        else
        {
            const char *name = !strcmp(cases[i].binding, "game") ? "Game.exe" :
                               !strcmp(cases[i].binding, "other") ? "Other.exe" :
                               !strcmp(cases[i].binding, "directory") ? "." : "Missing.exe";
            if (!strcmp(cases[i].binding, "relative")) binding = strdup("Game.exe");
            else if (asprintf(&binding, "%s/dosdevices/z:/Users/me/Steam/steamapps/common/Game/%s", config_dir, name) < 0) return 2;
            setenv("GLUON_CHROMIUM_OVERLAY_IMAGE", binding, 1);
            free(binding);
        }
        setenv("DYLD_INSERT_LIBRARIES", "/stale.dylib", 1);
        scope_steam_overlay(argv, cases[i].image);
        value = getenv("DYLD_INSERT_LIBRARIES");
        if (cases[i].overlay ? !value || strcmp(value, \"""" + OVERLAY + """\") : value != NULL)
        {
            fprintf(stderr, "case %zu: %s / %s -> %s\\n", i, cases[i].program,
                    cases[i].image ? cases[i].image : "<none>", value ? value : "<unset>");
            failed++;
        }
    }

    setenv("DYLD_INSERT_LIBRARIES", \"""" + OVERLAY + """\", 1);
    if (build_path_and_exec(&pid, "/runtime/bin", "wineserver", argv) || has_overlay(spawned_env))
    {
        fprintf(stderr, "server received the overlay\\n");
        failed++;
    }
    free_spawned_env();
    if (!has_overlay(environ)) { fprintf(stderr, "the caller lost its own environment\\n"); failed++; }

    setenv("GLUON_OVERLAY_INSERT", "", 1);
    argv[2] = (char *)cases[0].program;
    argv[3] = NULL;
    scope_steam_overlay(argv, cases[0].image);
    if (getenv("DYLD_INSERT_LIBRARIES")) { fprintf(stderr, "empty overlay variable did not clear injection\\n"); failed++; }
    return !!failed;
}
"""


def run_fixture(parts, directory, name):
    source, binary = directory / (name + ".c"), directory / name
    source.write_text(fixture_source(*parts))
    prefix, files = directory / "pfx", directory / "files"
    drive = prefix / "dosdevices/z:"
    if not drive.exists():
        drive.parent.mkdir(parents=True)
        game_dir = files / "Users/me/Steam/steamapps/common/Game"
        game_dir.mkdir(parents=True)
        (game_dir / "Game.exe").write_bytes(b"confirmed Chromium executable")
        (game_dir / "Other.exe").write_bytes(b"unrelated child executable")
        (game_dir.parent / "Other").mkdir()
        (game_dir.parent / "Other/Game.exe").write_bytes(b"same basename, unrelated executable")
        drive.symlink_to(files, target_is_directory=True)
    subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-O1", str(source), "-o", str(binary)],
                   check=True, timeout=20)
    return subprocess.run([str(binary)], env=dict(os.environ, GLUON_OVERLAY_FIXTURE_PREFIX=str(prefix)),
                          capture_output=True, text=True, timeout=5)


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
            source.replace("    scope_steam_overlay( argv, image_path );\n", ""),
            source.replace('if (strncmp( environ[count], "DYLD_INSERT_LIBRARIES=", 22 )) env[kept++] = environ[count];',
                           "env[kept++] = environ[count];"),
            source.replace('&& !strstr( lower, "/drive_c/windows/" )', ""),
            source.replace('if (!marker || strcmp( marker, "in-process-gpu-v1" )) return FALSE;', "if (!marker) return FALSE;"),
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
            game, identity, background, scope, spawn = parts
            everywhere = game.replace("    return game;\n", "    (void)game;\n    return TRUE;\n")
            leaky = spawn.replace('if (strncmp( environ[count], "DYLD_INSERT_LIBRARIES=", 22 )) env[kept++]', "env[kept++]")
            no_child_filter = scope.replace(" && !is_chromium_background_process( argv, image_path )", "")
            # Retain a function reference so -Werror does not reject this negative
            # before its behavior can be checked by the executable fixture.
            no_child_filter = no_child_filter.replace("    if (!overlay) return;", "    (void)is_chromium_background_process;\n    if (!overlay) return;")
            unsafe_marker = background.replace('!marker || strcmp( marker, "in-process-gpu-v1" )', '!marker')
            substring_role = background.replace('!strcmp( role, "renderer" )', 'strstr( role, "renderer" )')
            duplicate_role = background.replace('        if (role) return FALSE;\n', '')
            positional_role = background.replace('        if (!strcmp( argv[i], "--" )) break;\n', '')
            wrong_image = identity.replace("    return same;", "    (void)same;\n    return TRUE;")
            relative_image = game.replace(r" && (image[2] == '\\' || image[2] == '/')", "")
            mutations = (
                ("everywhere", (everywhere, identity, background, scope, spawn)),
                ("server", (game, identity, background, scope, leaky)),
                ("child-filter", (game, identity, background, no_child_filter, spawn)),
                ("marker", (game, identity, unsafe_marker, scope, spawn)),
                ("substring-role", (game, identity, substring_role, scope, spawn)),
                ("duplicate-role", (game, identity, duplicate_role, scope, spawn)),
                ("positional-role", (game, identity, positional_role, scope, spawn)),
                ("image-identity", (game, wrong_image, background, scope, spawn)),
                ("relative-image", (relative_image, identity, background, scope, spawn)),
            )
            for name, mutation in mutations:
                if mutation == parts:
                    raise ValueError("a fixture mutation did not change the functions")
                result = run_fixture(mutation, directory, "overlay-scope-" + name)
                if result.returncode != 1:
                    raise ValueError("overlay scope fixture accepted the %s mutation" % name)
    print("Wine overlay scope passed %d program cases and the server environment%s" % (
        len(CASES), "; nine unsafe scope mutations rejected" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("overlay scope check: %s" % error, file=sys.stderr)
        sys.exit(1)

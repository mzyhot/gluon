#!/usr/bin/env python3
"""Check that Wine keeps a loader started from an app bundle inside that bundle.

Gluon starts each game through its own app bundle. Wine normally re-executes
every program through a temporary per-program link, which would leave the
bundle; the patch keeps the bundle's loader instead. --source-only is safe for
local review. Compiling the exact helper against a stubbed executable-path
lookup requires a GitHub Actions Linux host; no Wine module is linked.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


# (executable path reported by the system or None for a lookup error, kept)
CASES = (
    ("/Users/me/Library/Application Support/Gluon/apps.noindex/1/Game.app/Contents/MacOS/wine", True),
    ("/Applications/Some Game.app/Contents/MacOS/wine", True),
    ("/tmp/winetemp-1-2-3-4/Game", False),
    ("/runtime/lib/wine/x86_64-unix/wine", False),
    ("/Games/Game.app/Contents/Resources/wine", False),
    ("/Games/Game.apps/Contents/MacOS/wine", False),
    ("/Games/Game.app/contents/macos/wine", False),
    (None, False),
)

REQUIRED = (
    "# include <mach-o/getsect.h>\n# include <mach-o/dyld.h>\n",
    "main_argc = argc; main_argv = argv; #ifdef __APPLE__ enter_game_directory(); #endif init_paths();",
    "char *bundled = bundled_wineloader(); if (bundled) { free( argv[1] ); argv[1] = bundled; } "
    "else if (getenv(\"WINEDLLPATH\")) replace_wineloader_path_with_link( &(argv[1]), image_path ); }",
)


MACDRV_REQUIRED = (
    "if (attempted || (focus && !strcmp(focus, \"0\"))) return;",
    "if (![[bundleURL pathExtension] isEqualToString:@\"app\"]) return; attempted = YES;",
    # Never activate an instance without a shown window; stop once a window is
    # active on the current Space, so a Big Picture Space is covered too.
    "if (![self gluonWindowShown:&onActiveSpace]) { [self bringBundleForward:bundleURL attempt:attempt + 1]; return; }",
    "if ([NSApp isActive] && onActiveSpace) return;",
    "if (attempt >= sizeof(delays) / sizeof(delays[0])) return;",
    "configuration.activates = YES; configuration.createsNewApplicationInstance = NO;",
    "if (![NSApp isActive]) [self tryToActivateIgnoringOtherApps:YES]; [self bringBundleForward:bundleURL attempt:attempt + 1];",
    "[self bringBundleForwardOnce]; if ([NSApp isActive]) return;",
    # Quitting the game's app ends the whole session through Gluon's host, which
    # must be verified by path so a reused PID is never signalled.
    "if (proc_pidpath((pid_t)pid, path, sizeof(path)) <= 0) return FALSE; name = strrchr(path, '/'); "
    "if (!name || strcmp(name + 1, \"gluon-launcher-host\")) return FALSE; return !kill((pid_t)pid, SIGTERM);",
    "if (quitReason != kAELogOut && quitReason != kAEReallyLogOut && quitReason != kAEShowRestartDialog && "
    "quitReason != kAEShowShutdownDialog && [self stopGluonSession]) return NSTerminateCancel;",
)


def check_macdrv(source):
    """The activation fallback starts once, only from an app bundle, is bounded,
    never starts another instance, and honors GLUON_FOCUS=0."""
    flat = normalized(source)
    for fragment in MACDRV_REQUIRED:
        if normalized(fragment) not in flat:
            raise ValueError("macdrv bundle activation differs from the checked contract: " + fragment[:60])
    if 'getenv("GLUON_FOCUS")' not in source:
        raise ValueError("bundle activation must honor GLUON_FOCUS")


def normalized(value):
    return re.sub(r"\s+", "", value)


def extract_helper(source):
    matches = re.findall(r"^static char \*bundled_wineloader\(void\)\n\{.*?^\}", source,
                         re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError("expected exactly one bundled_wineloader helper")
    return matches[0]


def extract_cwd_helper(source):
    matches = re.findall(r"^static void enter_game_directory\(void\)\n\{.*?^\}", source, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError("expected exactly one enter_game_directory helper")
    if 'unsetenv( "GLUON_GAME_CWD" );' not in matches[0]:
        raise ValueError("the game directory must be used by the first process only")
    return matches[0]


def check_source(source):
    flat = normalized(source)
    for fragment in REQUIRED:
        if normalized(fragment) not in flat:
            raise ValueError("app-bundle loader patch differs from the checked contract: " + fragment[:60])
    helper = extract_helper(source)
    if 'strstr( path, ".app/Contents/MacOS/" )' not in helper or "_NSGetExecutablePath( path, &size )" not in helper:
        raise ValueError("bundled loader detection changed")
    # The macOS branch without a preloader is the one Gluon's runtime builds.
    block = re.search(r"#if defined\(__APPLE__\) && !defined\(HAVE_WINE_PRELOADER\)(.*?)#endif", source, re.DOTALL)
    if not block or "bundled_wineloader()" not in block.group(1):
        raise ValueError("the bundled loader must be used in the macOS loader branch")
    return helper + "\n\n" + extract_cwd_helper(source)


def fixture_source(helper):
    cases = ",\n".join("    {%s, %d}" % ("NULL" if path is None else '"%s"' % path, kept) for path, kept in CASES)
    return """#define _GNU_SOURCE
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static const char *fixture_path;
static int _NSGetExecutablePath(char *buffer, uint32_t *size)
{
    if (!fixture_path || strlen(fixture_path) + 1 > *size) return -1;
    strcpy(buffer, fixture_path);
    return 0;
}
""" + helper + """

int main(void)
{
    static const struct { const char *path; int kept; } cases[] = {
""" + cases + """
    };
    size_t i;
    unsigned failed = 0;
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        char *result;
        fixture_path = cases[i].path;
        result = bundled_wineloader();
        if (!!result != cases[i].kept || (result && strcmp(result, cases[i].path)))
        {
            fprintf(stderr, "case %zu: %s -> %s\\n", i, cases[i].path ? cases[i].path : "<error>",
                    result ? result : "<null>");
            failed++;
        }
        free(result);
    }
    {
        char here[PATH_MAX], cwd[PATH_MAX];
        if (!getcwd(here, sizeof(here))) return 2;
        setenv("GLUON_GAME_CWD", "/", 1);
        enter_game_directory();
        if (!getcwd(cwd, sizeof(cwd)) || strcmp(cwd, "/") || getenv("GLUON_GAME_CWD"))
        {
            fprintf(stderr, "game directory not entered once: %s\\n", cwd);
            failed++;
        }
        setenv("GLUON_GAME_CWD", "/no/such/game/directory", 1);
        enter_game_directory();
        if (!getcwd(cwd, sizeof(cwd)) || strcmp(cwd, "/") || getenv("GLUON_GAME_CWD"))
        {
            fprintf(stderr, "missing game directory changed state\\n");
            failed++;
        }
        if (chdir(here)) return 2;
    }
    return !!failed;
}
"""


def run_fixture(helper, directory, name):
    source, binary = directory / (name + ".c"), directory / name
    source.write_text(fixture_source(helper))
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
    macdrv = (args.wine_source / "dlls/winemac.drv/cocoa_app.m").read_text()
    helper = check_source(source)
    check_macdrv(macdrv)
    if args.self_test:
        for broken in (
            source.replace("        if (bundled)\n", "        if (0)\n"),
            source.replace('".app/Contents/MacOS/"', '".app/"'),
            source.replace("# include <mach-o/dyld.h>\n", ""),
            source.replace("    enter_game_directory();\n", ""),
            source.replace('    unsetenv( "GLUON_GAME_CWD" );\n', ""),
        ):
            if broken == source:
                raise ValueError("a self-test mutation did not change the source")
            try:
                check_source(broken)
            except ValueError:
                continue
            raise ValueError("source check accepted an invalid app-bundle loader mutation")
        for broken in (
            macdrv.replace("configuration.createsNewApplicationInstance = NO;", ""),
            macdrv.replace('if (attempted || (focus && !strcmp(focus, "0"))) return;', "if (attempted) return;"),
            macdrv.replace("        [self bringBundleForwardOnce];\n", ""),
            macdrv.replace("            if ([NSApp isActive] && onActiveSpace) return;\n",
                           "            if ([NSApp isActive]) return;\n"),
            macdrv.replace("        if (attempt >= sizeof(delays) / sizeof(delays[0])) return;\n", ""),
            macdrv.replace("        if (!name || strcmp(name + 1, \"gluon-launcher-host\")) return FALSE;\n", ""),
            macdrv.replace("quitReason != kAEShowShutdownDialog &&", ""),
        ):
            if broken == macdrv:
                raise ValueError("a macdrv self-test mutation did not change the source")
            try:
                check_macdrv(broken)
            except ValueError:
                continue
            raise ValueError("source check accepted an invalid bundle activation mutation")
    if args.source_only:
        print("Wine app-bundle loader and activation source contracts passed; helper execution was not requested")
        return 0
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("helper compilation/execution requires GitHub Actions on Linux; use --source-only locally")
    with tempfile.TemporaryDirectory(prefix="gluon-app-bundle-") as temp:
        directory = Path(temp)
        result = run_fixture(helper, directory, "app-bundle")
        if result.returncode:
            raise ValueError("app-bundle loader fixture failed:\n" + result.stderr)
        if args.self_test:
            # Any executable path would let a program leave the bundle check.
            loose = helper.replace('!strstr( path, ".app/Contents/MacOS/" )', '!strstr( path, "/" )')
            result = run_fixture(loose, directory, "app-bundle-loose")
            if result.returncode != 1 or "winetemp" not in result.stderr:
                raise ValueError("app-bundle loader fixture accepted paths outside a bundle")
    print("Wine app-bundle loader passed %d path cases%s" % (
        len(CASES), "; paths outside a bundle rejected" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("app-bundle loader check: %s" % error, file=sys.stderr)
        sys.exit(1)

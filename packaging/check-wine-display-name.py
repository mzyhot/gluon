#!/usr/bin/env python3
"""Check the actual patched Wine display-name helper without starting Wine.

--source-only is safe for local review. Compilation and execution of this isolated
string helper require a GitHub Actions Linux host; no Wine module is linked.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


SUFFIX_BLOCK = """    exe_name_len = strlen(exe_name);
    if (exe_name_len > 4 && exe_name[exe_name_len - 4] == '.' &&
        (exe_name[exe_name_len - 3] == 'e' || exe_name[exe_name_len - 3] == 'E') &&
        (exe_name[exe_name_len - 2] == 'x' || exe_name[exe_name_len - 2] == 'X') &&
        (exe_name[exe_name_len - 1] == 'e' || exe_name[exe_name_len - 1] == 'E'))
    {
        exe_name_len -= 4;
        exe_name[exe_name_len] = 0;
    }
"""
CASES = (
    ("Primordialis.exe", "Primordialis"),
    ("Game Name.EXE", "Game Name"),
    ("Game.eXe", "Game"),
    ("Game.ExE", "Game"),
    (".exe", ".exe"),
    (".EXE", ".EXE"),
    ("Game.exe.exe", "Game.exe"),
    ("Game.exe.backup", "Game.exe.backup"),
    ("Game.dll", "Game.dll"),
    ("Version 1.2.exe", "Version 1.2"),
    ("Game", "Game"),
    ("exe", "exe"),
    ("", None),
    ("/Games/Primordialis/Primordialis.exe", "Primordialis"),
    ("C:\\Games\\Game Name\\Game Name.exe", "Game Name"),
    ("C:\\Games/Version 1.2.exe", "Version 1.2"),
    ("/Games/雪のゲーム.exe", "雪のゲーム"),
    ("C:\\Games\\Café Game.EXE", "Café Game"),
    ("0123456789abcdefABCDEF0123456789.Game.exe", "Game"),
    ("/tmp/0123456789abcdefABCDEF0123456789.Game Name.EXE", "Game Name"),
    ("0123456789abcdefABCDEF012345678.Game.exe",
     "0123456789abcdefABCDEF012345678.Game"),
    ("0123456789abcdefABCDEF0123456789a.Game.exe",
     "0123456789abcdefABCDEF0123456789a.Game"),
    ("Game..exe", "Game."),
    ("/Games/", None),
)


def extract_helper(source):
    matches = re.findall(
        r"^static char \*extract_exe_name\(const char \*exe_path\)\n\{.*?^\}",
        source, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError("expected exactly one extract_exe_name helper")
    return matches[0]


def check_source(helper):
    # These checks allow local source review while the cloud fixture below checks
    # behavior by compiling this exact function, not a second implementation.
    normalized = lambda value: re.sub(r"\s+", "", value)
    required = (
        "exe_path_copy = strdup(exe_path);",
        "if ((p = strrchr(exe_name, '\\\\'))) exe_name = p + 1;",
        "if ((p = strrchr(exe_name, '/'))) exe_name = p + 1;",
        'if (strspn(exe_name, "0123456789abcdefABCDEF") == 32 && '
        "exe_name[32] == '.') exe_name += 33;",
        SUFFIX_BLOCK,
        "if (exe_name_len) ret = strdup(exe_name); else ret = NULL;",
        "free(exe_path_copy); return ret;",
    )
    for fragment in required:
        if normalized(fragment) not in normalized(helper):
            raise ValueError("Wine display-name helper differs from the checked contract")


def fixture_source(helper):
    cases = ",\n".join(
        "    {%s, %s}" % (json.dumps(path, ensure_ascii=False),
                           "NULL" if expected is None else json.dumps(expected, ensure_ascii=False))
        for path, expected in CASES)
    return """#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
""" + helper + """

int main(void)
{
    static const struct { const char *path, *expected; } cases[] = {
""" + cases + """
    };
    size_t i;
    unsigned failed = 0;
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        char *input = strdup(cases[i].path);
        char *result;
        if (!input) return 2;
        result = extract_exe_name(input);
        if (strcmp(input, cases[i].path) ||
            (!!result != !!cases[i].expected) ||
            (result && strcmp(result, cases[i].expected)))
        {
            fprintf(stderr, "case %zu: %s -> %s (expected %s); input unchanged: %d\\n",
                    i, cases[i].path, result ? result : "<null>",
                    cases[i].expected ? cases[i].expected : "<null>",
                    !strcmp(input, cases[i].path));
            failed++;
        }
        free(result);
        free(input);
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
    source = (args.wine_source / "dlls/ntdll/unix/loader.c").read_text()
    helper = extract_helper(source)
    check_source(helper)
    if args.self_test:
        for broken in (
            helper.replace("exe_name[exe_name_len] = 0;", ";"),
            helper.replace("exe_name_len > 4", "exe_name_len >= 4"),
            helper.replace("exe_name_len - 3] == 'E'", "exe_name_len - 3] == 'e'"),
        ):
            try:
                check_source(broken)
            except ValueError:
                continue
            raise ValueError("source check accepted an invalid display-name mutation")
    if args.source_only:
        print("Wine display-name source contract passed; helper execution was not requested")
        return 0
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("helper compilation/execution requires GitHub Actions on Linux; use --source-only locally")
    with tempfile.TemporaryDirectory(prefix="gluon-display-name-") as temp:
        directory = Path(temp)
        result = run_fixture(helper, directory, "display-name")
        if result.returncode:
            raise ValueError("display-name fixture failed:\n" + result.stderr)
        if args.self_test:
            # Reproduce the original bug: calculate a shorter length but return
            # the full copy. The same behavioral fixture must reject it.
            regression = helper.replace("exe_name[exe_name_len] = 0;", ";")
            result = run_fixture(regression, directory, "display-name-regression")
            if result.returncode != 1 or "Primordialis.exe" not in result.stderr:
                raise ValueError("display-name fixture did not reject the original suffix regression")
    print("Wine display-name helper passed %d cases%s" % (
        len(CASES), "; original suffix regression rejected" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("display-name check: %s" % error, file=sys.stderr)
        sys.exit(1)

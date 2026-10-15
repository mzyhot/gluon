#!/usr/bin/env python3
"""Check Wine's patched per-program loader copies without starting Wine.

The patch gives each program a loader copy whose embedded Info.plist already
names it, so libraries inserted before main() cannot capture the default name.
--source-only is safe for local review. Compiling and running the exact patched
functions on fake loader files requires a GitHub Actions Linux host.
"""
import argparse
import os
from pathlib import Path
import plistlib
import re
import struct
import subprocess
import sys
import tempfile


PREFIX = '<key>CFBundleName</key>\\n    <string>'
SUFFIX = '</string>'
PADDING = '<!-- bundle name padding -->'
DEFAULT = "CrossOver-Hosted Application"
PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.codeweavers.CrossOver.wineloader</string><!-- CrossOver Hack 10913 -->
    <key>CFBundleName</key>
    <string>CrossOver-Hosted Application</string>{after}
    <key>NSPrincipalClass</key>
    <string>WineApplication</string>
</dict>
</plist>
"""
TAIL = b"\0__wine_main tail\0"


def macho(*commands):
    """A thin x86_64 Mach-O header with the given (cmd, payload) load commands."""
    body = b"".join(struct.pack("<II", cmd, 8 + len(payload)) + payload for cmd, payload in commands)
    return struct.pack("<IiiIIIII", 0xfeedfacf, 0x01000007, 3, 2, len(commands), len(body), 0, 0) + body


UUID = (0x1b, bytes(16))
HEADS = {
    "plain": macho(UUID), "padded": macho(UUID), "nomarker": macho(UUID),
    "signed": macho(UUID, (0x1d, struct.pack("<II", 0, 0))),
    "fat": struct.pack(">II", 0xcafebabe, 1) + bytes(24),
}
LIMITS = {"plain": len(DEFAULT), "padded": len(DEFAULT) + len(PADDING)}
# (fixture file, loader, name); names longer than the template are truncated
# exactly as apple_override_bundle_name truncates them in memory.
WRITES = (
    ("short", "plain", "Happy Wheels"),
    ("exact", "plain", "N" * len(DEFAULT)),
    ("long", "plain", "People Playground Deluxe Edition 2026"),
    ("padded", "padded", "A Very Long Game Name That Uses The Padding Space"),
    ("unicode", "plain", "Café Game"),
    ("nomarker", "nomarker", "No Marker"),
    ("empty", "plain", ""),
    ("signed", "signed", "Signed Game"),
    ("fat", "fat", "Universal Game"),
)
LINKS = (("plain", "Fresh Game"), ("plain", "Old Link"), ("plain", "Kept Copy"),
         ("nomarker", "No Marker Link"), ("signed", "Signed Link"))


def expected_name(kind, game):
    if kind not in LIMITS or not game:
        return None
    return game.encode("utf-8")[:LIMITS[kind]].decode("utf-8")


def extract(source, signature):
    matches = re.findall(r"^" + re.escape(signature) + r"\n\{.*?^\}", source, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError("expected exactly one " + signature)
    return matches[0]


def normalized(value):
    return re.sub(r"\s+", "", value)


def check_template(template):
    """The embedded Info.plist must stay valid, name a game category and keep the
    exact CFBundleName text that both renames search for."""
    if PREFIX.replace("\\n", "\n") + DEFAULT + SUFFIX not in template:
        raise ValueError("loader Info.plist no longer has the CFBundleName text the rename expects")
    try:
        info = plistlib.loads(template.replace("@PACKAGE_VERSION@", "11.0").encode())
    except plistlib.InvalidFileException as error:
        raise ValueError("loader Info.plist template is not a valid plist") from error
    if info.get("LSApplicationCategoryType") != "public.app-category.games":
        raise ValueError("loader Info.plist does not declare the games category for Game Mode")
    if info.get("CFBundleName") != DEFAULT:
        raise ValueError("loader Info.plist default name changed")


def check_source(loader, main):
    allowed = extract(loader, "static BOOL loader_copy_allowed(const char *data, size_t size)")
    writer = extract(loader, "static BOOL write_named_loader(const char *wineloader_path, "
                             "const char *linkpath, const char *exe_name)")
    link = extract(loader, "static char *create_preloader_link(const char *wineloader_path, const char *exe_name)")
    override = extract(main, "static void apple_override_bundle_name( int argc, char *argv[] )")
    for constant in (PREFIX, SUFFIX, PADDING):
        literal = '"%s"' % constant
        if literal not in writer or literal not in override:
            raise ValueError("loader copy and in-memory rename disagree on " + constant)
    required = (
        (allowed, "if (magic != 0xfeedfacf) return FALSE;"),
        (allowed, "if (cmd == 0x1d) return FALSE;"),
        (allowed, "if (cmdsize < 8 || cmdsize > size - pos) return FALSE;"),
        (writer, "if (!data || !size || !name_len || !loader_copy_allowed(data, size)) goto out;"),
        (writer, "if (padding_len <= remaining && !memcmp(end + suffix_len, padding, padding_len)) "
                 "max_len += padding_len;"),
        (writer, "memcpy(name, exe_name, copy_len); memcpy(name + copy_len, suffix, suffix_len); "
                 "memset(name + copy_len + suffix_len, ' ', max_len - copy_len);"),
        (writer, "ret = !close(fd) && !rename(temp, linkpath);"),
        (writer, "if (temp && !ret) unlink(temp);"),
        (link, "if (!stat(wineloader_path, &loader_st) && st.st_dev == loader_st.st_dev && "
               "st.st_ino == loader_st.st_ino) write_named_loader(wineloader_path, linkpath, exe_name);"),
        (link, "if (write_named_loader(wineloader_path, linkpath, exe_name)) return linkpath; "),
        (link, "if (!link(wineloader_path, linkpath) || !symlink(wineloader_path, linkpath)) return linkpath;"),
    )
    for text, fragment in required:
        if normalized(fragment) not in normalized(text):
            raise ValueError("loader-name patch differs from the checked contract: " + fragment[:60])
    if normalized(link).index(normalized("write_named_loader(wineloader_path, linkpath, exe_name))return")) > \
            normalized(link).index(normalized("!link(wineloader_path, linkpath)")):
        raise ValueError("the named copy must be tried before the hard link")
    return allowed, writer, link


def fixture_source(allowed, writer, link):
    return """#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#define MAX_PATH 260
static size_t fixture_strlcat(char *dst, const char *src, size_t size)
{
    size_t length = strlen(dst), extra = strlen(src);
    if (length < size) snprintf(dst + length, size - length, "%s", src);
    return length + extra;
}
#define strlcat fixture_strlcat
static const char *fixture_links;
static char *create_tempdir(const char *wineloader_path)
{
    char *dir = malloc(MAX_PATH);
    (void)wineloader_path;
    if (dir) snprintf(dir, MAX_PATH, "%s/", fixture_links);
    return dir;
}
""" + allowed + "\n\n" + writer + "\n\n" + link + """

int main(int argc, char **argv)
{
    /* argv: dir, then (write|link) loader output-or-name name ... */
    int i;
    if (argc < 2) return 2;
    fixture_links = argv[1];
    for (i = 2; i + 3 < argc; i += 4)
    {
        if (!strcmp(argv[i], "write"))
            printf("%s %d\\n", argv[i + 2], write_named_loader(argv[i + 1], argv[i + 2], argv[i + 3]));
        else
        {
            char *path = create_preloader_link(argv[i + 1], argv[i + 3]);
            printf("%s %s\\n", argv[i + 3], path ? path : "<null>");
            free(path);
        }
    }
    return 0;
}
"""


def embedded_name(path, original):
    data, source = Path(path).read_bytes(), Path(original).read_bytes()
    start, end = data.find(b"<?xml"), data.find(b"</plist>")
    if len(data) != len(source) or start < 0 or end < 0 or \
            data[:start] != source[:start] or data[end:] != source[end:]:
        raise ValueError(f"{path.name}: copy changed bytes outside the embedded plist")
    return plistlib.loads(data[start:end + len(b"</plist>")])["CFBundleName"]


def run_fixture(allowed, writer, link, directory, name):
    source, binary = directory / (name + ".c"), directory / name
    source.write_text(fixture_source(allowed, writer, link))
    subprocess.run(["cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-O1", str(source), "-o", str(binary)],
                   check=True, timeout=20)
    work = directory / (name + "-work")
    links = work / "links"
    links.mkdir(parents=True)
    loaders = {}
    for kind, head in HEADS.items():
        text = PLIST.format(after=PADDING if kind == "padded" else "<!-- CrossOver Hack 10913 -->")
        if kind == "nomarker":
            text = text.replace("<key>CFBundleName</key>", "<key>CFBundleDisplayName</key>")
        loaders[kind] = work / ("loader-" + kind)
        loaders[kind].write_bytes(head + b"loader head\0" + text.encode() + TAIL)
        loaders[kind].chmod(0o755)
    args = [str(binary), str(links)]
    for output, kind, game in WRITES:
        args += ["write", str(loaders[kind]), str(work / output), game]
    os.link(loaders["plain"], links / "Old Link")
    (links / "Kept Copy").write_bytes(b"existing named copy")
    for kind, game in LINKS:
        args += ["link", str(loaders[kind]), "-", game]
    result = subprocess.run(args, capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise ValueError("loader-name fixture crashed:\n" + result.stderr)
    errors = []
    for output, kind, game in WRITES:
        expected = expected_name(kind, game)
        path = work / output
        if expected is None:
            if path.exists():
                errors.append(f"{output}: wrote a copy for an unusable input")
            continue
        try:
            actual = embedded_name(path, loaders[kind])
        except (ValueError, plistlib.InvalidFileException, KeyError) as error:
            errors.append(f"{output}: {error}")
            continue
        if actual != expected:
            errors.append(f"{output}: name {actual!r} (expected {expected!r})")
        if path.stat().st_size != loaders[kind].stat().st_size or path.stat().st_mode & 0o777 != 0o755:
            errors.append(f"{output}: size or mode differs from the loader")
    plain = loaders["plain"].stat()
    for game in ("Fresh Game", "Old Link"):
        path = links / game
        if not path.is_file() or path.stat().st_ino == plain.st_ino:
            errors.append(f"{game}: expected a named copy, found a link to the loader")
        elif embedded_name(path, loaders["plain"]) != game:
            errors.append(f"{game}: copy is not named")
    if (links / "Kept Copy").read_bytes() != b"existing named copy":
        errors.append("Kept Copy: replaced an existing named copy")
    for kind, game in (("nomarker", "No Marker Link"), ("signed", "Signed Link")):
        path = links / game
        if not path.exists() or path.stat().st_ino != loaders[kind].stat().st_ino:
            errors.append(f"{game}: did not fall back to Wine's hard link")
    leftovers = [p.name for p in list(work.iterdir()) + list(links.iterdir())
                 if re.search(r"\.[A-Za-z0-9]{6}$", p.name)]
    if leftovers:
        errors.append("temporary files left behind: " + ", ".join(leftovers))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wine_source", type=Path)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    loader = (args.wine_source / "dlls/ntdll/unix/loader.c").read_text()
    main_c = (args.wine_source / "loader/main.c").read_text()
    template = (args.wine_source / "loader/wine_info.plist.in").read_text()
    check_template(template)
    allowed, writer, link = check_source(loader, main_c)
    if args.self_test:
        for broken in (
            template.replace("public.app-category.games", "public.app-category.utilities"),
            template.replace("<key>CFBundleName</key>\n    <string>", "<key>CFBundleName</key><string>"),
            template.replace("</dict>", "<dict>"),
        ):
            if broken == template:
                raise ValueError("a template self-test mutation did not change the plist")
            try:
                check_template(broken)
            except ValueError:
                continue
            raise ValueError("template check accepted an invalid loader Info.plist")
        for broken in (
            loader.replace("    if (write_named_loader(wineloader_path, linkpath, exe_name))\n        return linkpath;\n", ""),
            loader.replace("            write_named_loader(wineloader_path, linkpath, exe_name);\n",
                           "            (void)0;\n"),
            loader.replace('static const char padding[] = "<!-- bundle name padding -->";\n    const size_t prefix_len',
                           'static const char padding[] = "<!-- padding -->";\n    const size_t prefix_len'),
            loader.replace("if (temp && !ret) unlink(temp);", ""),
            loader.replace("        if (cmd == 0x1d) return FALSE;  /* LC_CODE_SIGNATURE */\n", ""),
        ):
            if broken == loader:
                raise ValueError("a self-test mutation did not change the source")
            try:
                check_source(broken, main_c)
            except ValueError:
                continue
            raise ValueError("source check accepted an invalid loader-name mutation")
    if args.source_only:
        print("Wine loader-name and Info.plist source contracts passed; fixture execution was not requested")
        return 0
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("fixture compilation/execution requires GitHub Actions on Linux; use --source-only locally")
    with tempfile.TemporaryDirectory(prefix="gluon-loader-name-") as temp:
        directory = Path(temp)
        errors = run_fixture(allowed, writer, link, directory, "loader-name")
        if errors:
            raise ValueError("loader-name fixture failed:\n" + "\n".join(errors))
        if args.self_test:
            # The original behavior: always a hard link, never a named copy.
            original = link.replace("    if (write_named_loader(wineloader_path, linkpath, exe_name))\n"
                                    "        return linkpath;\n", "").replace(
                "            write_named_loader(wineloader_path, linkpath, exe_name);\n", "            (void)0;\n")
            errors = run_fixture(allowed, writer, original, directory, "loader-name-original")
            if not any("expected a named copy" in error for error in errors):
                raise ValueError("loader-name fixture did not reject Wine's original hard links")
            # Editing a signed loader would leave macOS a binary it kills.
            unsigned_only = allowed.replace("if (cmd == 0x1d) return FALSE;", "(void)cmd;")
            if unsigned_only == allowed:
                raise ValueError("a fixture mutation did not change the signature check")
            errors = run_fixture(unsigned_only, writer, link, directory, "loader-name-signed")
            if not any(error.startswith(("signed:", "Signed Link:")) for error in errors):
                raise ValueError("loader-name fixture accepted a copy of a signed loader")
    print("Wine loader-name copies passed %d write cases and %d link cases%s" % (
        len(WRITES), len(LINKS),
        "; original hard links and signed-loader copies rejected" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("loader-name check: %s" % error, file=sys.stderr)
        sys.exit(1)

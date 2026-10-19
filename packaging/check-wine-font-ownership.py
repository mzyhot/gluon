#!/usr/bin/env python3
"""Exercise the exact Wine font constructor/destructor and its rejection paths.

Only font metadata parsing is stubbed. The constructor, every rejection, and
all owned-resource cleanup are extracted unchanged from the patched source.
Compilation and ASan/UBSan/LSan execution require GitHub Actions on Linux.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def extract(source):
    patterns = (
        r"^struct unix_face\n\{.*?^\};",
        r"^static struct unix_face \*unix_face_create\(.*?^\}",
        r"^static void unix_face_destroy\( struct unix_face \*This \)\n\{.*?^\}",
    )
    parts = []
    for pattern in patterns:
        matches = re.findall(pattern, source, re.MULTILINE | re.DOTALL)
        if len(matches) != 1:
            raise ValueError("expected exactly one Wine font ownership source section: " + pattern)
        parts.append(matches[0])
    constructor, destructor = parts[1:]
    if "free( This );" in constructor or constructor.count("unix_face_destroy( This );") != 3:
        raise ValueError("every font-constructor rejection must release the whole face")
    declaration = "static void unix_face_destroy( struct unix_face *This );"
    if declaration not in source[:source.index(constructor)]:
        raise ValueError("font destructor must be declared before the constructor")
    for member in ("full_name", "style_name", "second_name", "family_name"):
        if "free( This->%s );" % member not in destructor:
            raise ValueError("font destructor no longer owns " + member)
    if "if (This->ft_face) pFT_Done_Face( This->ft_face );" not in destructor:
        raise ValueError("font destructor must release its FreeType face")
    return parts[0] + "\n" + declaration + "\n" + constructor + "\n" + destructor


PRELUDE = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <wchar.h>
typedef wchar_t WCHAR;
typedef unsigned int UINT, DWORD, LANGID;
typedef int BOOL;
typedef struct { DWORD fsCsb[2], fsUsb[4]; } FONTSIGNATURE;
struct bitmap_font_size { int unused; };
struct ttc_sfnt_v1 { int unused; };
struct tt_name_v0 { int unused; };
struct opentype_name { int tag; };
struct family_names_data {
    LANGID primary_langid;
    struct opentype_name family_name, second_name;
    BOOL primary_seen, english_seen;
};
struct face_name_data { LANGID primary_langid; struct opentype_name face_name; };
struct fake_ft_face { long num_faces; };
typedef struct fake_ft_face *FT_Face;
typedef struct { unsigned int usWeightClass; } TT_OS2;
#define TRUE 1
#define FALSE 0
#define ADDFONT_ALLOW_BITMAP 1
#define NTM_BOLD 1
#define FW_BOLD 700
#define FW_NORMAL 400
#define LANG_ENGLISH 9
#define SUBLANG_DEFAULT 1
#define LANG_NEUTRAL 0
#define SUBLANG_NEUTRAL 0
#define MAKELANGID(a,b) ((a) | ((b) << 10))
#define FT_IS_SCALABLE(face) (fixture_case->scalable)
#define ft_sfnt_os2 0
#define TRACE(...) ((void)0)
#define WARN(...) ((void)0)
#define lstrlenW wcslen
#define lstrcpyW wcscpy
#define lstrcatW wcscat
#define wcsicmp wcscasecmp
static LANGID system_lcid = 1033;
struct font_case {
    const char *label;
    const WCHAR *family;
    int opentype, scalable, symbol_bit, rejected, missing_full, no_font, calloc_fail, same_secondary;
};
static const struct font_case *fixture_case;
static void *owned[16];
static unsigned owned_count, done_count;
static void *fixture_malloc(size_t size)
{
    void *ptr = malloc(size);
    if (!ptr || owned_count >= sizeof(owned) / sizeof(owned[0])) abort();
    owned[owned_count++] = ptr;
    return ptr;
}
static void *fixture_calloc(size_t n, size_t size)
{
    void *ptr;
    if (fixture_case->calloc_fail) return NULL;
    ptr = fixture_malloc(n * size);
    memset(ptr, 0, n * size);
    return ptr;
}
static void fixture_free(void *ptr)
{
    unsigned i;
    if (!ptr) return;
    for (i = 0; i < owned_count; i++)
    {
        if (owned[i] != ptr) continue;
        free(ptr);
        owned[i] = owned[--owned_count];
        return;
    }
    fprintf(stderr, "unowned or duplicate free in %s\n", fixture_case->label);
    abort();
}
static WCHAR *copy_name(const WCHAR *name)
{
    size_t size = (wcslen(name) + 1) * sizeof(*name);
    WCHAR *result = fixture_malloc(size);
    memcpy(result, name, size);
    return result;
}
static void search_family_names_callback(void) {}
static void search_face_name_callback(void) {}
static BOOL opentype_get_ttc_sfnt_v1(void *data, UINT size, UINT index, DWORD *count,
                                    const struct ttc_sfnt_v1 **table)
{
    static struct ttc_sfnt_v1 value;
    (void)data; (void)size; (void)index;
    *count = 1; *table = &value;
    return fixture_case->opentype;
}
static BOOL opentype_get_tt_name_v0(void *data, UINT size, const struct ttc_sfnt_v1 *table,
                                   const struct tt_name_v0 **names)
{
    static struct tt_name_v0 value;
    (void)data; (void)size; (void)table;
    *names = &value;
    return TRUE;
}
static BOOL opentype_get_properties(void *data, UINT size, const struct ttc_sfnt_v1 *table,
                                    DWORD *version, FONTSIGNATURE *fs, DWORD *flags, UINT *weight)
{
    (void)data; (void)size; (void)table;
    *version = 1; *flags = 0; *weight = FW_NORMAL;
    fs->fsCsb[0] = fixture_case->symbol_bit ? 0x80000000u : 0;
    return TRUE;
}
static void opentype_enum_family_names(const struct tt_name_v0 *table, void (*callback)(void),
                                       struct family_names_data *names)
{
    (void)table; (void)callback;
    names->family_name.tag = 0; names->second_name.tag = 1;
}
static void opentype_enum_style_names(const struct tt_name_v0 *table, void (*callback)(void),
                                      struct face_name_data *name)
{
    (void)table; (void)callback; name->face_name.tag = 2;
}
static void opentype_enum_full_names(const struct tt_name_v0 *table, void (*callback)(void),
                                     struct face_name_data *name)
{
    (void)table; (void)callback; name->face_name.tag = 3;
}
static WCHAR *decode_opentype_name(struct opentype_name *name)
{
    if (name->tag == 3 && fixture_case->missing_full) return NULL;
    return copy_name(name->tag == 0 ? fixture_case->family :
                     name->tag == 1 ? L"Secondary" : name->tag == 2 ? L"Regular" : L"Full name");
}
static FT_Face new_ft_face(const char *path, void *data, UINT size, UINT index, UINT flags)
{
    FT_Face face;
    (void)path; (void)data; (void)size; (void)index; (void)flags;
    if (fixture_case->no_font) return NULL;
    face = fixture_malloc(sizeof(*face));
    face->num_faces = 1;
    return face;
}
static WCHAR *ft_face_get_family_name(FT_Face face, LANGID lang)
{
    (void)face;
    return copy_name(lang == system_lcid || fixture_case->same_secondary ? fixture_case->family : L"Secondary");
}
static WCHAR *ft_face_get_style_name(FT_Face face, LANGID lang)
{
    (void)face; (void)lang; return copy_name(L"Regular");
}
static WCHAR *ft_face_get_full_name(FT_Face face, LANGID lang)
{
    (void)face; (void)lang; return copy_name(L"Full name");
}
static DWORD get_ntm_flags(FT_Face face) { (void)face; return 0; }
static DWORD get_font_version(FT_Face face) { (void)face; return 1; }
static void get_bitmap_size(FT_Face face, struct bitmap_font_size *size) { (void)face; size->unused = 1; }
static void get_fontsig(FT_Face face, FONTSIGNATURE *fs)
{
    (void)face; fs->fsCsb[0] = fixture_case->symbol_bit ? 0x80000000u : 0;
}
static TT_OS2 *pFT_Get_Sfnt_Table(FT_Face face, int table) { (void)face; (void)table; return NULL; }
static void pFT_Done_Face(FT_Face face) { done_count++; fixture_free(face); }
#define malloc fixture_malloc
#define calloc fixture_calloc
#define free fixture_free
'''

MAIN = r'''
int main(void)
{
    static const struct font_case cases[] = {
        {"invalid font", L"Invalid", 0, 0, 0, 1, 0, 1, 0, 0},
        {"OpenType ZenKai", L"AR PL ZenKai Uni", 1, 1, 0, 1, 0, 0, 0, 0},
        {"FreeType ZenKai", L"AR PL ZenKai Uni", 0, 1, 0, 1, 0, 0, 0, 0},
        {"OpenType Samyak", L"Samyak Oriya", 1, 1, 0, 1, 0, 0, 0, 0},
        {"FreeType Samyak", L"Samyak Oriya", 0, 1, 0, 1, 0, 0, 0, 0},
        {"OpenType Apple Symbol", L"Symbol", 1, 1, 0, 1, 0, 0, 0, 0},
        {"FreeType Apple Symbol", L"Symbol", 0, 1, 0, 1, 0, 0, 0, 0},
        {"OpenType accepted", L"Normal", 1, 1, 0, 0, 0, 0, 0, 0},
        {"FreeType accepted", L"Normal", 0, 1, 0, 0, 0, 0, 0, 0},
        {"OpenType symbol charset", L"Symbol", 1, 1, 1, 0, 0, 0, 0, 0},
        {"FreeType symbol charset", L"Symbol", 0, 1, 1, 0, 0, 0, 0, 0},
        {"bitmap Symbol", L"Symbol", 0, 0, 0, 0, 0, 0, 0, 0},
        {"constructed full name", L"Normal", 1, 1, 0, 0, 1, 0, 0, 0},
        {"rejected constructed full name", L"Symbol", 1, 1, 0, 1, 1, 0, 0, 0},
        {"duplicate secondary", L"Normal", 0, 1, 0, 0, 0, 0, 0, 1},
        {"allocation failure", L"Normal", 1, 1, 0, 1, 0, 0, 1, 0},
    };
    unsigned i, failed = 0;
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
    {
        struct unix_face *face;
        unsigned expected_done;
        fixture_case = &cases[i];
        assert(!owned_count);
        done_count = 0;
        face = unix_face_create(NULL, NULL, 0, 0, ADDFONT_ALLOW_BITMAP);
        if (!!face == fixture_case->rejected)
        {
            fprintf(stderr, "%s: wrong accept/reject result\n", fixture_case->label);
            failed++;
        }
        if (face)
        {
            if (wcscmp(face->family_name, fixture_case->family) || !face->style_name || !face->full_name)
            {
                fprintf(stderr, "%s: accepted face lost names\n", fixture_case->label);
                failed++;
            }
            unix_face_destroy(face);
        }
        expected_done = !fixture_case->opentype && !fixture_case->no_font && !fixture_case->calloc_fail;
        if (owned_count || done_count != expected_done)
        {
            fprintf(stderr, "%s: %u owned allocation(s) remain, %u/%u FreeType faces released\n",
                    fixture_case->label, owned_count, done_count, expected_done);
            failed++;
        }
        /* Negative controls intentionally omit cleanup. Release their leftovers
         * only after recording failure, keeping the sanitizer report readable. */
        while (owned_count) fixture_free(owned[0]);
    }
    return !!failed;
}
'''


def run_fixture(helper, directory, name, sanitize=False):
    source, binary = directory / (name + ".c"), directory / name
    source.write_text(PRELUDE + helper + MAIN)
    flags = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if sanitize else []
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-g", *flags,
                    str(source), "-o", str(binary)], check=True, timeout=30)
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1", UBSAN_OPTIONS="halt_on_error=1")
    return subprocess.run([str(binary)], env=env, capture_output=True, text=True, timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wine_source", type=Path)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    source = (args.wine_source / "dlls/win32u/freetype.c").read_text()
    helper = extract(source)
    if args.source_only:
        print("Wine font constructor/destructor ownership source contract passed; execution not requested")
        return 0
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("font fixture compilation/execution requires GitHub Actions on Linux; use --source-only locally")
    with tempfile.TemporaryDirectory(prefix="gluon-font-ownership-") as temp:
        directory = Path(temp)
        result = run_fixture(helper, directory, "font-ownership", sanitize=True)
        if result.returncode:
            raise ValueError("font ownership fixture failed:\n" + result.stderr)
        if args.self_test:
            mutants = [("old-rejections", helper.replace("unix_face_destroy( This );", "free( This );"))]
            mutants += [("missing-" + member, helper.replace("    free( This->%s );\n" % member, ""))
                        for member in ("family_name", "second_name", "style_name", "full_name")]
            mutants.append(("missing-ft-face", helper.replace("    if (This->ft_face) pFT_Done_Face( This->ft_face );\n", "")))
            for name, mutant in mutants:
                if mutant == helper:
                    raise ValueError("font ownership negative control did not change source: " + name)
                # pFT_Done_Face deliberately becomes unused in this one control.
                if name == "missing-ft-face":
                    mutant += "\nstatic void (*fixture_keep_done)(FT_Face) __attribute__((used)) = pFT_Done_Face;\n"
                result = run_fixture(mutant, directory, name)
                if result.returncode != 1 or "owned allocation(s) remain" not in result.stderr:
                    raise ValueError("font ownership fixture missed its negative control: " + name + "\n" + result.stderr)
    print("Wine font ownership passed 16 constructor/destructor cases under ASan/UBSan/LSan%s" %
          ("; original rejection leak and all five omitted cleanups detected" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("font ownership check: %s" % error, file=sys.stderr)
        sys.exit(1)

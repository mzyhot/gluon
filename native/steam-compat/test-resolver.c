/* Synthetic fixtures only. No Steam executable, signature table, or proprietary
 * instruction sequence is included or needed by these tests. */
#include "resolver.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#define FIXTURE_SIZE 16384u
#define TEXT_START 4096u
#define STRINGS_START 8192u
#define TEXT_SIZE 128u
#define STRINGS_SIZE 256u
#define COMMAND_SIZE (72u + 2u * 80u)
#define SYNTHETIC_LOAD_BASE UINT64_C(65536)
#define ARM64 UINT32_C(0x0100000c)
#define X86_64 UINT32_C(0x01000007)

struct fixture {
    unsigned char bytes[FIXTURE_SIZE];
    uint64_t base;
    size_t available;
    size_t string_offsets[GLUON_COMPAT_ANCHOR_COUNT];
};

static void put32(unsigned char *p, uint32_t value)
{
    unsigned i;
    for (i = 0; i < 4; ++i) p[i] = (unsigned char)(value >> (8 * i));
}

static void put64(unsigned char *p, uint64_t value)
{
    put32(p, (uint32_t)value); put32(p + 4, (uint32_t)(value >> 32));
}

static int read_fixture(void *opaque, uint64_t address, void *dest, size_t size)
{
    struct fixture *f = opaque;
    if (address < f->base || size > f->available || address - f->base > f->available - size) return 0;
    memcpy(dest, f->bytes + (size_t)(address - f->base), size);
    return 1;
}

static void adr(struct fixture *f, size_t code, size_t target, unsigned reg)
{
    uint32_t immediate = (uint32_t)((int64_t)target - (int64_t)code) & 0x1fffffu;
    put32(f->bytes + code, 0x10000000u | (immediate & 3u) << 29 |
          (immediate >> 2) << 5 | reg);
}

static void adrp_add(struct fixture *f, size_t code, size_t target, unsigned reg)
{
    uint64_t pc = f->base + code, destination = f->base + target;
    int64_t pages = ((int64_t)(destination & ~UINT64_C(4095)) -
                     (int64_t)(pc & ~UINT64_C(4095))) / 4096;
    uint32_t immediate = (uint32_t)pages & 0x1fffffu;
    put32(f->bytes + code, 0x90000000u | (immediate & 3u) << 29 |
          (immediate >> 2) << 5 | reg);
    put32(f->bytes + code + 4, 0x91000000u | (uint32_t)(destination & 4095u) << 10 |
          reg << 5 | reg);
}

static void lea(struct fixture *f, size_t code, size_t target)
{
    f->bytes[code] = 0x48; f->bytes[code + 1] = 0x8d; f->bytes[code + 2] = 0x05;
    put32(f->bytes + code + 3, (uint32_t)((int64_t)target - (int64_t)(code + 7)));
}

static void make_fixture(struct fixture *f, uint32_t cpu)
{
    unsigned char *segment, *section;
    size_t i, cursor = STRINGS_START;
    memset(f, 0, sizeof(*f));
    f->base = SYNTHETIC_LOAD_BASE; f->available = sizeof(f->bytes);
    put32(f->bytes, 0xfeedfacfu); put32(f->bytes + 4, cpu);
    put32(f->bytes + 12, 6); /* MH_DYLIB */
    put32(f->bytes + 16, 1); put32(f->bytes + 20, COMMAND_SIZE);
    segment = f->bytes + 32;
    put32(segment, 0x19); put32(segment + 4, COMMAND_SIZE);
    memcpy(segment + 8, "__TEXT", 7);
    put64(segment + 24, 0); put64(segment + 32, FIXTURE_SIZE);
    put64(segment + 48, FIXTURE_SIZE);
    put32(segment + 56, 5); put32(segment + 60, 5); put32(segment + 64, 2);
    section = segment + 72;
    memcpy(section, "__text", 7); memcpy(section + 16, "__TEXT", 7);
    put64(section + 32, TEXT_START); put64(section + 40, TEXT_SIZE);
    put32(section + 64, 0x80000400u);
    section += 80;
    memcpy(section, "__cstring", 10); memcpy(section + 16, "__TEXT", 7);
    put64(section + 32, STRINGS_START); put64(section + 40, STRINGS_SIZE);
    put32(section + 64, 2);
    for (i = 0; i < GLUON_COMPAT_ANCHOR_COUNT; ++i) {
        const char *name = gluon_compat_anchor_name(i);
        /* Simulates an assertion string, not a copied Valve assertion. */
        f->string_offsets[i] = cursor;
        memcpy(f->bytes + cursor, "fixture::", 9); cursor += 9;
        memcpy(f->bytes + cursor, name, strlen(name) + 1); cursor += strlen(name) + 1;
        if (cpu == ARM64) adr(f, TEXT_START + 16 * i, f->string_offsets[i], (unsigned)i);
        else lea(f, TEXT_START + 16 * i, f->string_offsets[i]);
    }
}

static struct gluon_compat_report inspect(struct fixture *f)
{
    struct gluon_compat_image image = {read_fixture, f, f->base};
    struct gluon_compat_report report = gluon_compat_inspect(&image);
    assert(!gluon_compat_activation_supported());
    return report;
}

static void expect(struct fixture *f, enum gluon_compat_discovery expected)
{
    struct gluon_compat_report result = inspect(f);
    if (result.discovery != expected) {
        fprintf(stderr, "expected %s, got %s\n", gluon_compat_discovery_name(expected),
                gluon_compat_discovery_name(result.discovery));
        assert(result.discovery == expected);
    }
}

static void test_resolution(void)
{
    struct fixture f;
    struct gluon_compat_report result;
    size_t i;
    make_fixture(&f, ARM64);
    expect(&f, GLUON_COMPAT_CANDIDATES);
    result = inspect(&f);
    for (i = 0; i < GLUON_COMPAT_ANCHOR_COUNT; ++i) {
        assert(result.anchors[i].strings == 1);
        assert(result.anchors[i].references == 1);
    }
    for (i = 0; i < GLUON_COMPAT_ANCHOR_COUNT; ++i)
        adrp_add(&f, TEXT_START + 16 * i, f.string_offsets[i], (unsigned)i);
    expect(&f, GLUON_COMPAT_CANDIDATES);
    /* A different load address exercises image-relative section mapping. */
    f.base += 32768;
    expect(&f, GLUON_COMPAT_CANDIDATES);
    /* An ADD using another source register is not a reference. */
    f.bytes[TEXT_START + 4] ^= 32;
    expect(&f, GLUON_COMPAT_MISSING);
    make_fixture(&f, X86_64);
    expect(&f, GLUON_COMPAT_CANDIDATES);
    /* Duplicate code references are ambiguous even with one anchor string. */
    lea(&f, TEXT_START + 96, f.string_offsets[0]);
    expect(&f, GLUON_COMPAT_AMBIGUOUS);
    make_fixture(&f, ARM64);
    adr(&f, TEXT_START + 96, f.string_offsets[0], 3);
    expect(&f, GLUON_COMPAT_AMBIGUOUS);
    make_fixture(&f, ARM64);
    memcpy(f.bytes + STRINGS_START + 200, gluon_compat_anchor_name(0),
           strlen(gluon_compat_anchor_name(0)) + 1);
    expect(&f, GLUON_COMPAT_AMBIGUOUS);
    make_fixture(&f, ARM64);
    memset(f.bytes + TEXT_START, 0, 4);
    expect(&f, GLUON_COMPAT_MISSING);
    make_fixture(&f, ARM64);
    f.bytes[f.string_offsets[0] + 8] = 'x'; /* Not an identifier boundary. */
    expect(&f, GLUON_COMPAT_MISSING);
    make_fixture(&f, ARM64);
    memset(f.bytes + STRINGS_START, 'x', STRINGS_SIZE);
    expect(&f, GLUON_COMPAT_MALFORMED);
}

static void test_negative_references(void)
{
    struct fixture f;
    size_t i;
    make_fixture(&f, ARM64);
    /* Swap sections so references travel backwards. */
    memcpy(f.bytes + TEXT_START, f.bytes + STRINGS_START, STRINGS_SIZE);
    memset(f.bytes + STRINGS_START, 0, STRINGS_SIZE);
    put64(f.bytes + 32 + 72 + 32, STRINGS_START);
    put64(f.bytes + 32 + 72 + 80 + 32, TEXT_START);
    for (i = 0; i < GLUON_COMPAT_ANCHOR_COUNT; ++i) {
        f.string_offsets[i] -= STRINGS_START - TEXT_START;
        adr(&f, STRINGS_START + 16 * i, f.string_offsets[i], (unsigned)i);
    }
    expect(&f, GLUON_COMPAT_CANDIDATES);
    for (i = 0; i < GLUON_COMPAT_ANCHOR_COUNT; ++i)
        adrp_add(&f, STRINGS_START + 16 * i, f.string_offsets[i], (unsigned)i);
    expect(&f, GLUON_COMPAT_CANDIDATES);
    put32(f.bytes + 4, X86_64);
    memset(f.bytes + STRINGS_START, 0, TEXT_SIZE);
    for (i = 0; i < GLUON_COMPAT_ANCHOR_COUNT; ++i)
        lea(&f, STRINGS_START + 16 * i, f.string_offsets[i]);
    expect(&f, GLUON_COMPAT_CANDIDATES);
}

static void test_malformed_and_bounds(void)
{
    struct fixture f;
    make_fixture(&f, ARM64); f.available = 16;
    expect(&f, GLUON_COMPAT_UNREADABLE);
    make_fixture(&f, ARM64); f.available = 40;
    expect(&f, GLUON_COMPAT_UNREADABLE);
    make_fixture(&f, ARM64); f.available = STRINGS_START;
    expect(&f, GLUON_COMPAT_UNREADABLE);
    make_fixture(&f, ARM64); put32(f.bytes, 0);
    expect(&f, GLUON_COMPAT_UNSUPPORTED);
    make_fixture(&f, 7);
    expect(&f, GLUON_COMPAT_UNSUPPORTED);
    make_fixture(&f, ARM64); put32(f.bytes + 20, GLUON_COMPAT_MAX_COMMAND_BYTES + 1);
    expect(&f, GLUON_COMPAT_LIMIT);
    make_fixture(&f, ARM64); put32(f.bytes + 16, 4097);
    expect(&f, GLUON_COMPAT_LIMIT);
    make_fixture(&f, ARM64); put32(f.bytes + 36, 0);
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); put32(f.bytes + 36, COMMAND_SIZE + 8);
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); put32(f.bytes + 32 + 64, 3);
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); put64(f.bytes + 32 + 24, UINT64_MAX - 1);
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); put64(f.bytes + 32 + 72 + 32, FIXTURE_SIZE);
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); put64(f.bytes + 32 + 72 + 80 + 32, TEXT_START);
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); put64(f.bytes + 32 + 72 + 32, 32);
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); put32(f.bytes + 32 + 60, 7); /* Writable text. */
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); f.base = UINT64_MAX - FIXTURE_SIZE + 1;
    expect(&f, GLUON_COMPAT_MALFORMED);
    make_fixture(&f, ARM64); put64(f.bytes + 32 + 32, GLUON_COMPAT_MAX_TEXT_BYTES * 2u);
    put64(f.bytes + 32 + 72 + 40, GLUON_COMPAT_MAX_TEXT_BYTES + 1u);
    expect(&f, GLUON_COMPAT_LIMIT);
    make_fixture(&f, ARM64); memcpy(f.bytes + 32 + 72, "__unused", 9);
    expect(&f, GLUON_COMPAT_MISSING);
    make_fixture(&f, ARM64); memcpy(f.bytes + 32 + 72 + 80, "__text\0\0\0", 10);
    expect(&f, GLUON_COMPAT_MALFORMED);
    assert(gluon_compat_inspect(NULL).discovery == GLUON_COMPAT_MALFORMED);
}

static void test_policy(void)
{
    struct gluon_compat_app app = {1, 1, 1, "game", "windows", ""};
    assert(gluon_compat_app_eligible(&app));
    app.oslist = " windows, macos "; app.osextended = "macos64";
    assert(!gluon_compat_app_eligible(&app));
    app.osextended = "macosapplesilicon";
    assert(!gluon_compat_app_eligible(&app));
    app.osextended = " "; /* Obsolete Mac build with a Windows build. */
    assert(gluon_compat_app_eligible(&app));
    app.oslist = "macos";
    assert(!gluon_compat_app_eligible(&app));
    app.oslist = NULL;
    assert(gluon_compat_app_eligible(&app));
    /* This deliberately matches the existing Python rule, which accepts any
     * app without macos, including explicit Linux-only metadata. It is not a
     * new Windows-build detector. Activation remains unavailable. */
    app.oslist = "linux";
    assert(gluon_compat_app_eligible(&app));
    app.oslist = "windows"; app.type = "tool";
    assert(!gluon_compat_app_eligible(&app));
    app.type = "DLC";
    assert(!gluon_compat_app_eligible(&app));
    app.type = "DEMO";
    assert(gluon_compat_app_eligible(&app));
    app.metadata_current = 0;
    assert(!gluon_compat_app_eligible(&app));
    app.metadata_current = 1; app.in_library = 0;
    assert(!gluon_compat_app_eligible(&app));
    app.in_library = 1; app.appid = 0;
    assert(!gluon_compat_app_eligible(&app));
    assert(!gluon_compat_app_eligible(NULL));
}

int main(void)
{
    test_resolution(); test_negative_references(); test_malformed_and_bounds(); test_policy();
    puts("Steam compatibility resolver: synthetic image and policy checks passed; activation disabled.");
    return 0;
}

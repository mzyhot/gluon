#include "resolver.h"

#include <stdlib.h>
#include <string.h>

/* Mach-O ABI values, not signatures or locations from a Steam build. */
#define MACH64_MAGIC UINT32_C(0xfeedfacf)
#define CPU_X86_64 UINT32_C(0x01000007)
#define CPU_ARM64 UINT32_C(0x0100000c)
#define SEGMENT64 0x19u
#define HEADER_SIZE 32u
#define SEGMENT_SIZE 72u
#define SECTION_SIZE 80u
#define MAX_COMMANDS 4096u

static const char *const names[GLUON_COMPAT_ANCHOR_COUNT] = {
    "FindToolForTargetApp", "GetOSListOverrideForApp", "ResolveCompatToolForApp"
};

struct section {
    uint64_t address, size;
    unsigned present;
};

static uint32_t u32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint64_t u64(const unsigned char *p)
{
    return (uint64_t)u32(p) | (uint64_t)u32(p + 4) << 32;
}

static int span(uint64_t address, uint64_t size)
{
    return size <= UINT64_MAX - address;
}

static int within(uint64_t address, uint64_t size, uint64_t base, uint64_t length)
{
    return address >= base && size <= length && address - base <= length - size;
}

static int named(const unsigned char *p, const char *name)
{
    size_t n = strlen(name);
    return n < 16 && memcmp(p, name, n) == 0 && p[n] == 0;
}

static unsigned increment(unsigned n)
{
    return n < 2 ? n + 1 : 2;
}

/* An identifier boundary avoids matching unrelated longer method names. */
static int identifier(unsigned char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9') || c == '_';
}

static int contains(const unsigned char *p, size_t size, const char *name)
{
    size_t n = strlen(name), i;
    if (size < n) return 0;
    for (i = 0; i <= size - n; ++i) {
        if (memcmp(p + i, name, n) == 0 && (!i || !identifier(p[i - 1])) &&
            (i + n == size || !identifier(p[i + n]))) return 1;
    }
    return 0;
}

static int add_signed(uint64_t base, int64_t delta, uint64_t *out)
{
    if (delta >= 0) {
        if (!span(base, (uint64_t)delta)) return 0;
        *out = base + (uint64_t)delta;
    } else {
        uint64_t magnitude = (uint64_t)(-(delta + 1)) + 1;
        if (magnitude > base) return 0;
        *out = base - magnitude;
    }
    return 1;
}

static int64_t sign_extend(uint32_t value, unsigned bits)
{
    uint32_t sign = UINT32_C(1) << (bits - 1);
    return (int64_t)(value & (sign - 1)) - (int64_t)(value & sign);
}

static void reference(struct gluon_compat_report *out, const uint64_t *addresses, uint64_t target)
{
    size_t a;
    for (a = 0; a < GLUON_COMPAT_ANCHOR_COUNT; ++a)
        if (out->anchors[a].strings == 1 && target == addresses[a])
            out->anchors[a].references = increment(out->anchors[a].references);
}

static void arm_references(const unsigned char *text, size_t size, uint64_t base,
                           struct gluon_compat_report *out, const uint64_t *addresses)
{
    size_t i;
    for (i = 0; i + 4 <= size; i += 4) {
        uint32_t insn = u32(text + i);
        uint32_t imm = ((insn >> 5) & 0x7ffffu) << 2 | ((insn >> 29) & 3u);
        uint64_t target;
        if ((insn & 0x9f000000u) == 0x10000000u) { /* ADR */
            if (add_signed(base + i, sign_extend(imm, 21), &target))
                reference(out, addresses, target);
        } else if ((insn & 0x9f000000u) == 0x90000000u && i + 8 <= size) { /* ADRP */
            uint32_t next = u32(text + i + 4);
            /* Only an adjacent 64-bit ADD-immediate using the ADRP register.
             * Other instruction sequences intentionally remain unresolved. */
            if ((insn & 31u) == 31u || (next & 0xffc00000u) != 0x91000000u ||
                ((next >> 5) & 31u) != (insn & 31u) || (next & 31u) == 31u) continue;
            if (add_signed((base + i) & ~UINT64_C(4095), sign_extend(imm, 21) * 4096, &target) &&
                span(target, (next >> 10) & 4095u))
                reference(out, addresses, target + ((next >> 10) & 4095u));
        }
    }
}

static void intel_references(const unsigned char *text, size_t size, uint64_t base,
                             struct gluon_compat_report *out, const uint64_t *addresses)
{
    size_t i;
    /* Candidate RIP-relative LEA only. Without a full instruction decoder a
     * byte match can lie inside another instruction; never treat it as an ABI. */
    for (i = 0; i + 7 <= size; ++i) {
        uint64_t target;
        if ((text[i] & 0xfbu) != 0x48u || text[i + 1] != 0x8d ||
            (text[i + 2] & 0xc7u) != 0x05u) continue;
        if (add_signed(base + i + 7, sign_extend(u32(text + i + 3), 32), &target))
            reference(out, addresses, target);
    }
}

struct gluon_compat_report gluon_compat_inspect(const struct gluon_compat_image *image)
{
    struct gluon_compat_report out = {GLUON_COMPAT_MALFORMED, {{0, 0}}};
    unsigned char header[HEADER_SIZE], *commands = NULL, *text = NULL, *strings = NULL;
    struct section code = {0, 0, 0}, cstrings = {0, 0, 0};
    uint64_t text_base = 0, text_size = 0, addresses[GLUON_COMPAT_ANCHOR_COUNT] = {0};
    uint32_t cpu, count, size, c;
    size_t offset = 0, i;
    unsigned text_segments = 0;
    if (!image || !image->read || !span(image->header_address, HEADER_SIZE)) return out;
    if (!image->read(image->context, image->header_address, header, sizeof(header))) {
        out.discovery = GLUON_COMPAT_UNREADABLE; return out;
    }
    cpu = u32(header + 4);
    if (u32(header) != MACH64_MAGIC || (cpu != CPU_X86_64 && cpu != CPU_ARM64)) {
        out.discovery = GLUON_COMPAT_UNSUPPORTED; return out;
    }
    count = u32(header + 16); size = u32(header + 20);
    if (!count || size < 8 || !span(image->header_address + HEADER_SIZE, size)) return out;
    if (count > MAX_COMMANDS || size > GLUON_COMPAT_MAX_COMMAND_BYTES) {
        out.discovery = GLUON_COMPAT_LIMIT; return out;
    }
    commands = malloc(size);
    if (!commands) { out.discovery = GLUON_COMPAT_LIMIT; goto done; }
    if (!image->read(image->context, image->header_address + HEADER_SIZE, commands, size)) {
        out.discovery = GLUON_COMPAT_UNREADABLE; goto done;
    }
    for (c = 0; c < count; ++c) {
        const unsigned char *p;
        uint32_t length;
        if (offset > size || size - offset < 8) goto done;
        p = commands + offset; length = u32(p + 4);
        if (length < 8 || length % 8 || length > size - offset) goto done;
        if (u32(p) == SEGMENT64) {
            uint32_t sections, s;
            uint64_t base, length_vm;
            if (length < SEGMENT_SIZE) goto done;
            sections = u32(p + 64); base = u64(p + 24); length_vm = u64(p + 32);
            if (sections > (length - SEGMENT_SIZE) / SECTION_SIZE || !span(base, length_vm)) goto done;
            if (named(p + 8, "__TEXT")) {
                if (++text_segments != 1 || u64(p + 40) != 0 ||
                    length_vm < HEADER_SIZE + (uint64_t)size ||
                    (u32(p + 60) & 5u) != 5u || (u32(p + 60) & 2u)) goto done;
                text_base = base; text_size = length_vm;
                for (s = 0; s < sections; ++s) {
                    const unsigned char *sec = p + SEGMENT_SIZE + s * SECTION_SIZE;
                    struct section *dest = NULL;
                    if (!named(sec + 16, "__TEXT")) goto done;
                    if (named(sec, "__text")) dest = &code;
                    if (named(sec, "__cstring")) dest = &cstrings;
                    if (!dest) continue;
                    if (dest->present++) goto done;
                    dest->address = u64(sec + 32); dest->size = u64(sec + 40);
                    if (!dest->size || !within(dest->address, dest->size, base, length_vm)) goto done;
                    if (dest == &cstrings && (u32(sec + 64) & 0xffu) != 2u) goto done;
                    if (dest == &code && (u32(sec + 64) & 0xffu) != 0u) goto done;
                }
            }
        }
        offset += length;
    }
    if (offset != size) goto done;
    if (!text_segments || !code.present || !cstrings.present) { out.discovery = GLUON_COMPAT_MISSING; goto done; }
    if (code.size > GLUON_COMPAT_MAX_TEXT_BYTES || cstrings.size > GLUON_COMPAT_MAX_STRING_BYTES) {
        out.discovery = GLUON_COMPAT_LIMIT; goto done;
    }
    if (!span(image->header_address, text_size) ||
        code.address - text_base < HEADER_SIZE + (uint64_t)size ||
        cstrings.address - text_base < HEADER_SIZE + (uint64_t)size ||
        (code.address < cstrings.address + cstrings.size && cstrings.address < code.address + code.size)) goto done;
    code.address = image->header_address + (code.address - text_base);
    cstrings.address = image->header_address + (cstrings.address - text_base);
    if (cpu == CPU_ARM64 && (code.address % 4 || code.size % 4)) goto done;
    text = malloc((size_t)code.size); strings = malloc((size_t)cstrings.size);
    if (!text || !strings) { out.discovery = GLUON_COMPAT_LIMIT; goto done; }
    if (!image->read(image->context, code.address, text, (size_t)code.size) ||
        !image->read(image->context, cstrings.address, strings, (size_t)cstrings.size)) {
        out.discovery = GLUON_COMPAT_UNREADABLE; goto done;
    }
    for (i = 0; i < cstrings.size;) {
        unsigned char *end = memchr(strings + i, 0, (size_t)cstrings.size - i);
        size_t a, length;
        if (!end) goto done;
        length = (size_t)(end - (strings + i));
        for (a = 0; a < GLUON_COMPAT_ANCHOR_COUNT; ++a) {
            if (!contains(strings + i, length, names[a])) continue;
            out.anchors[a].strings = increment(out.anchors[a].strings);
            addresses[a] = cstrings.address + i;
        }
        i += length + 1;
    }
    if (cpu == CPU_ARM64) arm_references(text, (size_t)code.size, code.address, &out, addresses);
    else intel_references(text, (size_t)code.size, code.address, &out, addresses);
    out.discovery = GLUON_COMPAT_CANDIDATES;
    for (i = 0; i < GLUON_COMPAT_ANCHOR_COUNT; ++i) {
        if (out.anchors[i].strings > 1 || out.anchors[i].references > 1) {
            out.discovery = GLUON_COMPAT_AMBIGUOUS; break;
        }
        if (!out.anchors[i].strings || !out.anchors[i].references) out.discovery = GLUON_COMPAT_MISSING;
    }
done:
    free(strings); free(text); free(commands);
    return out;
}

const char *gluon_compat_discovery_name(enum gluon_compat_discovery value)
{
    switch (value) {
    case GLUON_COMPAT_MALFORMED: return "malformed-image";
    case GLUON_COMPAT_UNSUPPORTED: return "unsupported-image";
    case GLUON_COMPAT_UNREADABLE: return "unreadable-image";
    case GLUON_COMPAT_LIMIT: return "scan-limit";
    case GLUON_COMPAT_MISSING: return "missing-anchor";
    case GLUON_COMPAT_AMBIGUOUS: return "ambiguous-anchor";
    case GLUON_COMPAT_CANDIDATES: return "anchor-candidates-only";
    }
    return "unknown";
}

const char *gluon_compat_anchor_name(size_t index)
{
    return index < GLUON_COMPAT_ANCHOR_COUNT ? names[index] : "unknown";
}

int gluon_compat_activation_supported(void)
{
    /* Never weaken this until a separately reviewed ABI and per-app hook exist. */
    return 0;
}

static int whitespace(unsigned char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' || c == '\v';
}

static int csv_has(const char *csv, const char *token)
{
    const char *p = csv ? csv : "";
    size_t token_size = strlen(token);
    while (*p) {
        const char *start = p, *end;
        while (*p && *p != ',') ++p;
        end = p;
        while (start < end && whitespace((unsigned char)*start)) ++start;
        while (end > start && whitespace((unsigned char)end[-1])) --end;
        if ((size_t)(end - start) == token_size && memcmp(start, token, token_size) == 0) return 1;
        if (*p) ++p;
    }
    return 0;
}

static int type_is(const char *input, const char *expected)
{
    if (!input) return 0;
    while (*input && *expected) {
        unsigned char c = (unsigned char)*input++;
        if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
        if (c != (unsigned char)*expected++) return 0;
    }
    return !*input && !*expected;
}

int gluon_compat_app_eligible(const struct gluon_compat_app *app)
{
    int has_mac;
    if (!app || !app->appid || !app->metadata_current || !app->in_library) return 0;
    if (!type_is(app->type, "game") && !type_is(app->type, "demo") &&
        !type_is(app->type, "application") && !type_is(app->type, "beta") &&
        !type_is(app->type, "mod")) return 0;
    has_mac = csv_has(app->oslist, "macos");
    if (!has_mac) return 1;
    return csv_has(app->oslist, "windows") && !csv_has(app->osextended, "macos64") &&
           !csv_has(app->osextended, "macosapplesilicon");
}

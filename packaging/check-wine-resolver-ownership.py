#!/usr/bin/env python3
"""Check the exact Wine getaddrinfo fallback for leaks and preserved error results.

The resolver and computer-name APIs are stubbed; getaddrinfo and its allocated
get_fqdn helper are extracted unchanged. No DNS queries or Wine processes run.
Compilation and ASan/UBSan/LSan execution require GitHub Actions on Linux.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


REJECT_CLEANUP = """                free( fqdn );
                free( nodev6 );
                return EAI_NONAME;"""


def extract(source):
    parts = []
    for pattern in (r"^static char \*get_fqdn\(void\)\n\{.*?^\}",
                    r"^int WINAPI getaddrinfo\(.*?^\}"):
        matches = re.findall(pattern, source, re.MULTILINE | re.DOTALL)
        if len(matches) != 1:
            raise ValueError("expected exactly one resolver source section: " + pattern)
        parts.append(matches[0])
    if parts[1].count(REJECT_CLEANUP) != 1:
        raise ValueError("canonical-name rejection must free temporary names before its existing direct return")
    return "\n\n".join(parts)


PRELUDE = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int DWORD;
#define WINAPI
#define ComputerNamePhysicalDnsFullyQualified 3
#define ERROR_MORE_DATA 234
#define WSA_NOT_ENOUGH_MEMORY 8
#define WSAHOST_NOT_FOUND 11001
#define EAI_NONAME 11001
#define AF_UNSPEC 0
#define AF_INET6 23
#define AI_CANONNAME 2
#define AI_NUMERICHOST 4
#define AI_ALL 256
#define AI_DNS_ONLY 0x20000000
#define TRACE(...) ((void)0)
#define ERR_(channel) FIXTURE_ERR
#define FIXTURE_ERR(...) ((void)0)
#define TRACE_ON(channel) 0
struct addrinfo {
    int ai_flags, ai_family;
    struct addrinfo *ai_next;
    char *ai_canonname;
};
struct resolver_case {
    const char *label, *node, *service;
    int flags, primary_error, fallback_error, fallback_canon;
    int expected_result, expected_last, expected_calls, expected_info, null_hints, alloc_fail;
};
static const struct resolver_case *fixture_case;
static void *owned[16];
static unsigned owned_count, resolver_calls, dns_calls;
static DWORD last_error;
static DWORD GetLastError(void) { return last_error; }
static void SetLastError(DWORD value) { last_error = value; }
static void *fixture_malloc(size_t size)
{
    void *ptr;
    if (fixture_case->alloc_fail) return NULL;
    ptr = malloc(size);
    if (!ptr || owned_count >= sizeof(owned) / sizeof(owned[0])) abort();
    owned[owned_count++] = ptr;
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
static int GetComputerNameExA(int kind, char *buffer, DWORD *size)
{
    static const char fqdn[] = "machine.invalid";
    assert(kind == ComputerNamePhysicalDnsFullyQualified);
    if (!buffer || *size < sizeof(fqdn))
    {
        *size = sizeof(fqdn);
        SetLastError(ERROR_MORE_DATA);
        return 0;
    }
    memcpy(buffer, fqdn, sizeof(fqdn));
    return 1;
}
static void lstrcpynA(char *dst, const char *src, int size)
{
    size_t count = strlen(src);
    if (count >= (size_t)size) count = size - 1;
    memcpy(dst, src, count);
    dst[count] = 0;
}
static struct addrinfo *make_info(int canonical)
{
    struct addrinfo *info = fixture_malloc(sizeof(*info));
    assert(info);
    memset(info, 0, sizeof(*info));
    if (canonical)
    {
        info->ai_canonname = fixture_malloc(sizeof("machine.invalid"));
        assert(info->ai_canonname);
        strcpy(info->ai_canonname, "machine.invalid");
    }
    return info;
}
static void freeaddrinfo(struct addrinfo *info)
{
    if (!info) return;
    fixture_free(info->ai_canonname);
    fixture_free(info);
}
static int do_getaddrinfo(const char *node, const char *service,
                          const struct addrinfo *hints, struct addrinfo **info)
{
    int result;
    (void)node; (void)service; (void)hints;
    resolver_calls++;
    result = resolver_calls == 1 ? fixture_case->primary_error : fixture_case->fallback_error;
    /* Deliberately observable API state: the existing direct error return must
     * not acquire a new SetLastError() call while fixing memory ownership. */
    SetLastError(4242);
    if (!result) *info = make_info(resolver_calls > 1 && fixture_case->fallback_canon);
    return result;
}
static int dns_only_query(const char *node, const struct addrinfo *hints, struct addrinfo **info)
{
    (void)node; (void)hints;
    dns_calls++;
    SetLastError(4242);
    if (!fixture_case->primary_error) *info = make_info(0);
    return fixture_case->primary_error;
}
#define malloc fixture_malloc
#define free fixture_free
'''

MAIN = r'''
int main(void)
{
    static const struct resolver_case cases[] = {
        {"missing node and service", NULL, NULL, 0, 0, 0, 0, 11001, 11001, 0, 0, 0, 0},
        {"empty local hostname canonical rejection", "", "80", AI_CANONNAME, 11001, 0, 0, 11001, 4242, 2, 0, 0, 0},
        {"full local hostname canonical rejection", "machine.invalid", "80", AI_CANONNAME, 11001, 0, 0, 11001, 4242, 2, 0, 0, 0},
        {"short local hostname canonical rejection", "machine", "80", AI_CANONNAME, 11001, 0, 0, 11001, 4242, 2, 0, 0, 0},
        {"local hostname canonical success", "machine.invalid", "80", AI_CANONNAME, 11001, 0, 1, 0, 0, 2, 1, 0, 0},
        {"local hostname ordinary fallback", "machine.invalid", "80", 0, 11001, 0, 0, 0, 0, 2, 1, 0, 0},
        {"local hostname no hints fallback", "machine.invalid", "80", 0, 11001, 0, 0, 0, 0, 2, 1, 1, 0},
        {"local hostname fallback failure", "machine.invalid", "80", AI_CANONNAME, 11001, 11001, 0, 11001, 11001, 2, 0, 0, 0},
        {"foreign hostname failure", "other.invalid", "80", AI_CANONNAME, 11001, 0, 0, 11001, 11001, 1, 0, 0, 0},
        {"bracketed IPv6 success", "[::1]:443", "80", 0, 0, 0, 0, 0, 0, 1, 1, 0, 0},
        {"bracketed IPv6 failure", "[::1]:443", "80", 0, 11001, 0, 0, 11001, 11001, 1, 0, 0, 0},
        {"numeric host failure", "[::1]", "80", AI_NUMERICHOST, 11001, 0, 0, 11001, 11001, 1, 0, 0, 0},
        {"DNS-only branch", "other.invalid", NULL, AI_DNS_ONLY, 0, 0, 0, 0, 0, 0, 1, 0, 0},
        {"null host with service", NULL, "80", 0, 0, 0, 0, 0, 0, 1, 1, 0, 0},
        {"hostname allocation failure", "", "80", AI_CANONNAME, 11001, 0, 0, 8, 234, 0, 0, 0, 1},
        {"IPv6 allocation failure", "[::1]", "80", 0, 0, 0, 0, 8, 9001, 0, 0, 0, 1},
    };
    unsigned i, repeat, failed = 0;
    for (repeat = 0; repeat < 128; repeat++)
    {
        for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
        {
            struct addrinfo hints = {0}, *info = NULL;
            int result;
            fixture_case = &cases[i];
            assert(!owned_count);
            resolver_calls = dns_calls = 0;
            last_error = 9001;
            hints.ai_flags = fixture_case->flags;
            result = getaddrinfo(fixture_case->node, fixture_case->service,
                                 fixture_case->null_hints ? NULL : &hints, &info);
            if (result != fixture_case->expected_result || last_error != (DWORD)fixture_case->expected_last ||
                resolver_calls != (unsigned)fixture_case->expected_calls || !!info != fixture_case->expected_info ||
                dns_calls != (unsigned)(fixture_case->flags == AI_DNS_ONLY))
            {
                fprintf(stderr, "%s: result/last-error/calls/info differs (%d/%u/%u/%d)\n",
                        fixture_case->label, result, last_error, resolver_calls, !!info);
                failed++;
            }
            freeaddrinfo(info);
            if (owned_count)
            {
                fprintf(stderr, "%s: %u owned allocation(s) remain\n", fixture_case->label, owned_count);
                failed++;
            }
            /* Release intentional negative-control leaks after recording them. */
            while (owned_count) fixture_free(owned[0]);
        }
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
    helper = extract((args.wine_source / "dlls/ws2_32/protocol.c").read_text())
    if args.source_only:
        print("Wine resolver ownership source contract passed; execution not requested")
        return 0
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("resolver fixture compilation/execution requires GitHub Actions on Linux; use --source-only locally")
    with tempfile.TemporaryDirectory(prefix="gluon-resolver-ownership-") as temp:
        directory = Path(temp)
        result = run_fixture(helper, directory, "resolver-ownership", sanitize=True)
        if result.returncode:
            raise ValueError("resolver ownership fixture failed:\n" + result.stderr)
        if args.self_test:
            mutants = (
                ("old-canonical-rejection", helper.replace(REJECT_CLEANUP, "                return EAI_NONAME;"),
                 "owned allocation(s) remain"),
                ("changed-last-error", helper.replace("                return EAI_NONAME;",
                 "                SetLastError( EAI_NONAME ); return EAI_NONAME;"), "last-error"),
            )
            for name, mutant, marker in mutants:
                if mutant == helper:
                    raise ValueError("resolver negative control did not change source: " + name)
                result = run_fixture(mutant, directory, name)
                if result.returncode != 1 or marker not in result.stderr:
                    raise ValueError("resolver fixture missed its negative control: " + name + "\n" + result.stderr)
    print("Wine resolver passed 16 cases repeated 128 times under ASan/UBSan/LSan%s" %
          ("; original leak and changed last-error behavior rejected" if args.self_test else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("resolver ownership check: %s" % error, file=sys.stderr)
        sys.exit(1)

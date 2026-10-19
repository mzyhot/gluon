#!/usr/bin/env python3
"""Run the patched Proton helper's exact launch and library-file ownership paths.

Windows API calls are stubbed; production allocation, goto, cleanup, and COM
ownership code is extracted unchanged. Compilation/execution is cloud-only.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def extract(source):
    patterns = [
        r"^static WCHAR \*find_quote\(.*?^\}",
        r"^static BOOL streq_niw\(.*?^\}",
        r"^static WCHAR\* get_end_of_excutable_name\(.*?^\}",
        r"^static BOOL should_use_shell_execute\(.*?^\}",
        r"^static HANDLE run_process\(.*?^\}",
        r"^static char \*escape_path_unix_to_dos\(.*?^\}",
        r"^size_t strappend\(.*?^\}",
        r"^static void setup_steam_files\(.*?^\}",
    ]
    parts = []
    for pattern in patterns:
        found = re.findall(pattern, source, re.MULTILINE | re.DOTALL)
        if len(found) != 1:
            raise ValueError("expected one Steam helper source section: " + pattern)
        parts.append(found[0])
    for needle in ("free(owned_cmdline);", "free(scratchA);", "free(scratchW);",
                   "HeapFree(GetProcessHeap(), 0, dos);", "if (SUCCEEDED(com_result)) CoUninitialize();"):
        if needle not in parts[4]:
            raise ValueError("missing launch cleanup: " + needle)
    if "free( buf );" not in parts[-1]:
        raise ValueError("missing library-file buffer cleanup")
    for target in ("create_steam_windows, NULL", "steam_drm_thread, child"):
        pattern = r"if \([^\n]*CreateThread\(NULL, 0, " + re.escape(target) + r", 0, NULL\)\)\)\s+CloseHandle\(thread\);"
        if not re.search(pattern, source):
            raise ValueError("detached thread handle is not closed: " + target)
    return "\n\n".join(parts)


PRELUDE = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <wchar.h>
#include <wctype.h>
typedef wchar_t WCHAR;
typedef int BOOL, HRESULT;
typedef unsigned int UINT;
typedef unsigned long DWORD;
typedef unsigned char BYTE;
typedef uintptr_t DWORD_PTR;
typedef intptr_t INT_PTR;
typedef void *HANDLE, *HDESK, *SC_HANDLE, *HKEY;
typedef struct { DWORD cb, dwFlags; unsigned short wShowWindow; } STARTUPINFOW;
typedef struct { HANDLE hProcess, hThread; } PROCESS_INFORMATION;
typedef struct { int unused; } SHFILEINFOW;
typedef struct { DWORD dwCurrentState; } SERVICE_STATUS;
#define TRUE 1
#define FALSE 0
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define CREATE_UNICODE_ENVIRONMENT 1024
#define CREATE_NEW_CONSOLE 16
#define CP_UNIXCP 65010
#define CP_UTF8 65001
#define SHGFI_EXETYPE 8192
#define E_FAIL (-1)
#define SUCCEEDED(result) ((result) >= 0)
#define HIWORD(value) (((value) >> 16) & 65535)
#define KEY_ALL_ACCESS 0
#define REG_DWORD 0
#define SERVICES_ACTIVE_DATABASEW L"ServicesActive"
#define SERVICE_QUERY_STATUS 0
#define SERVICE_STOP_PENDING 3
#define SERVICE_STOPPED 1
#define SW_HIDE 0
#define SW_SHOWNORMAL 1
#define STARTF_USESHOWWINDOW 1
#define SE_ERR_FNF 2
#define SE_ERR_NOASSOC 31
#define INSTALLSTATE_DEFAULT 0
#define ERROR_ALREADY_EXISTS 183
#define WINE_ERR(...) ((void)0)
#define WINE_TRACE(...) ((void)0)
#define TRACE(...) ((void)0)
#define ERR(...) ((void)0)
#define WARN(...) ((void)0)
#define strchrW wcschr
#define GetThreadDesktop(...) NULL
#define GetCurrentThreadId(...) 1
#define SetUserObjectInformationA(...) 1
#define RegOpenKeyExW(...) 1
#define RegQueryValueExW(...) 1
#define RegSetValueExW(...) 0
#define RegCloseKey(...) 0
#define OpenSCManagerW(...) NULL
#define OpenServiceW(...) NULL
#define QueryServiceStatus(...) 0
#define DeleteFileW(...) 0
#define CloseServiceHandle(...) 0
#define SetConsoleCtrlHandler(...) 1
#define MsiConfigureProductExW(...) 0
#define RegDeleteTreeW(...) 0
#define try_recover_eadesktop_symlink(...) 0
static WCHAR command[1024];
static int fail_allocation, allocation_index, conversion_failure, dos_failure;
static int create_failure, fixup_copy, shell_failure, hide_window;
static HRESULT com_status;
static unsigned com_acquired, com_released, thread_closed, process_closed, launched, written;
static DWORD last_error;
struct owned_ptr { void *ptr; int heap; };
static struct owned_ptr owned[64];
static unsigned owned_count;
static void *allocate(size_t size, int heap)
{
    void *ptr;
    if (++allocation_index == fail_allocation) return NULL;
    assert(owned_count < sizeof(owned) / sizeof(owned[0]));
    ptr = malloc(size);
    assert(ptr);
    owned[owned_count++] = (struct owned_ptr){ptr, heap};
    return ptr;
}
static void release(void *ptr, int heap)
{
    unsigned i;
    if (!ptr) return;
    for (i = 0; i < owned_count; ++i)
    {
        if (owned[i].ptr != ptr) continue;
        assert(owned[i].heap == heap);
        free(ptr);
        owned[i] = owned[--owned_count];
        last_error = 999;
        return;
    }
    fprintf(stderr, "wrong allocator, unowned pointer, or duplicate free\n");
    abort();
}
static void *fixture_malloc(size_t size) { return allocate(size, 0); }
static void fixture_free(void *ptr) { release(ptr, 0); }
static void *fixture_realloc(void *ptr, size_t size)
{
    unsigned i;
    void *next;
    if (!ptr) return fixture_malloc(size);
    for (i = 0; i < owned_count; ++i)
        if (owned[i].ptr == ptr && !owned[i].heap)
        {
            next = realloc(ptr, size);
            assert(next);
            owned[i].ptr = next;
            return next;
        }
    abort();
}
static BOOL HeapFree(HANDLE heap, DWORD flags, void *ptr)
{
    (void)heap; (void)flags;
    release(ptr, 1);
    return TRUE;
}
static HANDLE GetProcessHeap(void) { return (HANDLE)100; }
static WCHAR *GetCommandLineW(void) { return command; }
static DWORD GetLastError(void) { return last_error; }
static void SetLastError(DWORD value) { last_error = value; }
static HRESULT CoInitialize(void *reserved)
{
    (void)reserved;
    if (SUCCEEDED(com_status)) ++com_acquired;
    return com_status;
}
static void CoUninitialize(void) { ++com_released; last_error = 998; }
static BOOL env_nonzero(const char *name) { (void)name; return hide_window; }
static BOOL CloseHandle(HANDLE handle)
{
    if (handle == (HANDLE)10) ++thread_closed;
    else if (handle == (HANDLE)11) ++process_closed;
    else abort();
    return TRUE;
}
static int WideCharToMultiByte(UINT codepage, DWORD flags, const WCHAR *input,
                                int count, char *out, int size, void *a, void *b)
{
    int length = count == -1 ? (int)wcslen(input) + 1 : count;
    (void)codepage; (void)flags; (void)size; (void)a; (void)b;
    if (!out && conversion_failure == 1) return 0;
    if (out && conversion_failure == 2) return 0;
    if (out) for (int i = 0; i < length; ++i) out[i] = input[i];
    return length;
}
static WCHAR *wine_get_dos_file_name(const char *input)
{
    const WCHAR *path = strstr(input, ".txt") ? L"Z:\\fixture.txt" : L"Z:\\fixture.exe";
    size_t size = (wcslen(path) + 1) * sizeof(WCHAR);
    WCHAR *result;
    if (dos_failure || !(result = allocate(size, 1))) return NULL;
    memcpy(result, path, size);
    return result;
}
static DWORD_PTR SHGetFileInfoW(const WCHAR *path, DWORD attr, SHFILEINFOW *info, UINT size, UINT flags)
{
    (void)attr; (void)info; (void)size; (void)flags;
    assert(path);
    return 1;
}
static INT_PTR ShellExecuteW(void *window, const WCHAR *verb, WCHAR *path,
                            WCHAR *params, void *directory, int show)
{
    (void)window; (void)verb; (void)params; (void)directory; (void)show;
    assert(path && *path);
    ++launched;
    return shell_failure ? 2 : 42;
}
static WCHAR *fixup_cmdline_paths(WCHAR *input)
{
    WCHAR *copy;
    if (!fixup_copy) return input;
    copy = fixture_malloc((wcslen(input) + 1) * sizeof(WCHAR));
    assert(copy);
    wcscpy(copy, input);
    return copy;
}
static BOOL CreateProcessW(void *app, WCHAR *cmdline, void *a, void *b, BOOL inherit,
                           DWORD flags, void *env, void *cwd, STARTUPINFOW *si, PROCESS_INFORMATION *pi)
{
    (void)app; (void)a; (void)b; (void)inherit; (void)flags; (void)env; (void)cwd;
    assert(cmdline && *cmdline);
    assert(!hide_window || (si->dwFlags & STARTF_USESHOWWINDOW));
    ++launched;
    last_error = create_failure ? 123 : 0;
    if (create_failure) return FALSE;
    pi->hProcess = (HANDLE)11;
    pi->hThread = (HANDLE)10;
    return TRUE;
}
static BOOL CreateDirectoryW(const WCHAR *path, void *unused)
{ (void)path; (void)unused; return TRUE; }
static void lstrcpynA(char *dst, const char *src, int length)
{ assert(length > 0); memcpy(dst, src, length - 1); dst[length - 1] = 0; }
static void write_file(const WCHAR *path, const char *data, size_t size)
{
    (void)path;
    assert(size && strstr(data, "LibraryFolders") && data[size - 1] == '\n');
    ++written;
}
#define malloc fixture_malloc
#define realloc fixture_realloc
#define free fixture_free
'''


HARNESS = r'''
static int check_clean(const char *label)
{
    if (owned_count)
    {
        fprintf(stderr, "%s: %u owned allocation(s) remain\n", label, owned_count);
        return 1;
    }
    if (com_acquired != com_released)
    {
        fprintf(stderr, "%s: unmatched COM initialization\n", label);
        return 1;
    }
    assert(!process_closed);
    return 0;
}
static int launch_case(const char *label, const WCHAR *input, int allocation_fail,
                       int conversion_fail, int dos_fail, int process_fail,
                       int fixup, int shell_fail, HRESULT status)
{
    HANDLE process;
    BOOL await = 42;
    wcscpy(command, input);
    allocation_index = com_acquired = com_released = thread_closed = process_closed = launched = 0;
    fail_allocation = allocation_fail;
    conversion_failure = conversion_fail;
    dos_failure = dos_fail;
    create_failure = process_fail;
    fixup_copy = fixup;
    shell_failure = shell_fail;
    com_status = status;
    last_error = 0;
    process = run_process(&await, TRUE);
    if (wcscmp(input, L"steam.exe"))
    {
        assert(launched == 1);
        if (wcsstr(input, L".exe") && !wcsstr(input, L".txt"))
        {
            assert(await);
            assert(process == (process_fail ? INVALID_HANDLE_VALUE : (HANDLE)11));
            assert(thread_closed == (process_fail ? 0 : 1));
            if (process_fail) assert(last_error == 123);
        }
        else { assert(!await); assert(process == INVALID_HANDLE_VALUE); }
    }
    else { assert(!launched && !await && process == INVALID_HANDLE_VALUE); }
    return check_clean(label);
}
int main(void)
{
    for (unsigned iteration = 0; iteration < 64; ++iteration)
    {
        if (launch_case("direct", L"steam.exe C:\\fixture.exe --arg", 0, 0, 0, 0, 0, 0, 0)) return 1;
        if (launch_case("unix", L"steam.exe /fixture.exe --arg", 0, 0, 0, 0, 0, 0, 0)) return 1;
        if (launch_case("quoted unix", L"\"steam.exe\" \"/fixture.exe\" --arg", 0, 0, 0, 0, 0, 0, 1)) return 1;
        if (launch_case("create failed", L"steam.exe /fixture.exe", 0, 0, 0, 1, 0, 0, 0)) return 1;
        if (launch_case("fixed path failed", L"steam.exe /fixture.exe", 0, 0, 0, 1, 1, 0, 0)) return 1;
        if (launch_case("fixed path", L"steam.exe /fixture.exe", 0, 0, 0, 0, 1, 0, 0)) return 1;
        if (launch_case("COM unavailable", L"steam.exe /fixture.exe", 0, 0, 0, 0, 0, 0, -2)) return 1;
        if (launch_case("shell", L"steam.exe /fixture.txt --arg", 0, 0, 0, 0, 0, 0, 0)) return 1;
        if (launch_case("shell failed", L"steam.exe /fixture.txt", 0, 0, 0, 0, 0, 1, 0)) return 1;
        if (launch_case("conversion size", L"steam.exe /fixture.exe", 0, 1, 0, 0, 0, 0, 0)) return 1;
        if (launch_case("conversion result", L"steam.exe /fixture.exe", 0, 2, 0, 0, 0, 0, 0)) return 1;
        if (launch_case("DOS path failed", L"steam.exe /fixture.exe", 0, 0, 1, 0, 0, 0, 0)) return 1;
        for (int allocation = 1; allocation <= 4; ++allocation)
            if (launch_case("allocation failed", L"steam.exe /fixture.exe", allocation, 0, 0, 0, 0, 0, 0)) return 1;
        if (launch_case("invalid command", L"steam.exe", 0, 0, 0, 0, 0, 0, 0)) return 1;
        hide_window = 1;
        if (launch_case("hidden", L"steam.exe /fixture.exe", 0, 0, 0, 0, 0, 0, 0)) return 1;
        hide_window = 0;
        fail_allocation = conversion_failure = dos_failure = 0;
        setenv("STEAM_COMPAT_CLIENT_INSTALL_PATH", "/fixture/steam", 1);
        setenv("STEAM_COMPAT_LIBRARY_PATHS", "/fixture/SteamLibrary/steamapps:/fixture/OtherLibrary/steamapps", 1);
        setup_steam_files();
        if (check_clean("library files")) return 1;
    }
    assert(written == 64);
    puts("Steam helper ownership passed 64 restart cycles and 1216 launch/file cases");
    return 0;
}
'''


def run_fixture(source, directory, name, sanitize=False):
    path = directory / (name + ".c")
    binary = directory / name
    path.write_text(PRELUDE + "\n" + source + "\n" + HARNESS)
    command = ["clang", "-std=c11", "-g", "-O1", "-Wno-unused-value", str(path), "-o", str(binary)]
    if sanitize:
        command[1:1] = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return subprocess.run([str(binary)], capture_output=True, text=True,
                          env={**os.environ, "ASAN_OPTIONS": "detect_leaks=1", "UBSAN_OPTIONS": "halt_on_error=1"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="patched pinned steam_helper/steam.c")
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    helper = extract(args.source.read_text())
    if args.source_only:
        print("Steam helper ownership source extraction passed")
        return 0
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("compilation/execution requires GitHub Actions on Linux")
    with tempfile.TemporaryDirectory(prefix="gluon-steam-helper-") as temp:
        directory = Path(temp)
        result = run_fixture(helper, directory, "ownership", sanitize=True)
        if result.returncode:
            raise ValueError("Steam helper ownership fixture failed:\n" + result.stderr)
        print(result.stdout.strip())
        if args.self_test:
            cleanups = ("free(owned_cmdline);", "free(scratchA);", "free(scratchW);",
                        "HeapFree(GetProcessHeap(), 0, dos);", "free( buf );",
                        "if (SUCCEEDED(com_result)) CoUninitialize();",
                        "if (new_cmdline != cmdline) free(new_cmdline);")
            for index, cleanup in enumerate(cleanups):
                mutant = helper.replace(cleanup, "/* omitted cleanup negative control */", 1)
                if mutant == helper:
                    raise ValueError("negative control did not change source: " + cleanup)
                result = run_fixture(mutant, directory, "missing-" + str(index))
                if result.returncode != 1 or not any(text in result.stderr for text in
                        ("owned allocation(s) remain", "unmatched COM initialization")):
                    raise ValueError("missed omitted-cleanup negative control: " + cleanup + "\n" + result.stderr)
            print("All seven omitted-cleanup negative controls were detected")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        print("Steam helper ownership check failed:\n" + (error.stderr or str(error)), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError) as error:
        print("Steam helper ownership check: " + str(error), file=sys.stderr)
        sys.exit(1)

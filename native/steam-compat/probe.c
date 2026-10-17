/* This dylib is a developer probe, not an enabled compatibility layer. */
#include "resolver.h"

#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach-o/dyld.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int report_fd = STDERR_FILENO;
static atomic_uint inspected_images;

static int read_self(void *unused, uint64_t address, void *dest, size_t size)
{
    mach_vm_size_t copied = 0;
    (void)unused;
    return mach_vm_read_overwrite(mach_task_self(), (mach_vm_address_t)address,
                                 (mach_vm_size_t)size, (mach_vm_address_t)(uintptr_t)dest,
                                 &copied) == KERN_SUCCESS && copied == size;
}

static void inspect_image(const struct mach_header *header, intptr_t slide)
{
    Dl_info info;
    const char *name;
    struct gluon_compat_image image;
    struct gluon_compat_report report;
    size_t i;
    char line[1024];
    int used;
    (void)slide;
    if (!dladdr(header, &info) || !info.dli_fname) return;
    name = strrchr(info.dli_fname, '/');
    name = name ? name + 1 : info.dli_fname;
    if (strcmp(name, "steamclient.dylib") && strcmp(name, "steamclient64.dylib")) return;
    /* Also bounds repeated unload/reload of a matching image. */
    if (atomic_fetch_add(&inspected_images, 1) >= 4) return;
    image.read = read_self; image.context = NULL;
    image.header_address = (uint64_t)(uintptr_t)header;
    report = gluon_compat_inspect(&image);
    used = snprintf(line, sizeof(line), "{\"component\":\"steam-compat-probe\","
                    "\"activation\":\"disabled\",\"discovery\":\"%s\",\"anchors\":{",
                    gluon_compat_discovery_name(report.discovery));
    if (used < 0 || (size_t)used >= sizeof(line)) return;
    for (i = 0; i < GLUON_COMPAT_ANCHOR_COUNT; ++i) {
        int n = snprintf(line + used, sizeof(line) - (size_t)used,
                         "%s\"%s\":{\"strings\":%u,\"references\":%u}", i ? "," : "",
                         gluon_compat_anchor_name(i), report.anchors[i].strings,
                         report.anchors[i].references);
        if (n < 0 || (size_t)n >= sizeof(line) - (size_t)used) return;
        used += n;
    }
    if ((size_t)used + 4 >= sizeof(line)) return;
    memcpy(line + used, "}}\n", 4);
    /* One small append; no file names, image addresses, or library details. */
    (void)write(report_fd, line, (size_t)used + 3);
}

__attribute__((constructor)) static void start_probe(void)
{
    const char *flag = getenv("GLUON_STEAM_COMPAT_PROBE");
    const char *path = getenv("GLUON_STEAM_COMPAT_LOG");
    int requested = flag && strcmp(flag, "1") == 0;
    char log_path[PATH_MAX];
    log_path[0] = '\0';
    if (path && path[0] == '/' && strlen(path) < sizeof(log_path))
        memcpy(log_path, path, strlen(path) + 1);
    /* Do this before calling dyld or Steam can spawn a child. The Python probe
     * launcher supplies an isolated insertion list. Steam remains free to set
     * its own later overlay environment. Nothing is persisted in Steam.app. */
    unsetenv("DYLD_INSERT_LIBRARIES");
    unsetenv("GLUON_STEAM_COMPAT_PROBE");
    unsetenv("GLUON_STEAM_COMPAT_LOG");
    if (!requested || strcmp(getprogname(), "steam_osx")) return;
    if (log_path[0]) {
        struct stat st;
        int fd = open(log_path, O_WRONLY | O_APPEND | O_NOFOLLOW | O_CLOEXEC);
        if (fd >= 0) {
            if (!fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_uid == geteuid() &&
                st.st_nlink == 1 && !(st.st_mode & 077)) report_fd = fd;
            else close(fd);
        }
    }
    {
        static const char message[] = "{\"component\":\"steam-compat-probe\","
            "\"activation\":\"disabled\",\"reason\":\"hook-abi-unverified\","
            "\"discovery\":\"waiting-for-steamclient\"}\n";
        (void)write(report_fd, message, sizeof(message) - 1);
    }
    _dyld_register_func_for_add_image(inspect_image);
}

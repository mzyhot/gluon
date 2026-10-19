// Gluon's global policy is independent of Steam's explicit per-game mappings.
#include "gluon_settings.h"
#include "nativeplatform.h"
#include "../util/file.h"
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static atomic_int g_mac32 = -1;
static atomic_int g_effective_mac32 = -1;
static pthread_mutex_t g_settings_lock = PTHREAD_MUTEX_INITIALIZER;

static int preference_path(char *path, size_t cap) {
    const char *home = np_home_dir();
    if (!home) return 0;
    np_support_path(path, cap, home, "auto-mac32");
    return path[0] && strlen(path) + 16 < cap;
}

bool gluon_settings_mac32(void) {
    int value = atomic_load(&g_mac32);
    if (value >= 0) return value != 0;
    pthread_mutex_lock(&g_settings_lock);
    if ((value = atomic_load(&g_mac32)) < 0) {
        char path[PATH_MAX], bytes[3] = {0};
        int fd = preference_path(path, sizeof path) ? open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC) : -1;
        ssize_t len = fd >= 0 ? read(fd, bytes, sizeof bytes) : -1;
        if (fd >= 0) close(fd);
        value = len == 2 && bytes[0] == '1' && bytes[1] == '\n';
        atomic_store(&g_mac32, value);
    }
    pthread_mutex_unlock(&g_settings_lock);
    return value != 0;
}

bool gluon_settings_mac32_effective(void) {
    int value = atomic_load(&g_effective_mac32);
    if (value >= 0) return value != 0;
    int initial = gluon_settings_mac32(), unset = -1;
    atomic_compare_exchange_strong(&g_effective_mac32, &unset, initial);
    return atomic_load(&g_effective_mac32) != 0;
}

int gluon_settings_command(uint32_t appid, const char *name) {
    if (appid || !name || (strcmp(name, GLUON_MAC32_ON) && strcmp(name, GLUON_MAC32_OFF))) return 0;
    // Freeze launch policy before persisting a new preference. Steam caches app
    // availability across CEF windows, so a restart applies it consistently.
    (void)gluon_settings_mac32_effective();
    int enabled = !strcmp(name, GLUON_MAC32_ON), ok = 0;
    char path[PATH_MAX], temporary[PATH_MAX];
    pthread_mutex_lock(&g_settings_lock);
    if (preference_path(path, sizeof path)) {
        snprintf(temporary, sizeof temporary, "%s.XXXXXX", path);
        int fd = mkstemp(temporary);
        if (fd >= 0) {
            const char *value = enabled ? "1\n" : "0\n";
            int written = write(fd, value, 2) == 2 && fsync(fd) == 0;
            int closed = close(fd) == 0;
            if (written && closed && rename(temporary, path) == 0) {
                atomic_store(&g_mac32, enabled);
                ok = 1;
            }
            unlink(temporary);
        }
    }
    pthread_mutex_unlock(&g_settings_lock);
    return ok ? 1 : -1;
}

bool gluon_settings_auto_app(uint32_t appid) {
    return appid && gluon_settings_mac32_effective() && np_nativeplatform_is_mac32(appid);
}

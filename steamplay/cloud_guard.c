/* Native AutoCloud preparation boundary. No additional Steam offsets or hooks.
 * Steam's Cloud root resolver asks FindToolForTargetApp before returning a path.
 * Refusing the tool here prevents access to an unprepared Windows save root.
 */
#include "cloud_guard.h"
#include "../util/file.h"
#include "../util/log.h"
#include "../../vendor/cJSON.h"
#include <crt_externs.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static pthread_mutex_t guard_lock = PTHREAD_MUTEX_INITIALIZER;
static cJSON *catalogue;
static cJSON *failures;
static uint32_t failed_app;
static long long failed_until;

static long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int helper(const char *operation, const char *appid) {
    const char *home = np_home_dir();
    if (!home) return -1;
    char support[1024], pythonpath[1200], gluonhome[1200], steamroot[1200], log[1200];
    const char *configured = getenv("GLUON_HOME");
    if (configured && configured[0]) snprintf(support, sizeof(support), "%s", configured);
    else snprintf(support, sizeof(support), "%s/Library/Application Support/Gluon", home);
    if (snprintf(pythonpath, sizeof(pythonpath), "PYTHONPATH=%s/lib", support) >= (int)sizeof(pythonpath) ||
        snprintf(gluonhome, sizeof(gluonhome), "GLUON_HOME=%s", support) >= (int)sizeof(gluonhome) ||
        snprintf(log, sizeof(log), "%s/steamplay/cloud.log", support) >= (int)sizeof(log)) return -1;
    const char *root = getenv("GLUON_STEAM_ROOT");
    if (root && root[0]) {
        if (snprintf(steamroot, sizeof(steamroot), "STEAM_ROOT=%s", root) >= (int)sizeof(steamroot)) return -1;
    } else if (snprintf(steamroot, sizeof(steamroot), "STEAM_ROOT=%s/Library/Application Support/Steam", home) >= (int)sizeof(steamroot)) return -1;
    char *argv[] = { "/usr/bin/env", "-u", "DYLD_INSERT_LIBRARIES", pythonpath,
        gluonhome, steamroot, "PYTHONDONTWRITEBYTECODE=1", "/usr/bin/arch", "-arm64",
        "/usr/bin/python3", "-m", "gluon.cloud", (char *)operation, (char *)appid, NULL };
    if (!appid) argv[13] = NULL;
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attr;
    if (posix_spawn_file_actions_init(&actions)) return -1;
    if (posix_spawnattr_init(&attr)) { posix_spawn_file_actions_destroy(&actions); return -1; }
    sigset_t none, all;
    sigemptyset(&none); sigfillset(&all);
    int error = posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
    error |= posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, "/dev/null", O_WRONLY, 0);
    error |= posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, log, O_WRONLY | O_CREAT | O_APPEND, 0600);
    error |= posix_spawnattr_setsigmask(&attr, &none);
    error |= posix_spawnattr_setsigdefault(&attr, &all);
    error |= posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_CLOEXEC_DEFAULT);
    pid_t child = -1;
    if (!error) error = posix_spawn(&child, argv[0], &actions, &attr, argv, *_NSGetEnviron());
    posix_spawnattr_destroy(&attr); posix_spawn_file_actions_destroy(&actions);
    if (error) return -1;
    long long deadline = now_ms() + 30000;
    int status = 0;
    for (;;) {
        pid_t done = waitpid(child, &status, WNOHANG);
        if (done == child) return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
        if (done < 0 && errno != EINTR) return -1;
        if (now_ms() >= deadline) {
            kill(child, SIGKILL);
            while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
            return -1;
        }
        poll(NULL, 0, 10);
    }
}

static int paths_unchanged(cJSON *watch) {
    if (!cJSON_IsObject(watch) || !watch->child) return 0;
    for (cJSON *p = watch->child; p; p = p->next) {
        if (!p->string || !cJSON_IsString(p)) return 0;
        struct stat st;
        char stamp[192];
        if (lstat(p->string, &st) != 0) {
            if (errno != ENOENT) return 0;
            strcpy(stamp, "-");
        } else {
            snprintf(stamp, sizeof(stamp), "%" PRIu64 ":%" PRIu64 ":%lld:%lld:%lld",
                (uint64_t)st.st_dev, (uint64_t)st.st_ino,
                (long long)st.st_mtimespec.tv_sec * 1000000000 + st.st_mtimespec.tv_nsec,
                (long long)st.st_ctimespec.tv_sec * 1000000000 + st.st_ctimespec.tv_nsec,
                (long long)st.st_size);
        }
        if (strcmp(stamp, p->valuestring)) return 0;
    }
    return 1;
}

static int unchanged(void) {
    return catalogue && paths_unchanged(cJSON_GetObjectItemCaseSensitive(catalogue, "watch"));
}

static int load_catalogue(void) {
    char path[1200];
    np_support_path(path, sizeof(path), np_home_dir(), "cloud-index.json");
    size_t length = 0;
    uint8_t *data = np_read_whole_file(path, &length);
    cJSON *next = data ? cJSON_ParseWithLength((char *)data, length) : NULL;
    free(data);
    cJSON *schema = cJSON_GetObjectItemCaseSensitive(next, "schema");
    if (!next || !cJSON_IsNumber(schema) || schema->valueint != 1 ||
        !cJSON_IsObject(cJSON_GetObjectItemCaseSensitive(next, "apps"))) {
        cJSON_Delete(next); return 0;
    }
    cJSON_Delete(catalogue); catalogue = next;
    return unchanged();
}

bool gluon_cloud_ready(uint32_t appid) {
    if (!appid) return true;
    char key[16]; snprintf(key, sizeof(key), "%u", appid);
    bool ready = false;
    pthread_mutex_lock(&guard_lock);
    if (!unchanged() && (helper("--index", NULL) || !load_catalogue())) goto out;
    cJSON *apps = cJSON_GetObjectItemCaseSensitive(catalogue, "apps");
    cJSON *app = cJSON_GetObjectItemCaseSensitive(apps, key);
    // Uninstalled games still need a compatibility tool for the Install button.
    // Directory/manifest stamps force a fresh catalogue when Steam installs them.
    if (!app) { ready = true; goto out; }
    if (!paths_unchanged(cJSON_GetObjectItemCaseSensitive(app, "watch"))) {
        if (helper("--index", NULL) || !load_catalogue()) goto out;
        app = cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(catalogue, "apps"), key);
        if (!app) goto out;
    }
    cJSON *failed = failures ? cJSON_GetObjectItemCaseSensitive(failures, key) : NULL;
    if (failed && paths_unchanged(failed)) goto out;
    if (failed) cJSON_DeleteItemFromObjectCaseSensitive(failures, key);
    if (cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(app, "ready"))) { ready = true; goto out; }
    if (failed_app == appid && now_ms() < failed_until) goto out;
    int result = helper("--automatic", key);
    if (result == 0) {
        int loaded = load_catalogue();
        app = cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(catalogue, "apps"), key);
        ready = loaded && app && cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(app, "ready"));
    }
    if (!ready) {
        // Retry busy Wine sessions, but do not repeatedly copy a failed backup
        // on each UI query. A changed layout or a Steam restart allows recovery.
        if (result != 75) {
            if (!failures) failures = cJSON_CreateObject();
            cJSON *watch = cJSON_GetObjectItemCaseSensitive(app, "watch");
            cJSON *copy = cJSON_Duplicate(watch, true);
            if (copy) cJSON_AddItemToObject(failures, key, copy);
        }
        failed_app = appid; failed_until = now_ms() + 1000;
        NP_ERR("Steam Cloud prefix refused for appID %u; see Gluon/steamplay/cloud.log", appid);
    }
out:
    pthread_mutex_unlock(&guard_lock);
    return ready;
}

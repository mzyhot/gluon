/* Cloud-only controlled steamclient.dylib. Never linked into Gluon packages. */
#include "../tools/gluon-steam-client.h"
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static char fixture_directory[PATH_MAX];
static int outstanding;

static int fixture_path(char *destination, size_t capacity, const char *name)
{
    int length = snprintf(destination, capacity, "%s/%s", fixture_directory, name);
    return length >= 0 && (size_t)length < capacity;
}

static void record(const char *event)
{
    char name[64], path[PATH_MAX];
    snprintf(name, sizeof(name), "%d.client-log", getpid());
    if (!fixture_path(path, sizeof(path), name)) _exit(70);
    int descriptor = open(path, O_CREAT | O_WRONLY | O_APPEND, 0600);
    if (descriptor < 0) _exit(70);
    size_t length = strlen(event);
    if (write(descriptor, event, length) != (ssize_t)length ||
        write(descriptor, "\n", 1) != 1 || close(descriptor)) _exit(70);
}

static int mode(const char *wanted)
{
    char path[PATH_MAX], value[64];
    if (!fixture_path(path, sizeof(path), "mode")) return 0;
    FILE *file = fopen(path, "r");
    if (!file) return 0;
    int count = fscanf(file, "%63s", value);
    fclose(file);
    return count == 1 && !strcmp(value, wanted);
}

__attribute__((constructor)) static void initialize_fixture(void)
{
    Dl_info information;
    if (!dladdr((void *)&initialize_fixture, &information) || !information.dli_fname ||
        !realpath(information.dli_fname, fixture_directory)) _exit(70);
    char *slash = strrchr(fixture_directory, '/');
    if (!slash) _exit(70);
    *slash = 0;
    record("loaded");
}

static int create_pipe(GluonSteamClient *client)
{
    (void)client;
    record("create-pipe");
    return mode("pipe-fail") ? 0 : 42;
}

static int8_t release_pipe(GluonSteamClient *client, int pipe)
{
    (void)client;
    if (pipe != 42 || outstanding) _exit(71);
    record("release-pipe");
    return 1;
}

static int connect_user(GluonSteamClient *client, int pipe)
{
    (void)client;
    if (pipe != 42) _exit(71);
    record("connect-user");
    return mode("user-fail") ? 0 : 84;
}

static int create_local_user(GluonSteamClient *client, int *pipe, int account_type)
{
    (void)client;
    (void)pipe;
    (void)account_type;
    _exit(71); /* The watcher must connect to the existing global user only. */
}

static void release_user(GluonSteamClient *client, int pipe, int user)
{
    (void)client;
    if (pipe != 42 || user != 84 || outstanding) _exit(71);
    record("release-user");
}

static const GluonSteamClientVTable fixture_vtable = {
    .createPipe = create_pipe,
    .releasePipe = release_pipe,
    .connectUser = connect_user,
    .createLocalUser = create_local_user,
    .releaseUser = release_user,
};
static GluonSteamClient fixture_client = { &fixture_vtable };

void *CreateInterface(const char *name, int *return_code)
{
    record("create-interface");
    if (return_code) *return_code = 0;
    if (strcmp(name, "SteamClient020") || mode("interface-fail")) return NULL;
    return &fixture_client;
}

#ifndef GLUON_FIXTURE_MISSING_CALLBACK
static int completed_sent;
static int shutdown_sent;

static int present(const char *name)
{
    char path[PATH_MAX];
    return fixture_path(path, sizeof(path), name) && !access(path, F_OK);
}

int8_t Steam_BGetCallback(int pipe, GluonSteamCallback *message, int *ignored)
{
    static unsigned char completion_payload[16];
    if (pipe != 42 || outstanding) _exit(71);
    if (ignored) *ignored = 0;
    if (mode("block-get"))
    {
        record("blocked-get");
        /* A native IPC call that never returns must not block Cocoa Stop. */
        while (!present("release-block")) usleep(10000);
    }
    int identifier = 0;
    if ((!completed_sent && present("completed")) || mode("flood"))
    {
        completed_sent = 1;
        identifier = 703; /* SteamAPICallCompleted_t is not a shutdown request. */
    }
    else if (!shutdown_sent && present("shutdown"))
    {
        shutdown_sent = 1;
        identifier = GLUON_STEAM_SHUTDOWN;
    }
    if (!identifier) return 0;
    message->user = 0; /* Steam shutdown can be a broadcast, not a user match. */
    message->identifier = identifier;
    message->data = identifier == 703 ? completion_payload : NULL;
    message->size = identifier == 703 ? (int)sizeof(completion_payload) : 1;
    outstanding = identifier;
    record(identifier == 703 ? "get:703" : "get:704");
    return 1;
}
#endif

int8_t Steam_FreeLastCallback(int pipe)
{
    if (pipe != 42 || !outstanding) _exit(71);
    record(outstanding == 703 ? "free:703" : "free:704");
    outstanding = 0;
    return 1;
}

void Steam_ReleaseThreadLocalMemory(bool thread_exit)
{
    (void)thread_exit;
    record("release-thread-memory");
}

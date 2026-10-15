/* Minimal native SteamClient020 ABI prefix used by the launcher. The interface
 * and callback layouts are defined by Valve's Steamworks headers shipped in
 * Proton. No Steam library or SDK implementation is bundled with Gluon.
 */
#ifndef GLUON_STEAM_CLIENT_H
#define GLUON_STEAM_CLIENT_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct GluonSteamClient GluonSteamClient;
typedef struct
{
    int (*createPipe)(GluonSteamClient *);
    int8_t (*releasePipe)(GluonSteamClient *, int);
    int (*connectUser)(GluonSteamClient *, int);
    int (*createLocalUser)(GluonSteamClient *, int *, int);
    void (*releaseUser)(GluonSteamClient *, int, int);
} GluonSteamClientVTable;
struct GluonSteamClient { const GluonSteamClientVTable *vtable; };

/* macOS uses Valve's small (four-byte) callback packing on both native ABIs. */
#pragma pack(push, 4)
typedef struct
{
    int user;
    int identifier;
    unsigned char *data;
    int size;
} GluonSteamCallback;
#pragma pack(pop)
_Static_assert(sizeof(GluonSteamCallback) == 20, "Steam callback size");
_Static_assert(offsetof(GluonSteamCallback, data) == 8, "Steam callback pointer");
_Static_assert(offsetof(GluonSteamCallback, size) == 16, "Steam callback length");

enum { GLUON_STEAM_SHUTDOWN = 704 };
typedef void *(*GluonSteamCreateInterface)(const char *, int *);
typedef int8_t (*GluonSteamGetCallback)(int, GluonSteamCallback *, int *);
typedef int8_t (*GluonSteamFreeCallback)(int);
typedef void (*GluonSteamReleaseThreadMemory)(bool);
#endif

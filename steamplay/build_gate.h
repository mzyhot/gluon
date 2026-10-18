/* Gluon GPL-3.0: accept only upstream-declared builds, then require signature resolution. */
#ifndef GLUON_BUILD_GATE_H
#define GLUON_BUILD_GATE_H
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned long long gluon_parse_build(const char *text) {
    const char *p = strstr(text, "\"version\"");
    if (!p) return 0;
    p += strlen("\"version\"");
    while (*p && isspace((unsigned char)*p)) p++;
    if (*p++ != '"' || !isdigit((unsigned char)*p)) return 0;
    char *end = NULL;
    unsigned long long value = strtoull(p, &end, 10);
    return end && *end == '"' ? value : 0;
}

static unsigned long long gluon_installed_build(const char *root) {
    static const char *bases[] = {"", "/Steam.AppBundle/Steam/Contents/MacOS"};
    static const char *names[] = {"steam_client_macos.installed", "steam_client_osx.installed",
        "steam_client_macos.manifest", "steam_client_osx.manifest",
        "steam_client_signed_osx.manifest", "steam_client_signed-2_osx.manifest"};
    unsigned long long build = 0;
    for (size_t b = 0; b < sizeof(bases)/sizeof(bases[0]); b++) {
        for (size_t i = 0; i < sizeof(names)/sizeof(names[0]); i++) {
            char path[2048], buf[65536];
            if (snprintf(path, sizeof(path), "%s%s/package/%s", root, bases[b], names[i]) >= (int)sizeof(path)) return 0;
            char *extension = strrchr(path, '.');
            if (extension && !strcmp(extension, ".manifest")) {
                char inventory_path[sizeof(path)];
                int length = snprintf(inventory_path, sizeof(inventory_path), "%.*s.installed",
                                      (int)(extension - path), path);
                if (length < 0 || length >= (int)sizeof(inventory_path)) return 0;
                FILE *inventory = fopen(inventory_path, "r");
                if (!inventory) continue;
                fclose(inventory);
            }
            FILE *f = fopen(path, "r");
            if (!f) continue;
            size_t got = fread(buf, 1, sizeof(buf)-1, f);
            int complete = feof(f);
            fclose(f);
            if (!complete) continue;
            buf[got] = 0;
            unsigned long long found = gluon_parse_build(buf);
            if (found > build) build = found;
        }
    }
    return build;
}

static int gluon_build_supported(const char *root, const char *policy) {
    unsigned long long build = gluon_installed_build(root);
    if (!build) return 0;
    FILE *f = fopen(policy, "r");
    if (!f) return 0;
    char line[128];
    int supported = 0;
    while (fgets(line, sizeof(line), f)) {
        char *end = NULL;
        unsigned long long allowed = strtoull(line, &end, 10);
        if (allowed == build && end && (*end == '\n' || *end == 0)) supported = 1;
    }
    fclose(f);
    return supported;
}
#endif

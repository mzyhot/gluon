/* Synthetic native boundary fixture. Never loads or starts Steam or Wine. */
#include "cloud_guard.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int main(void) {
    int cloud_tool, recovery_tool;
    const char *root = getenv("GLUON_STEAM_ROOT");
    const char *home = getenv("GLUON_HOME");
    assert(root && home);
    char view[2048], marker[2048], index[2048], manifest[2048];
    snprintf(view, sizeof(view), "%s/steamapps/compatdata/123/pfx", root);
    snprintf(marker, sizeof(marker), "%s/steamapps/compatdata/123/.gluon-native-cloud.json", root);
    snprintf(index, sizeof(index), "%s/steamplay/cloud-index.json", home);
    assert(gluon_cloud_ready(0));
    assert(gluon_cloud_ready(123));
    assert(gluon_cloud_select(123, &cloud_tool, &recovery_tool) == &cloud_tool);
    struct stat st, before, after;
    assert(lstat(view, &st) == 0 && S_ISLNK(st.st_mode));
    assert(stat(marker, &st) == 0 && S_ISREG(st.st_mode));
    assert(stat(index, &before) == 0);
    for (int i = 0; i < 100; i++) assert(gluon_cloud_ready(123));
    assert(stat(index, &after) == 0);
    assert(before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec &&
           before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec);
    // A foreign Proton prefix must be refused before its paths can be returned.
    assert(unlink(view) == 0 && mkdir(view, 0700) == 0);
    assert(!gluon_cloud_ready(123));
    assert(gluon_cloud_select(123, &cloud_tool, &recovery_tool) == &recovery_tool);
    assert(lstat(view, &st) == 0 && S_ISDIR(st.st_mode));
    // An uninstalled app keeps Install available. A newly created manifest
    // invalidates that result and prepares its Cloud view automatically.
    assert(gluon_cloud_ready(456));
    snprintf(manifest, sizeof(manifest), "%s/steamapps/appmanifest_456.acf", root);
    FILE *file = fopen(manifest, "w"); assert(file);
    assert(fputs("fixture", file) >= 0 && fclose(file) == 0);
    assert(gluon_cloud_ready(456));
    snprintf(view, sizeof(view), "%s/steamapps/compatdata/456/pfx", root);
    assert(lstat(view, &st) == 0 && S_ISLNK(st.st_mode));
    assert(gluon_cloud_select(789, &cloud_tool, &recovery_tool) == &cloud_tool);
    assert(gluon_cloud_select(790, &cloud_tool, &recovery_tool) == &cloud_tool);
    assert(gluon_cloud_select(791, &cloud_tool, &recovery_tool) == &cloud_tool);
    snprintf(view, sizeof(view), "%s/steamapps/compatdata/791/pfx", root);
    assert(lstat(view, &st) == 0 && S_ISLNK(st.st_mode));
    puts("native Cloud preflight: fresh, cached, conflicting, API, GameInstall, legacy and newly installed games passed");
    return 0;
}

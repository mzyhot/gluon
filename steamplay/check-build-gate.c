/* Synthetic build-policy fixtures; never reads an installed Steam client. */
#include "build_gate.h"
#include <assert.h>
#include <sys/stat.h>
#include <unistd.h>

int main(void) {
    assert(gluon_parse_build("\"Steam\" { \"version\" \"123\" }") == 123);
    assert(gluon_parse_build("\"version\" \"123bad\"") == 0);
    assert(gluon_parse_build("\"version\" 123") == 0);
    assert(gluon_parse_build("no version") == 0);
    char folder[] = "/tmp/gluon-build-policy-XXXXXX";
    assert(mkdtemp(folder));
    char package[1024], installed[1024], policy[1024];
    snprintf(package, sizeof(package), "%s/package", folder);
    assert(mkdir(package, 0700) == 0);
    snprintf(installed, sizeof(installed), "%s/steam_client_macos.installed", package);
    snprintf(policy, sizeof(policy), "%s/builds", folder);
    FILE *f = fopen(installed, "w"); assert(f);
    fputs("\"Steam\" { \"version\" \"123\" }", f); fclose(f);
    assert(!gluon_build_supported(folder, policy));
    f = fopen(policy, "w"); assert(f); fputs("456\n", f); fclose(f);
    assert(!gluon_build_supported(folder, policy));
    f = fopen(policy, "w"); assert(f); fputs("123\n456\n", f); fclose(f);
    assert(gluon_build_supported(folder, policy));
    char manifest[1024], inventory[1024];
    snprintf(manifest, sizeof(manifest), "%s/steam_client_signed-2_osx.manifest", package);
    snprintf(inventory, sizeof(inventory), "%s/steam_client_signed-2_osx.installed", package);
    f = fopen(manifest, "w"); assert(f); fputs("\"osx\" { \"version\" \"789\" }", f); fclose(f);
    assert(gluon_installed_build(folder) == 123);
    f = fopen(inventory, "w"); assert(f); fputs("fixture installed inventory", f); fclose(f);
    assert(gluon_installed_build(folder) == 789);
    assert(!gluon_build_supported(folder, policy));
    f = fopen(policy, "w"); assert(f); fputs("789\n", f); fclose(f);
    assert(gluon_build_supported(folder, policy));
    unlink(manifest); unlink(inventory);
    char long_root[2048];
    size_t boundary = sizeof(long_root) - 1 - strlen("/package/steam_client_signed-2_osx.manifest");
    memset(long_root, 'a', boundary);
    long_root[boundary] = 0;
    assert(gluon_installed_build(long_root) == 0);
    unlink(installed); unlink(policy); rmdir(package); rmdir(folder);
    puts("Build policy accepts declared versions and rejects unknown versions");
    return 0;
}

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
    unlink(installed); unlink(policy); rmdir(package); rmdir(folder);
    puts("Build policy accepts declared versions and rejects unknown versions");
    return 0;
}

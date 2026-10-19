// Real preference persistence, with classification supplied by a fixture catalog.
#include "gluon_settings.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int np_nativeplatform_is_mac32(unsigned appid) { return appid == 123; }
void np_nativeplatform_refresh(void) {}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (!strcmp(argv[1], "on")) {
        assert(!gluon_settings_mac32());
        assert(!gluon_settings_auto_app(123));
        assert(!gluon_settings_command(123, GLUON_MAC32_ON));
        assert(!gluon_settings_command(0, "gluon-proton"));
        assert(gluon_settings_command(0, GLUON_MAC32_ON) == 1);
        assert(gluon_settings_mac32());
        assert(!gluon_settings_auto_app(123)); // Restart applies the new policy.
        assert(!gluon_settings_auto_app(456) && !gluon_settings_auto_app(0));
    } else if (!strcmp(argv[1], "off")) {
        assert(gluon_settings_mac32()); // Loaded by a fresh process.
        assert(gluon_settings_auto_app(123));
        assert(gluon_settings_command(0, GLUON_MAC32_OFF) == 1);
        assert(!gluon_settings_mac32() && gluon_settings_auto_app(123)); // Effective until restart.
    } else if (!strcmp(argv[1], "read-off")) {
        assert(!gluon_settings_mac32());
    } else if (!strcmp(argv[1], "unwritable")) {
        assert(gluon_settings_command(0, GLUON_MAC32_ON) == -1);
        assert(!gluon_settings_mac32());
    } else abort();
    puts("Gluon Settings preference fixture passed");
    return 0;
}

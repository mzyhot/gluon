#ifndef GLUON_STEAMPLAY_SETTINGS_H
#define GLUON_STEAMPLAY_SETTINGS_H
#include <stdbool.h>
#include <stdint.h>
#define GLUON_MAC32_ON  "__gluon_settings_mac32_on_v1"
#define GLUON_MAC32_OFF "__gluon_settings_mac32_off_v1"
bool gluon_settings_mac32(void);
bool gluon_settings_mac32_effective(void);
// A reserved appid-zero command is consumed before the Steam mapping writer.
// 0: ordinary mapping, 1: preference saved, -1: preference could not be saved.
int gluon_settings_command(uint32_t appid, const char *name);
bool gluon_settings_auto_app(uint32_t appid);
#endif

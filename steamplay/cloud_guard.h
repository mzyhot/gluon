/* Per-app preflight, called before returning a Gluon tool to Steam. */
#ifndef GLUON_CLOUD_GUARD_H
#define GLUON_CLOUD_GUARD_H
#include <stdbool.h>
#include <stdint.h>
bool gluon_cloud_ready(uint32_t appid);
void *gluon_cloud_select(uint32_t appid, void *cloud_tool, void *recovery_tool);
#endif

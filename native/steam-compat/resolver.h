/* Gluon: independently implemented, read-only Steam compatibility discovery. */
#ifndef GLUON_STEAM_COMPAT_RESOLVER_H
#define GLUON_STEAM_COMPAT_RESOLVER_H

#include <stddef.h>
#include <stdint.h>

#define GLUON_COMPAT_ANCHOR_COUNT 3
#define GLUON_COMPAT_MAX_COMMAND_BYTES (1024u * 1024u)
#define GLUON_COMPAT_MAX_TEXT_BYTES (64u * 1024u * 1024u)
#define GLUON_COMPAT_MAX_STRING_BYTES (16u * 1024u * 1024u)

/* read must either copy the whole requested range or return zero. */
typedef int (*gluon_compat_read)(void *, uint64_t, void *, size_t);
struct gluon_compat_image {
    gluon_compat_read read;
    void *context;
    uint64_t header_address;
};

enum gluon_compat_discovery {
    GLUON_COMPAT_MALFORMED,
    GLUON_COMPAT_UNSUPPORTED,
    GLUON_COMPAT_UNREADABLE,
    GLUON_COMPAT_LIMIT,
    GLUON_COMPAT_MISSING,
    GLUON_COMPAT_AMBIGUOUS,
    GLUON_COMPAT_CANDIDATES
};

struct gluon_compat_anchor {
    unsigned strings; /* Saturated at two: zero, unique, or ambiguous. */
    unsigned references;
};

struct gluon_compat_report {
    enum gluon_compat_discovery discovery;
    struct gluon_compat_anchor anchors[GLUON_COMPAT_ANCHOR_COUNT];
};

/* This API never changes process memory, installs hooks, or returns callable
 * addresses. CANDIDATES means only one recognized reference for each anchor;
 * it does not validate a function boundary, ABI, or compatibility-manager flag. */
struct gluon_compat_report gluon_compat_inspect(const struct gluon_compat_image *);
const char *gluon_compat_discovery_name(enum gluon_compat_discovery);
const char *gluon_compat_anchor_name(size_t);
int gluon_compat_activation_supported(void);

/* Pure policy groundwork mirroring enable.candidates/runs_through_gluon.
 * The caller must provide owned, current, original (not fallback-modified)
 * metadata. A future hook must obtain and validate that metadata through Steam.
 * No Steam metadata reader or hook is implemented here. */
struct gluon_compat_app {
    uint32_t appid;
    int metadata_current;
    int in_library;
    const char *type;
    const char *oslist;
    const char *osextended;
};
int gluon_compat_app_eligible(const struct gluon_compat_app *);

#endif

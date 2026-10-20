# Native Steam Cloud integration

The earlier optional design was a conservative implementation choice, not a
requirement of native Steam Play. Gluon now uses automatic preparation and a
single public compatibility choice. It leaves Steam responsible for synchronization,
rule evaluation, user preferences and Cloud conflict decisions.

## Why the launcher is too late

[Valve documents synchronization before and after each game session](https://partner.steamgames.com/doc/features/cloud).
The local client logs also place AutoCloud's launch scan before Gluon's game
launcher. Validating or migrating a prefix only in `waitforexitandrun` cannot
protect the preceding synchronization.

The pinned [NotProton compatibility registration](https://github.com/NotProtonNot/NotProton/blob/8562a6320279224125a80cd66bf8d1af9b04bb2d/dylib/feats/compat.c)
explains that a tool identifier containing `proton` enables the client's Windows
Cloud roots. Its [profile setup](https://github.com/NotProtonNot/NotProton/blob/8562a6320279224125a80cd66bf8d1af9b04bb2d/dylib/feats/compat_run.sh)
also establishes that the shared profile must exist before Wine initialization.
Gluon's prior default identifier omitted `proton`, so those roots were absent.

## Preparation boundary

Read-only inspection of the installed supported client established that its
compatibility Cloud path resolver calls `FindToolForTargetApp` before reading
the tool's Windows roots and joining them to the per-game compatibility path.
A missing tool makes this path lookup fail. This is static control-flow evidence,
not an observed network synchronization or a universal claim about future clients.

Gluon already hooks that function through the pinned public signature resolver.
The guard prepares Windows-root games before returning the Cloud tool. On failure
it returns an internal non-Proton recovery transport instead of removing the
game's compatibility tool. That transport exposes no Windows Cloud roots and
reports the preparation error when Play is requested; it never starts the game
without the required synchronization. No new Steam offsets, byte patterns or binary-derived
addresses are introduced. Only supported, fully resolved Steam builds remain
eligible. `getcompatpath` is retained for the tool protocol; it is not assumed to
be an obligatory pre-Cloud callback.

A filesystem catalogue avoids launching helpers on warm lookups. Library and
manifest changes invalidate installed-game decisions. Per-game prefix, profile,
folder, alias and readiness stamps invalidate prepared decisions. Uninstalled
games keep their Install choice; a new appmanifest causes preparation before the
installed game receives a tool. The launch shim independently validates the view.

The guard reads the game's actual save rules. Steam API Cloud and game-install
AutoCloud paths do not need Windows prefix views, so they leave separate prefixes
untouched. Installation and repair preflight installed Windows-root games while
Steam is closed. The metadata cache is read once per helper, and a fingerprint of
the game's Cloud rules prevents an old preparation from authorizing changed paths.
Installer preparation reuses the packaged native classifier for automatically
selected Mac32 titles and games with inaccurate common Mac flags. It follows the
saved Mac32 preference and preserves explicit compatibility-tool choices, without
implementing a second architecture classifier in Python.

## Save preservation

The existing Wine prefix and profile remain authoritative. A recognized old Gluon
dual-runtime layout can retain a retired Proton `pfx` alongside the active
`pfx-wine`. Matching Gluon runtime paths, the bridge marker, prefix inventories,
and the plain-Wine runtime distinguish this upgrade from a foreign prefix. The
retired prefix is renamed whole to `.gluon-retired-pfx-*`, with a recovery journal;
all of its files and the active profile remain separate. A crash before readiness
is published restores the old name on retry. Foreign prefixes are not adopted.

A fresh prefix receives real Windows save
folders, while a recognized pre-existing Mac Documents link retains its target.
The first preparation backs up the profile, per-game Steam userdata and affected
shared host-folder subtrees. Cloud metadata is read solely to select conservative
backup subtrees; it is never edited, and Gluon does not implement a second sync
engine. Transformed or unrestricted shared Documents roots are refused rather than backing up
unrelated personal documents or proceeding without protection.

Unrecognized Proton prefixes, multiple real profiles, ambiguous legacy locations,
unexpected links, conflicting real legacy directories and incomplete
backups or insufficient backup space are rejected. A running Wine server postpones the first preparation;
prepared layouts can be checked while another game is running. Steam's own Cloud
preferences and conflict prompts retain their normal meaning.

Preparation holds the same exclusive prefix lease as game launch through backup,
migration and readiness publication. Wine activity is checked again after lease
acquisition, and the launcher rechecks the final Cloud view after acquiring its
lease. A pending launch or another preparation is a transient busy result, so
the native guard retries instead of retaining a permanent failure. Current
layout conflicts and publisher-rule changes take precedence over historical
error messages. Symbolic-link cycles are reported as save-path conflicts without
moving files.

## Validation limits

CI runs the production native guard against disposable libraries, including API
Cloud, game-install roots, a recognized dual-runtime upgrade, recovery-tool
selection for a conflicting prefix, a replaced layout, cached lookups and installation after an
initial uninstalled lookup. Filesystem fixtures cover existing save contents,
backup verification, interrupted migration recovery, changed publisher rules,
and shared host folders without copying unrelated documents.
The complete runtime job initializes Wine in a prepared prefix and verifies that
Steam's profile view and Wine's Documents path see the same save files.

These checks do not demonstrate a live Steam Cloud download, upload or conflict
dialog. Those remain owner-run acceptance checks. The implementation must not be
described as validated for every publisher's Cloud configuration before that
evidence exists.

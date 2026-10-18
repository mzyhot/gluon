# Native Steam Play runtime features

This replaces the useful launcher/runtime work from PR #1 while retaining native
Steam Play. It does not restore the metadata agent, depot rewrites or experimental
Steam probe. For an old 32-bit Mac title with a Windows build, select Gluon in
Steam's Compatibility settings; Steam selects its Windows depots through the tool.

## Game sessions and responsiveness

The native launcher host keeps the PID tracked by Steam. Cocoa quit, signals,
Steam's shutdown callback and its new console-log exit request reach bounded,
prefix-specific cleanup. Signal state is reset for the owned shell; helpers that
ignore termination cannot hold that shell indefinitely. Another game's prefix
is never stopped. Install-script helpers do not acquire a game host, game identity,
Steam bridge, overlay, focus activation or keep-awake process.

Games use their own named app bundles, launched through LaunchServices with their
original working directory and argument boundaries. The Wine loader patch keeps
child processes inside that bundle. The host and Wine cooperate on focus, including
Big Picture's Space. Cmd-Q in the game requests cleanup of that session. A refused
LaunchServices launch falls back to the direct Wine command.

Overlay insertion is limited to game images; Wine's server and built-in Windows
helpers do not load it. Plain Wine uses msync by default. Startup logging reuses a
timestamp for the initial environment snapshot. Session logs distinguish the game
launcher returning from the remaining prefix becoming idle. The latter wait is
retained because a game launcher can exit while its child game keeps running.
These changes do not establish a measured launch-time improvement: real window
readiness, natural-exit latency, Stop and Big Picture remain manual acceptance tests.

## Settings

Settings are `NAME=value` lines in `Gluon/settings.env`, then
`Gluon/games/<appid>.env`. Recognized settings after `%command%` in a game's Steam
Launch Options take precedence and are removed from the game's arguments.
Native install helpers retain their original argument strings.

| Setting | Default | Purpose |
|---|---|---|
| `GLUON_APP_BUNDLE` | `1` | Named game bundle and LaunchServices launch |
| `GLUON_FOCUS` | `1` | Bring the game's window/Space forward |
| `GLUON_OVERLAY` | `1` | Steam-supplied native overlay |
| `GLUON_STEAM_BRIDGE` | `1` | Existing optional Steam API bridge |
| `GLUON_CAFFEINATE` | `1` | Keep the display/system awake during a game |
| `WINEMSYNC` | `1` | Mach semaphore synchronization for plain Wine |
| `GLUON_GL_COMPAT_AS_CORE` | `0` | Opt in to mapping OpenGL 3.0–4.1 compatibility requests onto Apple's core profile |

The OpenGL mapping does not supply removed compatibility features, so enable it
per game when appropriate. The checked GL/WGL aliases and Vulkan 1.0/1.3 device
probes are retained. Hosted runners without native CGL cannot verify actual WGL
rendering and record those dependent cases as skipped.

## Automatic native Steam Cloud

**Gluon** automatically supports Steam API Cloud and native AutoCloud. There is
one public compatibility choice and no per-game Gluon Cloud setting or preparation
command to run. Steam's existing global/per-game Cloud preferences still apply.
The internal tool identifier is `gluon-proton`, because the pinned Steam Play
implementation uses that name to populate Steam's Windows save roots. Existing
`gluon` selections resolve to this tool without rewriting the saved mapping.

API Cloud and AutoCloud rooted in the game installation use Steam's native paths.
They do not require prefix aliases, and an old separate `pfx` does not block them.
Only games whose rules need Windows profile roots receive prefix preparation.

Installation and repair prepare installed games while Steam is closed, after
selecting the Wine runtime. Preparation is also checked at `FindToolForTargetApp`, before the native Cloud root resolver
can return a Windows save path. It keeps the real `pfx-wine` and its existing
profile, adding `pfx` and `steamuser` views onto those same files. Fresh prefixes
get real save directories before Wine starts. Existing links to the account's
Mac Documents folder stay in place; affected game subtrees are backed up using
read-only Cloud metadata. Unrelated personal documents are not copied. Profile
files and Steam's per-game userdata are also backed up before first preparation.
Backups are compared with their sources before any prefix is retired. A changed
publisher Cloud rule requires a fresh backup before the new paths are exposed.

The dual-runtime layout from older Gluon versions has a specific migration:
Gluon's Proton configuration, plain-Wine bridge marker, active Wine runtime and
profile must agree. The retired Gluon `pfx` is renamed whole to a journalled
`.gluon-retired-pfx-*` directory beside the active prefix. Every old file remains
there; no registries or save sets are merged. The current `pfx-wine` profile stays
authoritative. Interrupted renames recover on retry before migration continues.

An unrecognized Proton `pfx`, another library/legacy prefix with saves, competing
profiles, unexpected external links, conflicting legacy save directories or a
failed/incomplete backup stop preparation. Active saves are never merged or
replaced. Insufficient space or an incomplete backup refuses preparation safely.
An internal recovery transport keeps Play available without exposing unsafe
Windows Cloud roots. Pressing Play then reports the saved preparation error and
does not start a game with unsynchronized saves. This transport is hidden from
Gluon's compatibility dropdown; it is not an optional Cloud mode. The normal
launch shim independently checks the game's required paths before running Wine.
Details go to `Gluon/steamplay/cloud.log` and per-app error records. Closing an active Wine session allows
preparation to retry automatically. Prefix views, saves and backups survive uninstall.

The native guard caches validated filesystem stamps. Warm lookups do not spawn
Python or run Wine. Installation/library/layout changes invalidate the cache.
Gluon's automatic views require its plain-Wine runtime; a conflicting third-party
Proton prefix is not adopted or overwritten. `gluonctl cloud prepare APPID` remains
a recovery command for use while Steam is closed, not an enablement step.
`gluonctl cloud status APPID` reports readiness or the saved reason for refusal.

CI checks the native guard with fresh, changed, conflicting and newly installed
synthetic games, plus backup preservation and real Wine save-path round trips.
Live Steam Cloud download/upload and conflict dialogs remain manual acceptance
checks; CI does not operate the owner's Steam account. See
[the investigation](STEAM-CLOUD.md) for the timing boundary and evidence limits.

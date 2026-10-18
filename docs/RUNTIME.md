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

## Optional native Steam Cloud

Steam API Cloud calls continue to use the existing bridge. AutoCloud uses a separate
**Gluon (Steam Cloud)** compatibility choice. Its tool identifier contains `proton`,
as the pinned upstream implementation requires for Steam's Windows Cloud roots.
The ordinary **Gluon** choice remains the default and does not enable AutoCloud.

With Steam and Wine closed, prepare an installed game's layout:

```sh
"$HOME/Library/Application Support/Gluon/bin/gluonctl" cloud prepare APPID
```

Then select **Gluon (Steam Cloud)** in that game's Steam Compatibility settings.
Preparation keeps the real `pfx-wine` and existing user profile, and adds a `pfx`
view and a `steamuser` alias pointing at those same files. Fresh profiles use real
Windows save folders. Existing profile files and Steam Cloud records are backed
up first, within a 256 MiB automatic backup limit. Games reject Cloud mode when
its readiness record/layout is missing or changed. Native path conversion verbs
delegate to the selected Wine prefix without a game identity or overlay.

A separate Proton `pfx`, multiple user profiles, external Documents/AppData links,
or conflicting legacy save folders stop preparation. Nothing is merged, relocated
or replaced in those cases; the ordinary Gluon choice remains available. Prefix
views and saves are retained by uninstall. The Cloud option targets Gluon's plain
Wine layout; third-party Proton runtimes keep their own Cloud behavior.

CI verifies filesystem layout, backup preservation, idempotence and conflict
rejection. Real Cloud download/upload, conflict resolution and first-sync behavior
require manual Steam acceptance tests before relying on this mode for saves.

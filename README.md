# Gluon

**Steam Play, for macOS.**

<img src="gluon-slogan.svg" alt="Download Gluon and experience a world without CrossOver (and Windows). Gluon is an independent project and is not affiliated with, endorsed by, or sponsored by CodeWeavers or its CrossOver product." width="100%">

Gluon runs Windows Steam games on macOS from your normal Steam library, including
games whose Mac version is 32-bit only. It uses a
Windows compatibility runtime that Gluon builds from open-source Wine code.

**No configuring every game. No separate launcher. Just Steam.**

> [!WARNING]
> Gluon is experimental. Not every Windows game is compatible, and some games may fail to
> launch or run correctly. Back up important Steam data before installing.

## Features

Everything below applies to every game Gluon runs. Each part can be switched off per
game or for all games (see [Settings](#settings)).

- **Steam integration.** Windows-only games become installable, and Play starts them
  through Gluon. Games whose only Mac version is 32-bit, which current macOS cannot run
  (Steam shows "Invalid OS Type"), install and run their Windows version the same way;
  games with a 64-bit or Apple silicon Mac version are left alone. Steam's Stop button and quitting Steam, from the desktop or Big Picture,
  end the game and its Windows processes as soon as you ask.
- **Each game is its own Mac app.** It opens in front of Steam, in Big Picture too, shows
  its own name in the menu bar and Dock, and macOS Game Mode can recognize it. Quitting it
  from its menu or the Dock ends the game the way Steam's Stop does.
- **Steam Cloud.** Saves sync through Steam Cloud. Gluon points each cloud rule's Windows
  save folder at the matching folder inside the game's Wine prefix, and backs up the
  game's local cloud files once before the first sync. See
  [Compatibility and limitations](#compatibility-and-limitations) for the rules Steam
  keeps to Windows.
- **Steam overlay and achievements** through your installed Steam client. The overlay
  runs only in the game's own processes.
- **Graphics.** Direct3D 9 through DXVK, Direct3D 10/11 through DXMT, Vulkan up to 1.4
  through MoltenVK, and OpenGL up to 4.1 (the newest macOS offers), including games that
  ask for an OpenGL 3.x compatibility context, which macOS does not provide.
- **Performance.** Wine's Mach-semaphore synchronization (msync) is on, and the Mac and
  display stay awake while you play with a controller.
- **Safe updates.** New runtimes are switched in only while Steam is closed, and the
  previous one is kept for rollback.
- **Metadata refreshes.** Steam re-downloads a game's details from time to time and,
  while it runs, forgets Gluon's changes; the game keeps launching. Gluon puts its
  changes back whenever Steam is closed, with no prompts and no restarts, and clears
  the do-nothing update Steam would otherwise run afterwards. Real game updates are
  untouched.

## Installation

1. Download the latest installer from the [Releases](../../releases) page.
2. Open the `.pkg` file and follow the instructions.
3. Quit and reopen Steam. Gluon's background helper sets up Steam while it is closed;
   afterwards, install and play your Windows games from the library.

## Settings

Each game's settings live in Steam: right-click the game, choose **Properties**, and add
them to **Launch Options** after `%command%`, for example:

```text
%command% GLUON_FOCUS=0 WINEMSYNC=0
```

Gluon applies these and removes them before the game starts. Settings for every game go
in `~/Library/Application Support/Gluon/settings.env`, and settings for one game in
`~/Library/Application Support/Gluon/games/<appid>.env`, one `NAME=value` per line.
Launch options win over a game's file, which wins over the file for every game.

| Setting | Default | Set to `0` to |
|---|---|---|
| `GLUON_APP_BUNDLE` | `1` | start the game without its own Mac app (also turns off the next one) |
| `GLUON_FOCUS` | `1` | stop the game from coming to the front when its window opens |
| `GLUON_OVERLAY` | `1` | keep Steam's overlay out of the game |
| `GLUON_STEAM_BRIDGE` | `1` | hide the Steam client from the game (no achievements or overlay) |
| `GLUON_GL_COMPAT_AS_CORE` | `1` | reject OpenGL 3.x compatibility contexts as Wine normally does |
| `GLUON_CAFFEINATE` | `1` | let the Mac and display sleep during play |
| `GLUON_CLOUD` | `1` | keep Steam Cloud off for the game on this Mac (settings files only) |
| `WINEMSYNC` | `1` | use the Wine server for synchronization instead of msync |

`GLUON_RUNTIME=<folder>` picks another runtime from `~/Library/Application Support/Gluon/runtime/`.
`GLUON_CLOUD` changes Steam's local details for the game, so it takes effect the next time
Steam is closed and reopened, and it is read only from the settings files.

## Tested games

Tested on an Apple Silicon Mac with the current runtime. Other games may or may not work.

| Game | Result |
|---|---|
| Happy Wheels | Runs, including fullscreen; Steam overlay and achievement popups work |
| People Playground | Runs; Steam achievements unlock |
| Primordialis | Runs |

## How it works

1. **Discover.** Find Steam games with a Windows build but no Mac build that current
   macOS can run (no Mac build, or a 32-bit one).
2. **Enable.** Update Steam's local metadata so those games can be installed.
3. **Launch.** Route their Play buttons through Gluon's launcher.
4. **Run.** Start the game as its own Mac app, on a Wine runtime built from source.

Gluon also supports Proton-layout Steam Play tools as runtimes.

## For developers

### Repository layout

```text
gluon/                  Python package (standard library only)
  vdf.py                Valve KeyValues readers and writers
  appinfo.py            Steam's app metadata cache
  enable.py             make a game installable and route Play to the launcher
  metadata.py           update protection and metadata refresh detection
  install.py            recover installation records for missing game folders
  runtime.py            switch runtimes only while Steam and Wine are idle
  appbundle.py          per-game Mac app bundles and launch sessions
  overlay.py            validate Steam's overlay libraries for the game
  session.py            bounded shutdown of one game's Wine prefix
  steam.py              Steam paths and process checks
  cef.py                Steam console over the client's debug bridge (development aid)
tool/gluon              launcher Steam starts in place of the game's .exe
tools/                  native launcher host (Steam Stop and quit)
packaging/              runtime build, Wine patches, checks and the installer
docs/                   implementation notes and findings
archive/                retired experiments
```

### Common commands

```bash
./scripts/install-tool.sh        # register the Steam Play shim (runs the installed launcher)
python3 -m gluon candidates      # games with a Windows build and no runnable Mac build
python3 -m gluon enable <appid>  # Steam must be closed
python3 -m gluon status
python3 -m gluon disable <appid> # restore original metadata and launch option
```

### Building

Runtimes and installers are built by GitHub Actions
([`.github/workflows/build.yml`](.github/workflows/build.yml)); source checks run on every
push ([`.github/workflows/source-checks.yml`](.github/workflows/source-checks.yml)).
See [docs/CLOUD-DEVELOPMENT.md](docs/CLOUD-DEVELOPMENT.md).

```bash
./packaging/build-runtime.sh                                      # -> build/gluon-wine
GLUON_PKG_RUNTIME=build/gluon-wine ./packaging/build-pkg.sh 0.3.0  # installer package
```

The launcher uses `~/Library/Application Support/Gluon/runtime/current` unless
`GLUON_RUNTIME` names another one, and logs to `~/Library/Application Support/Gluon/logs/`.
Implementation details and open items are in [docs/NOTES.md](docs/NOTES.md).

## Compatibility and limitations

Compatibility depends on the runtime, graphics API support, game-specific dependencies,
anti-cheat and DRM, and Windows-specific features. Some games work immediately, some
need more compatibility work, and some may not work at all.

Gluon modifies local Steam metadata to enable Windows-only titles. These changes are
experimental and may interfere with Steam's normal behavior.

Steam Cloud rules that a game's developer restricted to Windows stay Windows-only: Steam
keeps those files for Windows PCs and does not download them to a Mac, so those saves do
not sync here. Steam Cloud stays off on this Mac for a game whose rules Gluon cannot
place, such as one installed in a Steam library outside your home folder. Gluon keeps
its cloud file backups in `~/Library/Application Support/Gluon/backups/cloud/`.

## Disclaimer

Gluon is an independent open-source project and is not affiliated with, endorsed by, or
sponsored by Valve Corporation, CodeWeavers, or their respective products.

Steam is a trademark of Valve Corporation. CrossOver is a product of CodeWeavers.

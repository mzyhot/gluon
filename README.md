
# Gluon
Steam Play; for MacOS.
![Gluon slogan](gluon-slogan.svg)
## Layout

    gluon/            Python package (stdlib only)
      vdf.py          Valve KeyValues: text, binary, appinfo/packageinfo readers
      appinfo.py      read/rewrite Steam's app metadata cache
      enable.py       make an app installable + route its Play button to the launcher
      steam.py        Steam paths, running check
      install.py      preserve stale installation records when game folders are missing
      metadata.py     managed-game update protection and metadata refresh detection
      overlay.py      restore Steam's own native overlay at the Wine launch boundary
      runtime.py      select complete runtime updates while Steam and Wine are idle
      cef.py          Steam console over the client's debug bridge (development aid)
    tool/             what gets installed into Steam's compatibilitytools.d/gluon
      gluon           launcher Steam starts in place of the game's .exe
    scripts/          install-tool.sh (development copy of the launcher inside Steam)
    packaging/        build-runtime.sh (runtime from source), build-pkg.sh, package scripts
    docs/NOTES.md     findings, open items, machine state
    archive/          retired experiments, kept for reference

## Installer package

    ./packaging/build-runtime.sh         # Windows runtime, from open-source code -> build/gluon-wine
    GLUON_PKG_RUNTIME=build/gluon-wine ./packaging/build-pkg.sh 0.3.0

Releases are built by GitHub Actions (`.github/workflows/build.yml`).

Installing it is all a user does: a background helper then keeps every Windows-only game
in the library installable and routed through Gluon. See docs/NOTES.md, "Package".

## Development use

    ./scripts/install-tool.sh            # copy tool/ into Steam
    python3 -m gluon candidates          # cached games with a Windows build but no macOS build
    python3 -m gluon enable <appid>      # Steam must be closed
    python3 -m gluon status
    python3 -m gluon disable <appid>     # restore original metadata and launch option

The launcher uses the runtime at `~/Library/Application Support/Gluon/runtime/current`
(or `$GLUON_RUNTIME`): either a plain Wine tree or a Proton-layout Steam Play tool.
It logs to `~/Library/Application Support/Gluon/logs/`.

Gluon restores refreshed compatibility metadata while Steam is closed and protects
managed games with Steam's update-on-launch policy. If Steam refreshes an entry
mid-session, Gluon asks for a restart before updating or verifying that game.
Missing installation folders are recovered without removing saves or Wine prefixes.

The plain-Wine launcher preserves Steam's supplied native overlay for the game
host. The overlay comes from the user's installed Steam client.

Runtime builds include extension-checked OpenGL aliases and native Vulkan
portability negotiation through MoltenVK. Packages stage each complete runtime in
a unique directory; the background helper switches the default only while Steam
and Wine are idle, preserving the previous selection for rollback. Game prefixes
stay in place. See the graphics documents for tested capabilities and limitations.

**Steam Play, for macOS.**

<img src="gluon-slogan.svg" alt="Download Gluon and experience a world without CrossOver (and Windows). Gluon is an independent project and is not affiliated with, endorsed by, or sponsored by CodeWeavers or its CrossOver product." width="100%">

Gluon brings Windows-only Steam games to macOS through an open-source Windows compatibility runtime.

Install Gluon once, and it automatically integrates with Steam to make Windows-only games installable and launchable directly from your library.

**No manually configuring every game. No separate game launcher. Just Steam.**

> [!WARNING]
> Gluon is experimental. Not every Windows game is compatible, and some games may fail to launch or run correctly. Back up important Steam data before installing.

## Features

- **Native Steam integration.** Launch compatible Windows games directly from your existing Steam library.
- **Automatic game detection.** Identifies games with Windows builds but no macOS build.
- **Background integration.** Automatically maintains Steam compatibility settings while Steam is closed.
- **Open-source runtime.** Uses Wine-based compatibility technology without requiring CrossOver.
- **No per-game setup.** Gluon handles Steam metadata and launch routing automatically.
- **Reversible changes.** Development tools can restore original game metadata and launch options.

## Installation

### 1. Download Gluon

Get the latest installer from the [Releases](../../releases) page.

### 2. Install

Open the downloaded `.pkg` file and follow the installation instructions.

### 3. Launch Steam

Gluon's background helper automatically detects Windows-only games and configures their Steam integration when Steam is closed.

Once integration has completed, open Steam and install your Windows games directly from your library.

That's it.

> [!NOTE]
> Game compatibility varies. Gluon does not guarantee that every Windows game will launch or run correctly.

## How It Works

Gluon integrates a Windows compatibility runtime into the macOS Steam experience.

1. **Discover.** Identify Steam games with Windows builds but no native macOS build.
2. **Enable.** Modify Steam's local metadata to make those games installable.
3. **Launch.** Route their Play buttons through Gluon's compatibility launcher.
4. **Run.** Execute the Windows game using the configured Wine-based runtime.

Gluon supports both conventional Wine runtime trees and Proton-layout Steam Play tools.

The goal is simple: bring the Steam Play experience to macOS without forcing users to manage separate launchers or manually configure every game.

## For Developers

### Requirements

- macOS
- Steam
- Python 3
- A compatible Wine-based runtime

Gluon's Python package uses only the standard library.

### Repository Structure

```text
gluon/
├── gluon/
│   ├── vdf.py           # Valve KeyValues readers
│   ├── appinfo.py       # Steam metadata management
│   ├── enable.py        # Game enablement and launch routing
│   ├── steam.py         # Steam paths and process checks
│   └── cef.py           # Steam debug bridge utilities
├── tool/
│   └── gluon            # Compatibility launcher
├── scripts/
│   └── install-tool.sh  # Development installation
├── packaging/
│   ├── build-runtime.sh
│   └── build-pkg.sh
├── docs/
│   └── NOTES.md
└── archive/             # Retired experiments
```

### Development Installation

Install the compatibility launcher into Steam:

```bash
./scripts/install-tool.sh
```

List games with Windows builds but no macOS builds:

```bash
python3 -m gluon candidates
```

Enable a game while Steam is closed:

```bash
python3 -m gluon enable <appid>
```

Check integration status:

```bash
python3 -m gluon status
```

Restore a game's original metadata and launch options:

```bash
python3 -m gluon disable <appid>
```

### Building Gluon

Build the Windows compatibility runtime from open-source code:

```bash
./packaging/build-runtime.sh
```

Package Gluon with the resulting runtime:

```bash
GLUON_PKG_RUNTIME=build/gluon-wine ./packaging/build-pkg.sh 0.3.0
```

Release builds are automated through GitHub Actions:

[`.github/workflows/build.yml`](.github/workflows/build.yml)

### Runtime Configuration

By default, Gluon looks for its runtime at:

```text
~/Library/Application Support/Gluon/runtime/current
```

This can be overridden using the `GLUON_RUNTIME` environment variable.

Both conventional Wine trees and Proton-layout Steam Play tools are supported.

### Logs

Gluon writes logs to:

```text
~/Library/Application Support/Gluon/logs/
```

For implementation details, known issues, and development findings, see [docs/NOTES.md](docs/NOTES.md).

## Compatibility & Limitations

Gluon is an experimental compatibility project, not a native Windows implementation.

Compatibility depends on several factors, including:

- The Windows compatibility runtime
- Graphics API support
- Game-specific dependencies
- Anti-cheat and DRM systems
- Windows-specific functionality

Some games may work immediately, others may require additional compatibility improvements, and some may not work at all.

Gluon modifies local Steam metadata to enable Windows-only titles. These modifications are experimental and may interfere with Steam's normal behavior.

**Back up important Steam data before using Gluon.**

## Disclaimer

Gluon is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Valve Corporation, CodeWeavers, or their respective products.

Steam is a trademark of Valve Corporation. CrossOver is a product of CodeWeavers.

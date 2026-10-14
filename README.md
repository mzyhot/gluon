
# Gluon

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

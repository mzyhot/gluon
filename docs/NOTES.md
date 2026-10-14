# Gluon development notes

Findings are from the macOS Steam client build 1788652215 on macOS 27.2 (Apple M4),
tested 2026-10-13 with Swarm Queen (701870) and Primordialis (3011360).

## Working pipeline (stock client, nothing loaded into Steam)

    gluon enable <appid>      (Steam closed)
      1. appcache/appinfo.vdf: add "macos" beside "windows" for the app, its depots
         and its launch entries; add "macos64" to common/osextended
      2. userdata/<id>/config/localconfig.vdf: LaunchOptions = "<launcher>" run %command%
    start Steam
      -> app is "released (Subscribed, Permanent)", same as a native game
      -> Steam's own content system downloads the Windows depot
    Play
      -> Steam runs its normal launch flow (install script, cloud sync, stats sync)
      -> starts  <launcher> run <installdir>/<game>.exe  as the tracked game process
      -> launcher creates <library>/steamapps/compatdata/<appid>/pfx and starts Wine

Verified: Swarm Queen's depot 701871 (39 MB) and Primordialis's depot 3011361 (48.77 MB)
were downloaded by Steam; Primordialis reached Wine with MoltenVK initialised and Steam
showed "App Running" for the whole session.

## What the stock macOS client does and does not honour

| Mechanism | Result |
|---|---|
| `compatibilitytools.d/gluon` (in `Steam.AppBundle/Steam/Contents/MacOS/`) | Scanned at startup. Tool registers and its manifest loads when `to_oslist` is `macos`. |
| `CompatToolMapping` in `config/config.vdf` | Loaded ("Mapping AppID … to tool gluon"), but not applied at install or launch. |
| `userdata/<id>/config/compat.vdf` `platform_overrides { <appid> { src, dest } }` | Ignored. Steam writes this file itself only when its Steam Play manager is active. |
| `app_set_config <appid> platform_override_source/dest` | Written to the appmanifest `UserConfig`, survives restarts, but depot selection ignores it. `app_install` fails "Invalid platform". |
| `appcache/appinfo.vdf` entry lists `macos` | Honoured for platform validity, depot selection and launch-entry selection. |
| `common/osextended` lacking `macos64` | App flagged "Invalid OS Type" (POSTAL 232770 has this natively). |
| Launch option `"<wrapper>" run %command%` | Honoured. Steam passes the wrapper, `run`, and the absolute exe path as separate arguments. |

Without a launch option Steam tries to start the `.exe` directly and fails with
`Failed to spawn process` / `AppError_46 "OS Error 0"`.

Steam's Steam Play manager only becomes active when the client's platform is Linux at
startup (`@sSteamCmdForcePlatformType linux`). That makes the whole client behave as
Linux and puts installed macOS games at risk (their depots stop matching), so it is
not used. The earlier experiment that changed this inside the running client is
retired; its source is kept in `archive/retired-hook/` for reference only and is not
built or installed.

## appinfo.vdf (v29) facts

- Layout is documented at the top of `gluon/appinfo.py`.
- The file round-trips byte-for-byte through `AppInfoFile`.
- Per-entry binary SHA-1 = SHA-1 of the stored blob (verified for 412/412 entries).
- Per-entry text SHA-1 is only reproduced by our formatter for 77/412 entries. It is
  left untouched when an entry is edited and Steam accepts the entry.
- Steam reads the cache at startup and writes it on exit and after metadata refreshes
  (the October 14 refresh was persisted within five seconds), so edits must be made
  while Steam is closed. Edited entries survived three restarts.

## Launcher behaviour

- Environment Steam provides: `SteamAppId`, `SteamGameId`, `SteamOverlayGameId`,
  `SteamUser`, `SteamClientLaunch=1`, `SteamEnv=1`, `Steam3Master`; cwd is the install dir.
- `wine game.exe` returns as soon as a GUI program has started. The launcher therefore
  waits on `wineserver -w`; otherwise Steam marks the game as exited after a few seconds.
- On SIGTERM the launcher runs `wineserver -k`; Steam then logs exit code 143 and
  removes the app from its running list.
- First run creates the prefix with the Mono/Gecko download prompts suppressed
  (about 12-15 s under Rosetta).

## Reference project: albvalort/proton-macos

Cloned for reference at `third_party/proton-macos` (not tracked by this repo), commit
`cc1d07a` of 2026-10-12. BSD-3-Clause for its own code; its Wine fork is LGPL-2.1+.
It has no published release, so its runtime only exists as a source build.

The two projects cover opposite halves:

| | proton-macos | Gluon |
|---|---|---|
| Runtime: Wine fork, DXVK on MoltenVK, msync | done | uses stock Wine Stable |
| Steam API from inside Wine (lsteamclient loading the client's `steamclient.dylib`) | done, achievements verified | missing (open item 4) |
| Steam itself installs the Windows build and Play works on the game's own page | "not working yet" in the GUI client; uses a separate downloader and non-Steam shortcuts | done, via the metadata cache |

Taken from it so far:

- **Runtime contract.** Its tool is laid out like Proton (`proton`, `files/bin/wine`) and
  expects `proton waitforexitandrun <exe>` with `STEAM_COMPAT_DATA_PATH`,
  `STEAM_COMPAT_CLIENT_INSTALL_PATH` (the client folder holding `steamclient.dylib`),
  `STEAM_COMPAT_INSTALL_PATH`, `STEAM_COMPAT_APP_ID`, `SteamAppId`, `SteamGameId`.
  `tool/gluon` now provides exactly that when `runtime/current` is such a tool, so their
  build can be dropped in as Gluon's runtime without further changes. Verified with a
  stub tool; not yet with their real build.
- **Overlay library.** Steam adds its native-game overlay library to what it launches;
  it must not reach Wine. The launcher clears `DYLD_INSERT_LIBRARIES`.
- **Library list.** `appcache/librarycache/<appid>/` exists for every app in the user's
  library; `gluon candidates` uses it to skip cached apps the user does not own.

Facts from its docs worth keeping in mind (theirs, not re-verified here):

- `steamclient.dylib` is universal (x86_64 + arm64), so x86_64 Wine under Rosetta can
  load it in-process; no helper process is needed for the Steam API.
- Steam may start a tool as x86_64. On macOS 27 `/usr/bin/python3` only works as arm64,
  so their `proton` is a small wrapper that runs the Python script with `arch -arm64`.
- The Mac client does not sync Steam Cloud for Windows-only games in their setup.
  Gluon's launch flow did run Steam's cloud-sync step for Primordialis; whether files
  actually sync is untested.
- Per-game settings live in `compat/<appid>.yaml` (graphics backend, extra environment).
  A model for Gluon's game-specific configuration later.

Building their runtime needs: llvm-mingw (PE toolchain), Homebrew bison/flex/automake,
their `build-deps.sh` (FreeType, MoltenVK, DXVK-macOS), then a full Wine cross-build and
the Proton modules. On this Mac llvm-mingw, bison, flex and automake are not installed.

## Runtime now in use: proton-macos 0.0.0-dev

- Source: build artifact `proton-macos-0.0.0-dev` of the "Release" workflow run
  38242355395 in albvalort/proton-macos (2026-10-12, commit `cc1d07a`).
- Hashes checked 2026-10-13: artifact container SHA-256 `e93ff4e1…0f152f` equals GitHub's
  recorded digest; the archive inside is `ecea4586…51de24c`, equal to its published
  `.sha256`. The tool's `version` file names Proton `823f91a` and Wine `ff4b41a`, the
  submodule commits of that source commit.
- Extracted (tool folder only, their `install.sh` not run) to
  `~/Library/Application Support/Gluon/runtime/proton-macos-0.0.0-dev`; `runtime/current`
  points at it. The previous Wine Stable link target is `~/Games/SteamWine/wine/Wine Stable.app/Contents/Resources/wine`.
- It does not bundle Wine Mono/Gecko. Its first run stalls on Wine's add-on download
  prompt unless `mscoree,mshtml=` is in `WINEDLLOVERRIDES`; `tool/gluon` adds that
  whenever prefix setup is about to happen. Games that need .NET via Wine Mono will not
  work until the add-ons are bundled.
- Verified through Steam: Play on Primordialis -> Gluon -> `proton waitforexitandrun` ->
  prefix created (`version` 11.0-100), msync active, `steam.exe` helper ran.
- Not verified: the Steam API bridge with a real game (no test game reached it yet).
- Primordialis itself exits after ~25 s on both runtimes: its log says
  `glVertexAttribIPointer could not be loaded` (needs OpenGL 3 functions the default
  macOS OpenGL context lacks). The earlier "running" session under Wine Stable was most
  likely this same failure waiting on an error box. A different test game is needed
  for rendering; the tool's own notes list Lingo, Downwell, Minimalism and ANIMAL WELL.

## Package

`packaging/build-pkg.sh [version]` builds `dist/Gluon-<version>.pkg` (349 MB with the
runtime, unsigned). It is a per-user package: no administrator password, everything goes
under `~/Library/Application Support/Gluon` (`bin/`, `lib/gluon/`, `runtime/`).
Its postinstall script writes and loads `~/Library/LaunchAgents/com.gluon.agent.plist`,
which runs `bin/gluon-agent` at login and every 60 s. The agent runs `gluon sync`:

- does nothing while Steam is running;
- otherwise enables every Windows-only game in the library and re-applies entries Steam
  has refreshed (this is the answer to the metadata-refresh item below);
- writes Steam's files atomically (temp file + rename).

So after installing, new Windows-only games become installable the next time Steam is
restarted, with nothing to run by hand. `bin/gluon-uninstall` restores every edited
entry and launch option and removes the program files.

Tested: payload and scripts inspected; `sync` and full rollback exercised against a copy
of Steam's files (55 games enabled, second run a no-op, native games untouched, all
edited values restored). Not tested: installing the package itself.

Why per-user rather than system-wide: Steam's data is per-user, and the runtime writes
its prefix template and lock files inside its own folder on first use.

Known packaging gaps: needs `/usr/bin/python3` (Xcode Command Line Tools), as does the
runtime's own script; unsigned and not notarized; rollback note - Steam itself re-saves
an edited entry's integer fields as text (seen: `osarch` 64), values unchanged.

## First real use (2026-10-13, after the package was installed)

Three games tried, three runtime-level results (Steam integration worked each time):

| Game | Result with proton-macos 0.0.0-dev | Evidence |
|---|---|---|
| Primordialis (3011360) | exits after ~25 s | game log: `glVertexAttribIPointer could not be loaded` (needs OpenGL 3 functions) |
| People Playground (1118200, Unity) | crashes after ~9 s | `virtual_setup_exception stack overflow`; Unity's Player.log stops right after "Mono path" |
| Happy Wheels (4705510) | runs but white screen | wined3d path: `fs_hack_setup_gamma_shader ... version '330' is not supported`, `GL_INVALID_ENUM ... Querying context profile` |

Not yet tried: the same games on the stock Wine runtime. `games/1118200.env` and
`games/4705510.env` are set to `GLUON_RUNTIME=wine-stable` for that comparison.

Launcher changes made in response:

- Early failure is reported: a non-zero exit within 90 s raises a macOS notification with
  the last error line and the log path.
- Per-game settings: `$GLUON_HOME/games/<appid>.env` (NAME=value lines) can pick the
  runtime by name (`GLUON_RUNTIME=wine-stable`) and pass runtime options.
- A plain Wine runtime uses `<compat>/pfx-wine`; `<compat>/pfx` belongs to the Steam Play tool.

### Steam Cloud finding - why cloud safe mode exists

People Playground's cloud rules cover only `Contraptions` (root `gameinstall`). On first
sync the Mac client downloaded every cloud file, including four under `Mods/`. In the
post-exit pass it logged `no longer matches any patterns (2). Removing from cloud` for all
180 files ("2" is Steam's platform flag for macOS), re-persisted the 176 that match the
Contraptions rule, and committed `Forget` for the four `Mods` files in upload batch 48.
Local copies were untouched; they and the contraptions are backed up in
`backups/cloud-1118200-20261013-163753`.

Of the managed apps, 38 have auto-cloud rules; most rules use Windows roots
(`WinAppDataLocalLow`, `WinMyDocuments`, ...) and 29 are marked Windows-only. As a macOS
client Steam cannot resolve those, so saves do not sync, and the same "forget" could hit
them. Until that is solved `enable.CLOUD_SAFE_MODE` removes the `ufs` section from every
managed entry (original kept in the state file, restored exactly by `disable`), which
turns Steam Cloud off for those apps on this Mac only.

Real fix to build and test carefully: add macOS to each rule's platforms and add
`rootoverrides` that map each Windows root to the matching folder inside the game's prefix.

### Selection rule

Fixed 2026-10-13: apps with no `oslist` at all are Windows apps (Fallout: New Vegas,
Hitman 2, Watch_Dogs 2, ...) and are now included; platform lists are parsed with
whitespace tolerated ("windows, macos"); types game, demo, application, beta and mod are
offered. 71 apps managed. Not handled: games whose only macOS build is 32-bit (flagged
"Invalid OS Type", e.g. POSTAL) - they count as having a macOS build and are left alone.

## Runtime in use since 2026-10-13 evening: cx-26.3-foss

Gluon's default runtime is now the open-source part of CrossOver 26.3.0: Wine 11.0 with
CodeWeavers' changes, plus DXMT, DXVK, MoltenVK, wine-mono, Gecko and the support
libraries. It replaces both the Proton-based runtime and official Wine Staging.

Two forms of this runtime exist:

- **Distributed (the installer package):** built by `packaging/build-runtime.sh` on a
  GitHub Actions macOS runner. Wine and FreeType are compiled from CodeWeavers' published
  open-source release (`crossover-sources-26.3.0.tar.gz`, sha256 `ac99c8ca…dd6872`);
  MoltenVK and DXMT come from those projects' own releases; the Steam bridge is an
  optional input. No binary from any commercial product is distributed.
- **Local development copy (`runtime/cx-26.3-foss`, this Mac only, never distributed):**
  the open-source components as shipped inside the CrossOver 26.3.0 app, used to find out
  quickly whether CodeWeavers' fixes solve the failures. Every file was checked against
  the published source and anything not in it was left out (`cxcompatdb.so`, `winelib`,
  the `cx*.exe` tools, `winewrapper.exe`, `steamurlhelper.exe`, stub programs, all
  CrossOver scripts, Apple's D3DMetal). Closed components are not analysed, used or shipped.

Graphics layers are installed straight into the default DLL folder (Wine's originals
kept as `*.wined3d`): DXMT's `d3d11`, `dxgi`, `d3d10core`, `winemetal`. The local copy
also has DXVK's `d3d9`; the CI build uses Wine's own Direct3D 9 for now.

What CodeWeavers' open Wine source does that upstream Wine does not (read from source,
`sources/wine`, 80 numbered fixes in total; these are the ones behind our failures):

| Fix | File | What it does |
|---|---|---|
| CW 24945 | `dlls/ntdll/unix/virtual.c` | A write fault on a page that is already writable and executable is absorbed by switching the page's execute permission off and on. Under Rosetta this is what a JIT (Unity's Mono) triggers; upstream reports it to the game as a crash. |
| CW 25719 | same | Same treatment for an execute fault on an executable page. |
| CW 18947 | same | After code is written into another process, execute permission is toggled so Rosetta retranslates it. |
| CW 24256 | `signal_x86_64.c` | Floating-point control state in signal contexts is wrong under Rosetta; corrected. |
| CW 24067 | `loader.c` | Exports `prepend_dll_path` and loads an optional module at start. This is how CrossOver puts a graphics layer's folder ahead of Wine's own DLLs. Gluon gets the same effect by placing the layer's DLLs in the default folder. |
| msync | `server/`, ntdll | Faster synchronisation, enabled with `WINEMSYNC=1` (not enabled by Gluon yet). |

Also set by the launcher: `ROSETTA_ADVERTISE_AVX=1` and `DOTNET_EnableWriteXorExecute=0`.

Which Wine builds contain CW 24945 (checked by string in `ntdll.so`): CrossOver 26.3 yes;
Sikarugir WineCX 24.0.7 yes; official Wine Staging 11.18 no; Wine Stable 11.0 no;
proton-macos 0.0.0-dev no.

### Steam API bridge

`lsteamclient.dll/.so` and `steam.exe` from the proton-macos build are added to the
runtime (its native half only needs symbols the CodeWeavers Wine core library exports).
The launcher, for a plain-Wine runtime that carries them:

1. copies the two files into the prefix's `system32` / `syswow64` if missing,
2. creates `C:\Program Files (x86)\Steam`,
3. sets `HKCU\Software\Valve\Steam\ActiveProcess` `SteamClientDll` and `SteamClientDll64`
   to `C:\windows\system32\lsteamclient.dll` (once per prefix),
4. starts the game as `steam.exe <game.exe>` with `STEAM_COMPAT_CLIENT_INSTALL_PATH` set.

`GLUON_NO_STEAM_BRIDGE=1` in a game's settings file turns it off.

### Results on this runtime

| Game | Result | Evidence |
|---|---|---|
| People Playground (Unity, D3D11) | Works. Without the bridge: runs, renders (user-confirmed), but "SteamApi_Init failed". With the bridge: 15-minute session, that error no longer in the game log. | Unity log: Direct3D 11.0 level 11.1 on "Apple M4" |
| Happy Wheels (Electron) | Renders with `--in-process-gpu` (added automatically for Chromium-based apps; without it DXMT logs `cross-process swapchain not supported yet`). Fullscreen crashed with the DXMT build from the CrossOver bundle and does not with upstream DXMT 0.80 - see below. | wine log, user report |
| Primordialis (OpenGL 3) | Starts (picks its AVX build) but the OpenGL error remains (user report). macOS gives OpenGL 3+ only as a core profile. Not fixed. | user report |

### Happy Wheels fullscreen crash - diagnosis

Reproduction without the game's UI: the game keeps "start in fullscreen" as a flag file,
`%APPDATA%\\HappyWheels\\hwdata\\flags\\fullscreen`. With it present the game crashed 12 s
after launch, 3 out of 3 runs, at the same address as the user's crash on toggling
fullscreen. (The game exits if started with a Chromium debugging port, so that route is
not available.)

What the crash is, from `WINEDEBUG=+loaddll,+seh`:

- Access violation in `msvcrt.dll` +0xA496, inside its memory-copy routine.
- Registers decode (per `dlls/msvcrt/string.c`: rdi/rsi = current destination/source,
  r8 = bytes left, rcx/rdx = original arguments): a copy of 0x591000 bytes =
  1520 x 960 x 4, with 0x1000 bytes left when the source ran out.
- The source is readable for 0x590000 bytes, which is exactly a 1520 x **959** x 4 image
  rounded up to 4 KB pages, starting at the head of a large Chromium allocation.
- The caller is DXMT's `d3d11.dll` (the exception handlers of the two calling frames are
  inside it).

So DXMT copied one row more than the application supplied. Large buffers have nothing
mapped after them, which is why only fullscreen-sized uploads fault; in a window the
same over-read lands in pooled memory and goes unnoticed.

Upstream DXMT's `TextureUpdateCommand` / `UpdateTexture` compute the row count from the
update box correctly. With the official upstream **DXMT v0.80** release swapped into the
runtime, the same reproduction runs without a fault. The defect is therefore in the DXMT
build that ships inside CrossOver 26.3 (file dated 2026-07-15), not in v0.80. Which
commit introduced or fixed it has not been identified; the CI-built runtime pins v0.80.

Local runtime: upstream v0.80 DLLs are installed over the bundled ones (kept as
`*.cx263`). People Playground needs re-checking on v0.80.

Crash reporting: `HKCU\\Software\\Wine\\WineDbg\\ShowCrashDialog=0` was set in that prefix;
Wine's debugger still printed no backtrace. The module and register method above works
without it.

### Tooling note

REA in the desktop app had no Ghidra setting (only the terminal config did), so it used
Hopper. `the desktop app's configuration` now has `GHIDRA_INSTALL_DIR` and
`REA_ANALYSIS_PROVIDER=ghidra` (backup beside it); it applies after the app restarts its
REA server. Until then `rea` on the command line with those variables uses Ghidra.

### Other components on disk, not in use

- `runtime/wine-cx-24.0.7`: Sikarugir engine `WS12WineCX24.0.7_7` (sha256 verified) with
  DXMT v0.80 installed; cannot start alone, it expects support libraries beside it.
- `runtime/wine-staging-11.18`, `runtime/proton-macos-0.0.0-dev`, `runtime/wine-stable`.
- D9VK `v1.10.3-20250511`: downloaded, unused; GitHub has no checksum recorded for it.

### Distribution

Only CI-built packages are released. `.github/workflows/build.yml` runs
`packaging/build-runtime.sh` and `packaging/build-pkg.sh`; a tag `vX.Y.Z` publishes the
release. Inputs are pinned by SHA-256 in the build script.

Not yet in the CI-built runtime (present in the local development copy): DXVK for
Direct3D 9, wine-mono and Gecko, GnuTLS (so no HTTPS from inside games), GStreamer
(video playback), SDL (controllers through SDL).

## Graphics development and execution policy (2026-10-13)

The owner stopped local graphics testing after reporting severe MacBook heating.
All matching Wine/graphics test processes were terminated; the stop check found
zero remaining processes. Do not repeat the failed native-module experiments or
start parallel local probes. Compilation stays on GitHub Actions. The owner later
authorized one short local test at a time, with reduced workload/priority, a hard
timeout, immediate stop on failure, and prefix-specific cleanup. Temperature
polling was explicitly withdrawn; do not start a temperature polling loop.

The first `smctemp` invocation failed inside the tool sandbox with IOKit
`kIOReturnNotPermitted` (`e00002e2`); its `0.0` was invalid. A later sensor-only
invocation outside the sandbox returned valid readings. No graphics
test was started by either temperature command.

Build/probe helpers now reject ordinary local invocation, default to four build
workers, and run on GitHub infrastructure. The optional Linux Codespaces setup
supports source review and validation; it cannot reproduce macOS/Metal/CGL.
Use macOS Actions for the complete runtime. See `docs/CLOUD-DEVELOPMENT.md`.

### OpenGL

Primordialis was reproduced from a copied game and copied prefix. Its trace calls
only `wglCreateContext`; no attributes/core context request precedes the failure.
macdrv supplies `2.1 Metal - 91.7`; its separate core probe finds `4.1`. The legacy
context advertises `GL_EXT_gpu_shader4`, yet Wine rejects the unsuffixed
`glVertexAttribIPointer` because the generated requirement is OpenGL 3.0.

A source-built Windows baseline probe confirmed the EXT entry point exists and
sets integer-array state (`integer_array=1`, `GL_error=0`). The Wine source patch
adds all 34 exact Khronos-defined core/EXT aliases in that extension. Capability
checks now precede the shared function-pointer cache so a pointer obtained on a
core context cannot bypass a later legacy context's capability checks.

All alias metadata, signatures and source entries passed validation against the
SHA-256-pinned Khronos registry. Missing aliases, changed signatures and a cache
bypass were rejected by negative checks. The native Wine OpenGL module compiled,
but substituting that module alone in a development-runtime copy failed during
DLL attachment before WGL ran. This is not a passed runtime test. Validate the
complete source-built runtime in Actions; do not repeat that partial substitution.

ANGLE's Metal backend supplies ES 2/3, and rejects the desktop EGL API. MGL's
current source has incomplete integer attributes, enumeration, context ownership
and legacy support. Neither was shipped or used to claim full desktop OpenGL
compatibility. Primordialis rendering remains unresolved; an alias fix does not
add modern GLSL or every desktop API. Evidence: `docs/graphics-opengl-backends.md`.

### Vulkan and Direct3D 9

Wine configure is explicitly bound to the bundled
`@rpath/libMoltenVK.dylib`, preventing an unrelated Vulkan SDK on a runner from
silently selecting the shipped backend. Native loading/instance-extension checks
passed, and both Windows ABI probes compiled. Initial sandboxed GPU/device and
Wine executions were unavailable and were not retried in that environment.
See `docs/graphics-vulkan.md`.

Direct3D 9 now builds from `sources/dxvk` in the existing SHA-256-pinned
CodeWeavers archive, for both x86_64 and i386. Three labelled source patches adapt
modern headers and negotiate the optional geometry/cull/fill Vulkan features;
the latter is based on the pinned Sikarugir d9vk source. The unmodified feature
requests reproduced `vkCreateDevice` failure on MoltenVK. Both patched source
DLLs passed PE machine/export/import checks. Their Wine builtin marker makes
runtime-folder loading work; the original DLLs remain `d3d9.dll.wined3d`.

Historical local logs, before the stop instruction, show both source DLLs passing
the tiny clear/triangle GPU readback on MoltenVK 1.4.2: pixels `0xff123456` and
`0xff3366aa` matched. Installation fixtures preserved the original bytes and
included the license/source notices. These results are not full CI-runtime,
Steam game, presentation, performance or installer validation. See
`docs/graphics-d3d9.md` and `NOTICE.md`.

A later, lower-priority single-test attempt with a 20-second deadline stopped
before spawning Wine: the temporary test runtimes no longer existed. No graphics
process was launched. Use a fresh CI-built runtime for the next authorized test.

### Cloud validation

Existing Wine build run 38489527625 (commit `52c59e8`) completed successfully.
Its configure/build fixes were preserved. Branch `codex/graphics-cloud` adds Linux
source validation plus macOS compilation, bounded complete-runtime probes and
uploaded text evidence. Each Wine probe has a deadline and an isolated CI prefix;
cleanup stops its server. A missing hosted-runner GPU is recorded as a skip,
never a passed rendering check. Linux source checks passed on commit `a483e56`
(run 38498349195). The first macOS graphics build was dispatched as run
38498386464. Wine and both Direct3D 9 DLLs built; the job then failed because a
`lipo -verify_arch` check put the dylib path after its variadic architecture list.
The filename is now before that option. Complete-runtime results remain pending.
Follow-up review tightened
server cleanup and requires an explicit unavailable exit code for GPU skips.

## Installation recovery and notifications (2026-10-14)

Happy Wheels (4705510) had a missing installation directory, but its manifest still
claimed fully installed (`StateFlags=4`, `SizeOnDisk=0`, empty `InstalledDepots`).
The client had refreshed its metadata to `windows,linux` at 10:13:37, removing depot
4705511 from the selected depots. Verification at 10:23:41 selected no target depots
and Steam committed deletion of 426 files. This is direct evidence of the metadata
refresh hazard, in addition to the owner's manual removal of the game folder.

Steam was closed through its own `-shutdown` command. Gluon sync restored the
refreshed entries and the stale Happy Wheels manifest was moved, without deletion,
to `backups/install-4705510-20261014-103947/appmanifest_4705510.acf`. After reopening
Steam, its library index no longer listed Happy Wheels as installed; the owner
confirmed the Install button was enabled. No saves or compatibility prefixes were
removed and the game was not launched.

`gluon/install.py` now recovers the same state for managed games during offline
sync: it preserves a fully installed manifest under a unique sibling filename when
the installation directory is confirmed absent. Existing folders, dangling links,
pending downloads, inaccessible or unmounted libraries and ambiguous manifests
are left alone. Steam process-inspection errors now prevent file edits.

`gluon/metadata.py` adds a persistent update preference for installed managed games:
Steam's ordinary update-on-launch setting (`AutoUpdateBehavior=1`). The current
client's `steamui/sp.js` module 71670 maps its update-on-launch label to enum value 1;
Valve describes the setting in its [download and update documentation](https://help.steampowered.com/en/faqs/view/71AB-698D-57EB-178C).
The manifest is backed up, original preferences are recorded, and disabling Gluon
restores the preference only if the owner has not since changed it. This defers
background updates; it cannot prevent manual Verify or a launch-time update from
reconciling depots after a live metadata refresh. New installations receive this
guard on the next offline sync; in-progress downloads are never rewritten.

The background helper now checks a changed on-disk cache while Steam runs and warns
once per changed drift set. It never edits Steam's files while the client runs.
The incident's `appinfo_log.txt` records a successful refresh write at 10:13:42,
so this incident is detectable from disk. Detection follows the helper's minute
interval; immediate detection of an in-memory-only change is not established.
Hardcoding a future change number or locking the shared cache is not used: those
would not reliably prevent an in-memory replacement and could affect native games.

Gluon's launcher clears Steam's native overlay library before Wine. The Steam API
bridge stores achievements: local caches decode 14 Happy Wheels unlocks and 17
People Playground unlocks. A custom cache watcher and AppKit banner prototype was
installed and detected the real achievement an unlock, but the owner
heard its sound without seeing its fullscreen panel. A window-ordering fix was
previewed once. The owner rejected this substitute and requires Steam's actual
achievement notifications and overlay. The custom helper and its package/launcher
integration were removed; previous installed files were preserved under
`backups/removed-custom-notifications-20261014-110905`. No active custom helper
remained at removal. Do not restore this prototype or show another custom banner.
Real Steam overlay support remains unresolved.

The installation/metadata/process fixtures passed as part of the earlier 63-test
suite; that count included the removed banner prototype. No Wine, game, OpenGL or
Vulkan process was started by those fixture tests. Game sessions above were the
owner's own tests. Source and packaging changes remain uncommitted.

The owner stopped REA after it selected the configured Hopper provider. The
Hopper query failed with Apple-events authorization denied and unconfirmed target
cleanup; it was not retried. All research agents were interrupted. The subsequent
process check showed no running Hopper or Ghidra/Java analysis process. Future REA
native analysis must explicitly use Ghidra, never Hopper; do not resume the stopped
analysis without the owner's instruction.

The owner subsequently resumed the investigation and explicitly requested REA's
command-line tools. The connected MCP instance lacked `GHIDRA_INSTALL_DIR`; the
installed CLI was used with the existing `a local Ghidra 12.1.4 installation`, explicit
`--provider ghidra`, a 512 MB headless heap, one available CPU and reduced priority.
Every native invocation had a 60-second external deadline and a 45-second startup
deadline. REA evidence confirms Ghidra 12.1.4; Hopper was not used after resumption.
No Ghidra installation or native build was required: its Mac arm64 decompiler was
already present in `Ghidra/Features/Decompiler/build/os/mac_arm_64/decompile`.

The official Steam overlay's x86_64 slice was analyzed statically, without executing
it. Its initializer reads `SteamGameId` and `SteamOverlayGameId` and creates native
per-PID overlay streams/events. Its NSApplication category initializes native Metal
hooks for CAMetalLayer nextDrawable and the actual command-buffer class's present,
commit and scheduled-handler methods. Those are the native methods reached by
DXMT and MoltenVK, so rejecting the Mac overlay merely because the guest is Windows
was not supported by the evidence. Runtime compatibility still needs validation.
See `docs/graphics-steam-overlay.md` for the artifact identity and REA evidence.

`gluon/overlay.py` validates Steam's supplied insertion list against the actual
client directory and x86_64 Mach-O dylib headers. The launcher clears insertions
before its helpers, then restores the validated list only on the final direct
plain-Wine launch. Steam IDs are preserved. The Proton wrapper path is unchanged;
its own shell/Python boundary needs separate integration. Wine may implicitly
spawn server/child processes inheriting the final native environment; explicit
setup, registry and server commands remain clear. This uses Valve's actual overlay,
with no custom banner, sound or achievement modifications, and no redistributed
Valve binary. The reviewed helper and launcher are installed for the owner's next
Play test. Real fullscreen popup/sound success is not yet established.

The updated local suite ran 53 tests in 0.048 seconds: 50 passed and three Linux-only
shell-routing fixtures were skipped because macOS SIP purges DYLD variables from
shell interpreters. Header validation accepted the actual installed Valve library
pair without loading native code. Linux CI must run the routing fixtures.

The owner requires the original Steam game page and Play button. The alternative
of Gluon-owned installations with local Steam shortcuts was declined. No shortcuts
or separate content store were created. Update-on-launch plus refresh detection is
a mitigation, not a guarantee against Steam replacing its in-memory app metadata.

The owner's restored-overlay Play test did not show Shift+Tab. A later passive
file inventory showed the Valve renderer as an open file, not mapped executable
code, while DXMT was mapped in the main Happy Wheels process. REA CLI signature
inspection found that both development Wine executables are signed with Hardened
Runtime but lack the DYLD environment entitlement. Library-validation permission
alone does not enable DYLD insertion. The source-built Actions runtime should be
verified and selected for Happy Wheels on its next Play, retaining the old runtime
and original prefix. No Steam binary, system protection or vendor signature has
been changed. See the overlay investigation for exact hashes and evidence IDs.

The verified Actions package and its archive match their published SHA-256s.
Its complete runtime and Steam bridge were staged as `runtime/gluon-wine-186bfc4`.
REA signature inspection confirms both native Wine executables are unsigned;
they do not have the development runtime's Hardened Runtime signing gate.
Happy Wheels' per-game settings select this runtime on the next normal Play.
The default runtime link and original prefix were left intact, and the selection
is reversible from `backups/happy-runtime-20261014-153843`. No installer script,
Wine executable or game was started by this deployment. The owner subsequently
confirmed that Steam's actual overlay and achievement popup both appear during
the requested Happy Wheels fullscreen test. Launch logs confirm the source-built
runtime, original executable/prefix, original Steam IDs and native overlay request;
user sessions at 16:09:55 and 16:10:54 ended successfully after 52 and 109 seconds.
The visible result is owner-confirmed, not inferred from those logs. No custom
banner or artificial achievement changes were used. The signing-policy correction
and native insertion restoration resolve this Happy Wheels notification failure.
Other games and OpenGL/Vulkan overlay paths have not been tested.

The build now checks the signing policy of all present native Wine host paths
before runtime probes. Static inspection accepts the staged runtime and rejects
the old development loader. All 62 local fixtures completed in 0.078 seconds:
59 passed and three Linux-only launcher fixtures skipped. No Wine or graphics
code is loaded by the new signature/path tests.

The build for commit `186bfc4` is green:
https://github.com/mzyhot/gluon/actions/runs/38600823639 . Source checks
https://github.com/mzyhot/gluon/actions/runs/38600824537 passed all 53 tests,
including the three launcher fixtures skipped locally. Cloud runtime checks
passed Vulkan device/loader checks, Wine prefix setup and D3D9 rendering in both
ABIs. The runner could not create native OpenGL, so OpenGL rendering and WGL alias
rendering were explicitly skipped. The package was built and uploaded. These
results do not establish Primordialis rendering or Steam overlay support.

## Open items

1. **Metadata refresh.** Steam replaces an app's cache entry when its change number
   moves on the server. The app then reverts to Windows-only and Steam would remove its
   depots at the next update. `gluon sync` (run by the packaged agent) re-applies it
   whenever Steam is closed. Managed installed games now defer background updates
   until launch, and a disk-cache refresh produces a restart notification. Manual
   verification or launch-time reconciliation after a refresh remains a limitation.
2. **Library UI.** Tested through `app_install` and `steam://rungameid`. The Install
   button was confirmed enabled by the owner after Happy Wheels installation recovery.
   Platform icons and the remaining library flows are untested.
3. **Steam's Stop button.** `app_stop` and `SteamClient.Apps.TerminateApp` did not
   signal the launcher. Find out how the Mac client stops a game.
4. **Steamworks inside Wine.** The bridge is wired in (see above). Verified only as
   "init error gone" with People Playground; local achievement caches confirm persisted
   unlocks. The owner confirmed Valve's real overlay and achievement popup in Happy
   Wheels fullscreen with the source-built runtime. Other game/backend overlay paths,
   sound level, Workshop and DRM-wrapped titles remain untested.
5. **Install scripts.** Steam logs `RunningInstallScript` but the Windows steps
   (registry keys, redistributables such as Swarm Queen's Adobe AIR) are not run in
   the prefix.
6. **Runtime.** CI build `186bfc4` is green; test its runtime against the same games, then add the
   missing pieces listed under Distribution, and enable and test msync.
7. **Swarm Queen first launch** stops at Steam's EULA dialog, which the user has to
   answer.
8. **Packaging.** The debug bridge (`-cef-enable-debugging`) is a development aid only;
   the enable flow itself is file-based and does not need it.

## Machine state

- `~/Library/Application Support/Gluon/` - `state/apps.json` (original metadata values
  per enabled app), `backups/`, `logs/launch-<appid>.log`, `logs/wine-<appid>.log`,
  `runtime/current`.
- Still present from the retired experiment and unused: `lib/libgluon_hook.dylib`,
  `logs/hook.log`.
- Project Zomboid (108600) and POSTAL (232770) manifests are in `steamapps/`, identical
  to their backups. `parked/` is empty.

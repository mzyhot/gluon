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
not used. An earlier experiment that changed this inside the running client is
retired and removed; Gluon does not modify the Steam client.

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

### Steam Cloud finding - why cloud was off until 2026-10-16

People Playground's cloud rules cover only `Contraptions` (root `gameinstall`). On first
sync the Mac client downloaded every cloud file, including four under `Mods/`. In the
post-exit pass it logged `no longer matches any patterns (2). Removing from cloud` for all
180 files ("2" is Steam's platform flag for macOS), re-persisted the 176 that match the
Contraptions rule, and committed `Forget` for the four `Mods` files in upload batch 48.
Local copies were untouched; they and the contraptions were backed up locally.

Of the managed apps, 38 have auto-cloud rules; most rules use Windows roots
(`WinAppDataLocalLow`, `WinMyDocuments`, ...) and 29 are marked Windows-only. As a macOS
client Steam cannot resolve those, so saves do not sync, and the same "forget" could hit
them. Until 2026-10-16 Gluon removed the `ufs` section from every managed entry
(original kept in the state file, restored exactly by `disable`), which turned Steam
Cloud off for those apps on this Mac only, including games that use only the Steam API
for cloud files.

### Steam Cloud mapping (2026-10-16)

Steam's cloud log shows what the Mac client does with each kind of rule:

- A rule restricted to another OS (`platforms` = Windows or Linux) is logged as
  `Skipping rule N because it does not run on our platform`, and its cloud files as
  `Skipping download of file ... to our platform`; nothing is forgotten (seen for
  POSTAL 2 and Primordialis). Steamworks documents that files of an OS-restricted rule
  are locked to that OS, so these saves cannot sync to a Mac.
- A cross-platform rule with the install-folder root resolves to the same folder on a
  Mac (`Evaluating rule 0 with root="GameInstall"` for People Playground).
- A cross-platform rule with a Windows root has no Mac location; that is where files
  can be forgotten.

`gluon/cloud.py` therefore rewrites the section instead of removing it, using
Steamworks root overrides ("the new location that the Original Root maps to on the
specified OS"; an Add Path is inserted between the new root and the rule's path):

- Every cross-platform rule gets a macOS override for its root. The Windows location
  is the rule's root, or the root a Windows override moves it to (with that override's
  path and transforms), mapped to the game's Wine profile: `Documents`,
  `AppData/Local`, `AppData/LocalLow`, `AppData/Roaming` and `Saved Games` under
  `<library>/steamapps/compatdata/<appid>/pfx-wine/drive_c/users/crossover` (or `pfx`
  and `steamuser` for a Steam Play tool runtime). The override uses `MacAppSupport` or
  `MacHome` plus the profile's path below it.
- The developer's own macOS overrides are dropped (they describe a native Mac build the
  Windows game does not use); Windows and Linux overrides are kept unchanged.
- OS-restricted rules are left as they are. Sections without rules (Steam API files)
  are kept as they are.
- A rule Gluon cannot place (an unknown root, an unknown override key or comparison, a
  move into the install folder, a prefix outside the home folder or a prefix with two
  user profiles) keeps the whole section out, as before. `GLUON_CLOUD=0` in the
  settings files does the same for one game or all games.
- The mapping is idempotent; Gluon recognizes its own overrides (a macOS override into
  `drive_c/users/`), so the state file always keeps Steam's section for `disable`.
  A section left out by an earlier Gluon is brought back mapped until Steam refreshes
  the entry, which supplies Steam's current section.
- Before the first mapped sync, the helper copies Steam's cloud records
  (`userdata/<user>/<appid>`) and each mapped rule's local folder to
  `$GLUON_HOME/backups/cloud/<appid>-<time>/`, up to 256 MB per game; larger folders are
  skipped and named in the helper log. A failed backup keeps cloud off for that game.

A dry run over this Mac's 49 managed games with cloud sections mapped 13, kept 28 with
install-folder or OS-restricted rules only, kept 8 that use only the Steam API, and left
none off. Needs an owner run: a mapped game's first launch (download into the prefix)
and exit (upload) in Steam's cloud log.

### 32-bit Mac games (2026-10-16)

Steam marks a Mac build it can run with `macos64` or `macosapplesilicon` in
`common/osextended`; a Mac build without either is 32-bit only and shows "Invalid OS
Type" (HITMAN World of Assassination lists only `macosapplesilicon` and is native).
This library has 46 such apps with a Windows build, including Portal 2, Team Fortress 2,
Left 4 Dead 2, Garry's Mod and Hotline Miami. Their Mac depots and launch entries carry
`oslist` `macos` (for example Portal 2's depot 623 and launch entry 3).

Gluon now manages them like Windows-only games: `macos` is added to their Windows depots
and launch entries and `macos64` to `osextended`, and their Mac-only depots and launch
entries are removed from the cached entry (recorded whole in the state file, restored by
`disable`), so Steam installs and starts the Windows build. Shared depots without an
`oslist` stay. Cloud rules restricted to macOS describe the Mac build and keep their
locations and the developer's macOS overrides; rules the Windows build uses are mapped
as for Windows-only games. Games with a 64-bit or Apple silicon Mac build are never
touched. None of the 46 were installed when this was added.

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

Local graphics testing stopped after the development Mac overheated. Do not
repeat the failed native-module experiments or start parallel local probes.
Compilation stays on GitHub Actions. A local test, when one is needed, runs one
at a time with reduced workload/priority, a hard timeout, immediate stop on
failure, and prefix-specific cleanup.

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

Happy Wheels (4705510) had a missing installation directory because the owner had
deleted the game, but its manifest still claimed fully installed (`StateFlags=4`,
`SizeOnDisk=0`, empty `InstalledDepots`). Steam had also refreshed its metadata to
`windows,linux` at 10:13:37.

Steam was closed through its own `-shutdown` command. Gluon sync restored the
refreshed entries and the stale Happy Wheels manifest was moved, without deletion,
to `backups/install-<appid>-<timestamp>/`. After reopening
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
bridge stores achievements: local achievement caches confirm persisted unlocks for
Happy Wheels and People Playground. A custom cache watcher and AppKit banner
prototype detected a real unlock, but its fullscreen panel was not visible. This
substitute was rejected: Steam's actual achievement notifications and overlay are
required. The custom helper and its package/launcher integration were removed.
Do not restore this prototype or show another custom banner.
Real Steam overlay support remains unresolved.

The installation/metadata/process fixtures passed as part of the earlier 63-test
suite; that count included the removed banner prototype. No Wine, game, OpenGL or
Vulkan process was started by those fixture tests. Game sessions above were the
owner's own tests. Source and packaging changes remain uncommitted.

Native static analysis uses Ghidra 12.1.4 only, with a 512 MB headless heap, one
CPU, reduced priority and short startup and external deadlines.

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
is reversible. No installer script,
Wine executable or game was started by this deployment. The owner subsequently
confirmed that Steam's actual overlay and achievement popup both appear during
the requested Happy Wheels fullscreen test. Launch logs confirm the source-built
runtime, original executable/prefix, original Steam IDs and native overlay request;
both recorded test sessions ended successfully.
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

## Graphics runtime update (2026-10-14)

The owner requested the OpenGL and Vulkan translation fixes after confirming
Happy Wheels' real Steam overlay. The installed `gluon-wine-186bfc4` contains
the OpenGL aliases and bundled MoltenVK/D3D9 changes, but the shared default
still selects the older development runtime. No local graphics test was launched.

Source review found a native Vulkan requirement missing from Wine: the host
advertises `VK_KHR_portability_subset`, but Wine's filtered device-extension
table omitted it. The new native-only patch detects host support, queries the
15 supported portability feature bits and preserves the original feature chain
when enabling the required extension. Vulkan 1.0 callers receive the host's
properties2 query prerequisite when supported. Generated Windows declarations
and shared driver ABI stay intact. The exact private layout and structure type
are checked against pinned Khronos XML, with five negative fixtures. The next
Actions run must compile it and verify API 1.0 device creation plus native-enabled
extension logs and D3D9 readback in both ABIs. See `graphics-vulkan.md`.

The older Actions build omitted dependencies present in the development runtime.
Before replacing the shared runtime, the builder now restores source-built TLS,
GStreamer and SDL plus pinned official shared Wine Mono/Gecko releases. TLS and
media dependency closures are relocated and checked; no commercial-product
binaries are copied. New Windows provider/module checks cover both ABIs, with
no network or rendering in those checks. Source build and real runtime results
for this update remain pending. The earlier green artifact remains intact.

Packages now use an Actions-run/attempt-specific runtime directory and stage a
pending selection instead of overwriting `runtime/current`. Offline sync defers
while Steam or Wine is active, then atomically replaces the default symlink with
an exact previous-target backup. Old runtime directories and game prefixes are
preserved. Ten fixtures cover idle installation, activity/inspection failures,
late Steam startup, dry runs and invalid requests. The complete local fixture
suite passed 72 tests (three platform skips); no native code was executed.

## Launcher recovery and lifecycle fixes (2026-10-14)

The latest Primordialis (`3011360`) attempts in `launch-3011360.log` failed before
Wine with `no runtime found`. The installed v0.1.0 directory and `pending.json`
were present, but `runtime/current` was absent. The updater waited for Steam to
quit even to create the initial selection. These attempts provide no new OpenGL
rendering evidence; the retained `glVertexAttribIPointer could not be loaded`
game log is from the earlier runtime.

Runtime selection now permits creating a missing default while Steam is open.
It validates the packaged request and complete Wine/server layout, records the
selection, and creates the link exclusively so a concurrent selection cannot be
overwritten. Existing selections, including dangling links, retain the idle update
rules. The launcher can invoke a bootstrap-only helper if no runtime is available;
dry runs do not create the default. Startup/setup failures now produce a Gluon
notification with the error and launch-log path.

Wineboot, registry setup, and server waits now run as tracked background jobs with
interruptible shell waits. A delivered TERM/INT/HUP stops the owned helper, kills
only the selected Wine prefix, waits for server shutdown, and reaps the helper.
The shell defers cancellation across child-PID capture and checks its live job
table before signalling a delayed helper. Failed registry setup no longer marks
the bridge initialized or starts the game. This hardens delivered signals; Steam's
actual Stop-button signal path remains unverified.

For the modern plain-Wine native-loader layout, the launcher supplies the runtime
DLL root through `WINEDLLPATH`, preserving a nonempty custom value. The pinned
Wine loader uses this variable to enable its existing descriptive process links
and embedded application-menu naming. Proton retains its own configuration.
Actual Dock/menu appearance still needs a normal owner-run game session. Initial
environment logging now shares one timestamp instead of invoking `date` per line.

The local offline suite ran 97 tests: 94 passed and three existing Linux-only
overlay fixtures were skipped on macOS. Sixteen new launcher fixtures cover
startup/naming, bootstrap, notification routing, and cancellation during setup,
gameplay, and server waits, including a wrapper that ignores TERM. All runtime
executables and notification handlers in those fixtures are Python recorders.
No Wine, game, native graphics probe, runtime build, or renderer was executed.

The tested launcher and runtime-selection module were installed on the development
Mac with the originals backed up. During this work the
existing background helper independently selected
`gluon-runtime-0.1.0-38653382448.1` after Steam became idle. Its `current` link is
present. No game session was started to verify rendering or the Steam Stop UI.

## Terminal executable suffix in Wine application names

The owner confirmed that separate games run simultaneously, but their Dock names
still end in `.exe`. The pinned native Wine loader calculates a shortened display
name and then duplicates the full name, so enabling its named-loader path exposes
that suffix. `wine-macos-display-name.patch` trims only a terminal ASCII `.exe`,
case-insensitively, in the copied display name. It preserves internal dots, other
suffixes, a lone `.exe`, and the executable path supplied to Wine.

`check-wine-display-name.py` extracts that exact patched C helper for an isolated
GitHub Actions Linux fixture. Its 24 cases cover Windows and Unix paths, spaces,
Unicode, existing 32-hex launcher prefixes, empty names and repeated suffixes.
The fixture also checks that the input path remains unchanged and rejects a
mutation reproducing the original suffix bug. Local validation applied and
reverse-checked the patch against the pinned source and checked the source
contract, three negative mutations and the local compilation guard. No helper was
compiled locally. Cloud source checks 38722851299 passed all 24 cases and rejected
the original suffix bug. A complete runtime rebuild and actual Dock appearance
still require validation.

## OpenGL instancing build and native Stop handling (2026-10-15)

The owner supplied Primordialis's next assertion: `glDrawArraysInstanced could
not be loaded`. The promoted-alias patch now tries the registry-defined ARB and
EXT instancing aliases with their existing extension checks. It continues after
a failed candidate and preserves Wine's valid zero-index resolver result.
Complete build 38677914371 at `29b562c` passed module, dependency, Vulkan loader,
Vulkan device, and Direct3D 9 checks for both Windows ABIs. Native OpenGL and WGL
rendering checks were skipped because the hosted runner lacks the CGL prerequisite.
The package artifact digest and its contained package checksum were verified.
Its complete runtime was staged alongside the old runtime and selected only in
`games/3011360.env`, with a rollback record. Rendering still
needs an owner-run retry. No local Wine or native graphics probe was run.

Native static inspection found a Steam process-kill path that starts with
`NSRunningApplication terminate`; a false result can discard the request before
later Unix signal stages. The evidence and its limits are recorded in
`launcher-process-lifecycle.md`. A native accessory application host now keeps
Steam's tracked PID through `exec`, accepts Cocoa quit, resets inherited child
signals, and forwards cancellation only to its owned launcher child. Each host
remains independent. The shell's prefix-specific shutdown helper gives each
server command a five-second deadline, reaps its own helper, and reports an
unconfirmed shutdown as failure. The host has a separate outer deadline.

The local offline suite ran 103 tests: 100 passed and three existing macOS
platform fixtures were skipped. Native host compilation, Cocoa fixtures, and the complete naming runtime
are assigned to GitHub Actions; source checks already passed the exact naming
helper's 24 cases and rejected the original suffix bug. The owner confirmed Steam
Stop works on a fresh session. The launch log records the native host receiving
Cocoa quit, forwarding TERM, and the child entering prefix cleanup. The tested
host/launcher/session module were installed with a rollback record. Dock appearance
still requires the rebuilt naming runtime.

The same owner-run Primordialis retry passed the previous lookup and stopped at
`wglCreateContextAttribsARB` (`code\\gl_functions_list.h:118`). This revealed a
regression in the pre-cache capability check: it checked WGL dispatch entries
against GL extensions. Wine already advertises and implements context creation
through its separate WGL path. The guard is now scoped to GL names, with an exact
resolver fixture and explicit WGL lookups in the runtime probe. The superseded
naming build 38723411770 was canceled; the corrected patch requires a fresh
complete build. The early legacy trace does not prove the game cannot request
its own modern context after function loading.

Follow-up static call-site inspection identified that request as OpenGL 3.3,
flags zero, compatibility profile. The actual pointer is loaded from
`wglCreateContextAttribsARB`; the same list appears in all three executable
variants. macdrv rejects this profile, including when `CX_FWD_COMPAT_GL_CTX=1`
adds the forward-compatible flag. Correct WGL dispatch remains a valid shared
fix, but the game's rendering still needs compatibility-profile support. The
full evidence and limits are in `graphics-opengl-backends.md`.

## Shared runtime activation (2026-10-15)

The corrected complete runtime build
https://github.com/mzyhot/gluon/actions/runs/38725676665 succeeded at `b4fd214`.
Its native launcher fixtures, dependency/module checks, Vulkan loader/device
checks, and Direct3D 9 readbacks passed for both Windows ABIs. Native CGL and WGL
rendering checks were explicitly skipped because the runner lacks the required
CGL capability. A fresh-host recheck of the preserved complete runtime also
succeeded at `90d7608`:
https://github.com/mzyhot/gluon/actions/runs/38728423047 . That recheck verified
archive provenance, source/probe hashes and runtime stamps before running the
same bounded checks without recompiling Wine. Source checks 38725956148 passed
all 103 fixtures, 38 checked aliases with 12 negative cases, the exact resolver
fixture and its WGL regression mutation, and the 24 naming cases.

The verified complete package was staged alongside the old runtimes. After the
owner confirmed Steam was closed, the existing updater activated
`gluon-runtime-0.1.1-dev-38725676665.1` as `runtime/current`. The temporary pins
in `games/3011360.env` and `games/4705510.env` were removed after checking their
installed hashes. All ordinary Gluon launches now follow this shared default;
explicit custom runtime overrides retain priority. Original settings and the
previous default were recorded for rollback. The installed
launcher, session helper, runtime selector, native host checksum and runtime
patch stamp were verified. No local Wine or game was launched for these checks.

Steam Stop is owner-confirmed. The new naming patch still needs an owner-run
Dock check. Primordialis's compatibility-context requirement remains unresolved;
successful module checks and restored WGL lookup do not establish game rendering.

## Steam quit with running games (2026-10-15)

The owner reported that quitting Steam with a game open hangs the same way Stop
did before the native host. Steam's logs for a Primordialis session show
`steam://exit` opening Steam's `Shutdown` popup while the game kept running.
Gluon only began prefix cleanup about 11 seconds later, when `Apps.TerminateApp`
delivered a Cocoa quit; Steam then repeated its exit and shut down.
The host had handled Stop, but nothing connected Steam's own shutdown to it.

Each launcher host now opens a private callback pipe to the user's installed
`steamclient.dylib` on a background thread and stops its game through the
existing TERM cleanup when `SteamShutdown_t` (704) arrives. The game's own pipe
is never drained. If Steam exits without that callback, the host also stops
when the identified owning Steam application terminates. Every running game has
its own host, so quitting Steam stops all of them, while Stop for one game still
leaves the others running. Library, interface or pipe failures are logged and
leave Stop handling unchanged. Blocked Steam IPC cannot delay Stop or host exit.

Cloud fixtures use a controlled `steamclient.dylib` and a fake Steam bundle.
They cover three concurrent games, one stopped first, an unrelated callback, the
shutdown callback, Steam exiting without it, blocked IPC, missing/failing
interfaces and a callback flood. Delivery of 704 by the real Mac client still
needs an owner-run Steam quit; no local Steam, Wine or game was launched.

Recheck run https://github.com/mzyhot/gluon/actions/runs/38761822480 at
`526d904` passed every launcher host fixture. Its `launcher-host` artifact
matched GitHub's recorded zip digest and was installed as
`bin/gluon-launcher-host` while no game was running.
The installed shell launcher already matched the branch. The previous host was
backed up with a manifest. Only a usage
invocation of the installed host was run locally.

## OpenGL compatibility contexts (2026-10-15)

A game asked Wine for an OpenGL 3.3 compatibility-profile context, which macdrv
rejects with `ERROR_INVALID_VERSION_ARB` (8341) because macOS offers 3.x only as
a core profile. A linear-disassembly call-site audit of that game (whole-binary
Ghidra analysis was stopped as too heavy for the local machine) found only
core-profile usage apart from never creating a vertex array object.
`wine-gl-compat-as-core.patch` maps 3.0-4.1 compatibility or
non-forward-compatible requests to Apple's core profile and binds a default
vertex array object per mapped context. The launcher enables it for every game
(`GLUON_GL_COMPAT_AS_CORE=0` opts out); it only changes requests Wine would
otherwise refuse. Details and limits are in `graphics-opengl-backends.md`. The
owner confirmed rendering with build 38776361036.

## One Mac app per game (2026-10-15)

Three owner reports had one cause: Wine games are not Mac apps of their own.
Games opened behind Steam, because since macOS 14 an app may activate only when
the active app yields or LaunchServices launches it, and Steam never yields to a
process it spawned. The menu bar showed `CrossOver-Hosted Application`, the
loader's embedded default name: Wine renames it in memory at the start of
`main()`, but Steam's overlay library links AppKit and Foundation, which load
before `main()` and read the name first. And Game Mode never applied, since
nothing declared a game category.

Each game now gets its own bundle,
`~/Library/Application Support/Gluon/apps.noindex/<appid>/<name>.app`
(`gluon/appbundle.py`), named from the game's app manifest, with
`LSApplicationCategoryType` set to games and `LSUIElement` kept so Wine's helper
processes stay agents. Its executable is a hard link of the runtime's loader:
the owner's first run showed games in front and named in the Dock, but the menu
bar still read `CrossOver-Hosted Application`, because Core Foundation treats a
process as part of a bundle only when it runs the bundle's declared executable,
and the first design declared a launch script. The launcher opens the bundle with
`open -n -W -a`, passing allow-listed Wine, Gluon, Steam and graphics settings
with `--env` and game output through `--stdout`/`--stderr` (merged into the Wine
log afterwards); `-W` waits like the previous child process, and Stop and Steam
quit still end the session through the prefix's server. A bundle opened by hand
starts the loader without a program, which exits. If LaunchServices refuses the
bundle at once, the launcher starts Wine directly.

`wine-macos-app-bundle.patch` makes Wine keep a loader that runs from a bundle,
instead of re-executing through its temporary per-program link, so every
process of the game stays in the bundle; it also enters `GLUON_GAME_CWD`, since
LaunchServices starts apps in `/`. If a window still appears while the game is
inactive, macdrv asks LaunchServices once to activate the bundle and repeats
Wine's cooperative handover (`GLUON_FOCUS=0` turns this off). LaunchServices
does not report a bundle's exit status, so a crash notification after a bundle
launch requires a crash line in the log. Bundles are used only with runtimes
carrying this patch and the overlay-scope patch. Owner-confirmed so far: games
open in front and the Dock shows their names; the menu bar name with the loader
as executable and Game Mode need the next build.

## Steam's overlay limited to game processes (2026-10-15)

The owner saw a game sometimes not close. After one such session a
`winedevice.exe` stayed running for two hours with no server left. The Wine log
shows why: Steam's `steamloader.dylib` and `gameoverlayrenderer.dylib`, inserted
for the game, were inherited by every process the game host started, including
the Wine server and `services.exe`. Steam's IPC code then reported
`fork without exec` in the server and later ended a process with
`fatal stalled cross-thread pipe (pipe is disconnected)`, leaving the prefix
half stopped. `wine-macos-overlay-scope.patch` receives the overlay as
`GLUON_OVERLAY_INSERT` and sets `DYLD_INSERT_LIBRARIES` only when starting a
program outside the prefix's Windows directory; the server never receives it.
Runtimes without the patch still get the overlay through dyld. Steam's toast
animations may have suffered from several overlay instances registering for one
game; that needs an owner check.

## Steam quit callback, msync, keep-awake and settings (2026-10-15)

Steam showed its shutdown popup but never delivered `SteamShutdown_t` to the
host's pipe, so quitting relied on the Steam-exit fallback (about 13 s).
`SteamShutdown_t` is a SteamUtils callback (`k_iSteamUtilsCallbacks + 4`), and
`SteamAPI_Init` obtains SteamUtils for its pipe. The host now requests
`SteamUtils010` (the version paired with `SteamClient020` in Valve's SDK 1.50-1.55
headers, method 9 of `ISteamClient`) on its private pipe after connecting the
user. Failure is logged; the exit fallback remains. Real delivery still needs an
owner-run Steam quit.

`wineserver -k` exits 1 when no server is running, so shutdown success is now
decided by `wineserver -w` returning in time; a server that keeps its lock is
still reported as unconfirmed.

Plain Wine runtimes run with CrossOver's msync (`WINEMSYNC=1`), sharing it with
the server for every Wine command of a session, and `caffeinate -d -i -w` keeps
the Mac and display awake until the launcher exits.

Every feature is on for every game and can be switched off: in a game's Steam
Launch Options (`%command% NAME=value`, applied and removed before the game sees
its arguments, allow-listed names only), in `games/<appid>.env`, or for all
games in `settings.env`. The README lists the switches.

Reference projects reviewed for reusable code: NotProton (GPL-3.0) is not
license-compatible with the Gluon Public License, so only its ideas (a
keep-awake assertion during play, a games category, launching through a
per-game bundle) were reimplemented independently. proton-macos
(BSD-3-Clause) already supplies the Steam bridge; its launcher adds nothing
Gluon lacks. kaon (Apache-2.0) relies on a Windows Steam client in a CrossOver
bottle, which Gluon's native-client approach supersedes.

## Quitting a game's app, graphics versions and Big Picture (2026-10-16)

Quitting a game's app (Cmd-Q, its menu or the Dock) went through Wine's own
path: `WM_QUERYENDSESSION` to the process's windows, then termination of that
process only if every window agreed. A game that does not answer, or the game's
other processes (the Steam helper, launchers), kept running and Steam still
showed it as running. `wine-macos-app-bundle.patch` now makes macdrv ask Gluon's
launcher host (`GLUON_HOST_PID`, set only by the launcher when it runs under the
host and verified with `proc_pidpath` to be `gluon-launcher-host`) to stop the
session, exactly like Steam's Stop. Logout, restart and shutdown keep Wine's
handling. LaunchServices output is now written to separate stdout and stderr
files, because `open` does not append and one shared file let the streams
overwrite each other.

Vulkan versions are passed through: winevulkan reports the lower of Wine's 1.4
and the host's version, and MoltenVK 1.4.2 offers 1.4. The cloud probe gains a
`--device-1.3` mode that requests a 1.3 instance and device with
`dynamicRendering` and `synchronization2`. The hosted runners' paravirtual GPU
reported 1.0/1.1 devices for the existing probes; below 1.3 the new probe reports
itself unavailable and is recorded as skipped, not failed.

OpenGL tops out at the 4.1 core profile Apple ships. Requests for 4.2-4.6 are
still rejected so that games which try newer versions first fall back on their
own; returning 4.1 for them would break games that then use 4.2+ features. More
than 4.1 needs a different OpenGL implementation (for example Mesa's Zink on a
Vulkan driver), which is a separate project.

### Big Picture (owner run, 2026-10-16)

Steam logs its UI mode in `gameprocess_log.txt`: `SSGL: UI mode 4` is Big Picture and
7 the desktop (Big Picture launches also carry `SteamGamepadUI=1`). Big Picture is a
native macOS full-screen Space: opening it with `steam://open/bigpicture` made the Dock
log a `FullscreenSpace`, and Steam Helper's window filled the screen at window level 0.
A window created by another app while that Space is current goes to a desktop Space
behind it, even when that app is active.

A Big Picture launch of Happy Wheels started the game but did not bring it forward.
Quitting Steam logged
`ExecuteSteamURL: "steam://open/steam://exit"` at once, Steam stopped tracking the
game 12 seconds later, and the host received TERM after 18 seconds; the shutdown
callback did not arrive. Fixes:

- The host reads Steam's console log for the exit request and stops the session one
  second after it, unless Steam refuses the exit (see
  [launcher-process-lifecycle.md](launcher-process-lifecycle.md)).
- In Big Picture, `Apps.RaiseWindowForGame` returned in 11 ms at launch, when the
  launcher host was the only tracked process; on the desktop it waits about a second.
  The host now keeps Steam's activation for up to 60 seconds while it stays active,
  and passes it to the game's app as soon as the game's window process exists, so
  macOS shows the Space with the game's window. It logs when Steam activates it, when
  it passes the activation on and when it loses it first.
- The first launch after a runtime update also runs Wine's prefix update (about 13
  seconds for Happy Wheels on 2026-10-16) before the game starts.
- macdrv keeps asking LaunchServices to activate the game's bundle, at most three times
  within four seconds, until one of its windows is active on the current Space, so a
  window on another Space comes forward, and writes each check (active, window shown,
  on the current Space) to the game's session log.
- The metadata helper checks again for a running game after its restart question is
  answered, so "Restart Steam" never quits Steam under a game started meanwhile.

Needs an owner run in Big Picture.

## Metadata refreshes without prompts (2026-10-17)

The owner rejected restart prompts, automatic restarts, frozen change numbers and
update windows, and confirmed that games keep launching after a refresh. What remained
was an update that does nothing, traced in Steam's `content_log.txt` for Happy Wheels:

    22:40:05  appinfo refresh (appinfo_log: Apps changed: 4705510=39927967)
    22:40:06  state changed : Update Required,Fully Installed, (Update delayed for 13638 secs)
    22:40:17  Steam quit; Gluon re-applied its details
    22:40:42  Running Update, Reconfiguring, Verifying Installed
    22:40:42  has no changes, 1 active: 4705511 (491856728672780704), 1 target: 4705511 (491856728672780704)

The refresh flags the installed game for an update, the flag is saved in its app
manifest (`StateFlags` 6 = 4 fully installed + 2 update required, with
`ScheduledAutoUpdate`), and after Gluon's re-applied details the next start runs an
update with no changes. `metadata.settle_update_flags` now runs after each sync while
Steam is closed. For an installed managed game whose manifest is exactly `StateFlags`
6 on the public branch, and whose installed `buildid` and every installed depot
manifest equal Steam's details (`depots/branches/public/buildid`,
`depots/<id>/manifests/public/gid`), it backs up the manifest to
`backups/update-flags/` and sets `StateFlags` 4 and `ScheduledAutoUpdate` 0. Any
other build, manifest, branch or download state keeps the flag, so real updates run
as Steam scheduled them.

The restart prompt and the code that quit and reopened Steam are removed; while Steam
runs, the helper only logs refreshes. Steam hooks or blocking refreshes are not used.
Not yet observed: a session where Steam runs the flagged update before it quits.

## Open items

1. **Metadata refresh.** Steam replaces an app's cache entry when its change number
   moves on the server, and the running client forgets Gluon's changes for that app.
   The owner confirmed that refreshed games keep launching. Gluon no longer prompts or
   restarts Steam: the helper logs the refresh and re-applies its changes at the next
   Steam quit, then clears the leftover update flag (see "Metadata refreshes without
   prompts").
2. **Library UI.** Tested through `app_install` and `steam://rungameid`. The Install
   button was confirmed enabled by the owner after Happy Wheels installation recovery.
   Platform icons and the remaining library flows are untested.
3. **Steam's Stop button.** Fixed through the native host; owner-confirmed on
   a fresh session, with the Cocoa-quit handoff recorded in the launcher log.
   Quitting Steam with games open stops each game when Steam exits (owner-
   confirmed, about 13 s). The host now also stops its game as soon as Steam logs
   its exit request (about one second); that needs an owner-run Steam quit.
4. **Steamworks inside Wine.** The bridge is wired in (see above). Verified only as
   "init error gone" with People Playground; local achievement caches confirm persisted
   unlocks. The owner confirmed Valve's real overlay and achievement popup in Happy
   Wheels fullscreen with the source-built runtime. Other game/backend overlay paths,
   sound level, Workshop and DRM-wrapped titles remain untested.
5. **Install scripts.** Steam logs `RunningInstallScript` but the Windows steps
   (registry keys, redistributables such as Swarm Queen's Adobe AIR) are not run in
   the prefix.
6. **Runtime.** Corrected complete build 38725676665 and its preserved-runtime
   recheck passed, and the shared default is active. OpenGL 3.x compatibility
   requests are mapped to the core profile for every game; general
   compatibility-profile support is still missing. Dock naming is
   owner-confirmed. Per-game app bundles, overlay scoping, msync and keep-awake
   await a complete build and owner runs.
7. **Swarm Queen first launch** stops at Steam's EULA dialog, which the user has to
   answer.
8. **Packaging.** The debug bridge (`-cef-enable-debugging`) is a development aid only;
   the enable flow itself is file-based and does not need it.

## Installed layout

- `~/Library/Application Support/Gluon/` - `state/apps.json` (original metadata values
  per enabled app), `backups/`, `logs/launch-<appid>.log`, `logs/wine-<appid>.log`,
  `runtime/current`.

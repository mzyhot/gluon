# Native Steam overlay investigation

The required result is Valve's actual achievement notification and Steam overlay
inside a game launched from its original Steam library page. A custom banner or
sound is not an acceptable substitute. The rejected custom notification prototype
has been removed. No achievement state should be changed to test this work.

This document records source, binary and launch-log evidence gathered on
2026-10-14, followed by the owner's successful Happy Wheels fullscreen test.
The owner confirmed Steam's actual overlay and achievement popup with the
source-built runtime. No game, Wine session, graphics probe or compiler was
launched by the agent for this investigation.

## Stats and rendering are separate

Valve documents achievement notifications as a Steam overlay feature. Games
set achievement state and store stats; that can generate achievement callbacks
and the overlay notification. The documented graphics backends include Metal,
OpenGL and Vulkan. The overlay needs to attach before graphics initialization.
See [Valve's overlay documentation](https://partner.steamgames.com/doc/features/overlay)
and [StoreStats](https://partner.steamgames.com/doc/api/ISteamUserStats#StoreStats).

Existing Happy Wheels Wine logs show its Windows Steam API loading
`C:\windows\system32\lsteamclient.dll` successfully. The current Steam stats
cache contains its unlocked achievements. These observations establish a
working stats connection, not overlay drawing or sound.

The pinned Proton bridge
[unixlib.cpp](https://github.com/albvalort/Proton/blob/823f91a4215104d3c149d243c827c1152f33ee9b/lsteamclient/unixlib.cpp)
loads the Mac client's `steamclient.dylib` through
`STEAM_COMPAT_CLIENT_INSTALL_PATH`. Its callback path receives native Steam
callbacks and converts them to Windows layouts. It does not implement the
Mac overlay's graphics hooks.

The matching
[Steam helper](https://github.com/albvalort/Proton/blob/823f91a4215104d3c149d243c827c1152f33ee9b/steam_helper/steam.c)
sets up Steam-compatible Windows registry/files and starts the game through
`CreateProcessW` or `ShellExecuteW`. It creates the Windows
`Steam3Master_SharedMemLock` event when `SteamGameId` is present. That Windows
compatibility event is not evidence that the native Mac overlay's IPC is ready.

## The previous injection policy was not a proven incompatibility

At the beginning of this investigation, `tool/gluon` explicitly removed
`DYLD_INSERT_LIBRARIES` before any Wine invocation. Its comment said native
Steam libraries must not reach Wine. That statement came from
[proton-macos run.rs](https://github.com/albvalort/proton-macos/blob/cc1d07a283fed137da82d3bc34152c6776a93288/launcher/src/run.rs),
which removes the same variable by default but exposes
`PMAC_KEEP_DYLD_INSERT=1` for experiments.

The inspected reference source and its status document supply no tested
incompatibility or successful Mac overlay rendering result. Its achievement
milestone demonstrates a Spacewar unlock and public-profile persistence. It
does not demonstrate an in-game achievement popup. Therefore the blanket
incompatibility claim needs verification; it should not be repeated as fact.

Gluon's existing launch logs preserve all three IDs (`SteamAppId`,
`SteamGameId`, `SteamOverlayGameId`). They also consistently contain
`STEAM_DYLD_INSERT_LIBRARIES`, naming the user's official `steamloader.dylib`
and `gameoverlayrenderer.dylib` in the active Mac client folder.

The launcher is a system-shell script. Apple documents that entering protected
system executables purges dynamic-linker environment variables. Removing
Gluon's explicit unset alone might consequently be insufficient: the backed-up
Steam variable may need restoring at the direct native Wine launch boundary.
That is an inference to validate, not a reason to disable SIP or change privacy
permissions. See [Apple's runtime protection documentation](https://developer.apple.com/library/archive/documentation/Security/Conceptual/System_Integrity_Protection_Guide/RuntimeProtections/RuntimeProtections.html).

Published Wine/CX 26.3 source provides a compatible environment path:

- `dlls/ntdll/unix/loader.c` invokes the Mac preloader using `posix_spawn` and
  `_NSGetEnviron()`.
- `dlls/ntdll/unix/process.c` forks before `exec_wineloader`; it inherits the
  Unix environment and supports explicit PE-to-Unix environment promotions.
- Targeted searches of the Wine loader, ntdll Unix process code and winemac
  driver found no named Steam overlay blacklist or `DYLD_INSERT_LIBRARIES`
  sanitizer. This is a bounded source-search result, not proof of compatibility.

These files come from the already pinned
[published CodeWeavers source archive](https://media.codeweavers.com/pub/crossover/source/crossover-sources-26.3.0.tar.gz),
SHA-256 `ac99c8ca4b3848f3e81784135f023df266b61c2345726ea55a50b3e030dd6872`.
No closed CrossOver component is part of this investigation.

## DXMT reaches native Metal hook points

The source inspection uses Gluon's actual DXMT release pin, v0.80, commit
`bc48400148cfd3d720200a3f5486a9d564745394`, rather than the newer checkout HEAD.
Its
[winemetal Unix implementation](https://github.com/3Shain/dxmt/blob/bc48400148cfd3d720200a3f5486a9d564745394/src/winemetal/unix/winemetal_unix.c)
forwards these operations to actual Objective-C Metal objects in the Wine host:

- `_MetalLayer_nextDrawable`: `CAMetalLayer`'s drawable acquisition.
- `_MTLCommandBuffer_presentDrawable` and its duration variant: command-buffer
  drawable presentation.
- `_MTLCommandBuffer_commit`: native Metal command-buffer submission.

The corresponding
[DXMT present path](https://github.com/3Shain/dxmt/blob/bc48400148cfd3d720200a3f5486a9d564745394/src/dxmt/dxmt_context.cpp)
calls those operations. This source mapping means a native Metal hook is a
plausible route; a Windows `.exe` alone does not rule it out. It does not prove
Valve's hook initializes, selects the correct layer, or receives the correct
Steam overlay IPC for this Wine process.

Earlier lightweight static inspection of the user's official installed Valve
overlay found x86_64 and arm64 Mach-O slices, a Metal framework dependency, and
native Metal hook class/selectors including `SteamMetalHook`,
`steamoverlay_nextDrawable`, `steamoverlay_presentDrawable:` and
`steamoverlay_commit`. These are binary observations, not original Valve
source or a runtime trace. The inspected whole-file SHA-256 was
`[hash removed]`.
The Mac client owns these files; Gluon does not redistribute them.

The Vulkan path differs. Pinned
[MoltenVK v1.4.2 MVKImage.mm](https://github.com/KhronosGroup/MoltenVK/blob/v1.4.2/MoltenVK/MoltenVK/GPUObjects/MVKImage.mm)
acquires `CAMetalLayer.nextDrawable`, then its
`MVKPresentableSwapchainImage::presentCAMetalDrawable` installs a Metal
command-buffer scheduled handler. That handler calls the drawable's `present`
or `presentAtTime:` instead of command-buffer `presentDrawable:`. The earlier
Valve overlay selector inventory also included `steamoverlay_addScheduledHandler:`.
This is a candidate hook route to inspect, not proof that native Steam overlay
works through MoltenVK or Gluon's Vulkan-based Direct3D 9 implementation.

## CLI analysis and the initial launch change

REA CLI analysis now used Ghidra 12.1.4 explicitly, with one available CPU,
512 MB headless heap, reduced priority, a 45-second startup deadline and a
60-second external deadline. The original installed Valve library remained
unchanged. A local x86_64 slice was derived for the Wine host architecture;
its SHA-256 is `[hash removed]`.
The proprietary library and raw analysis are local ignored research artifacts,
not package or repository contents.

| REA evidence | Observation |
|---|---|
| `[evidence ID removed]` | Defined strings include the Steam overlay game ID and native Metal hook selectors. |
| `[evidence ID removed]` | Initializer [address removed] reads the game IDs and creates per-PID overlay streams/events; this inspected function contains no Wine rejection. |
| `[evidence ID removed]` | Encoded Objective-C metadata associates NSApplication's `steammetalhook_init` with implementation [address removed]. |
| `[evidence ID removed]` | Routine [address removed] installs hooks on CAMetalLayer drawable acquisition and the native command-buffer class's presentation, submission and scheduled-handler methods. |

The launcher change is installed for a test: it validates Steam's backed-up
insertion list against the current client directory and x86_64 dylib headers,
then restores it on the final plain-Wine native game command. Setup and explicit
server commands remain clear. Implicit child/server processes may inherit the
final Wine environment, so their exclusion is not claimed. Steam's original
game IDs and original library page are retained. No Valve files are shipped,
copied or modified. At this stage the missing loading request was restored;
native loading and visible notifications had not yet been established.

Native binary analysis must use REA CLI with Ghidra exclusively. Hopper is
prohibited by the owner. The source observations above and Ghidra observations
remain separate from runtime claims.

Before a game test, establish the overlay's actual startup gates, the roles of
`steamloader.dylib` versus `gameoverlayrenderer.dylib`, the native process/Steam
ID association, and the exact environment passed to Wine. Loading a dylib is
not a successful notification test. Also account for the Proton tool's protected
shell/arm64-Python wrapper, which differs from a direct plain-Wine invocation.

Further agent-run validation must use one short, bounded game session and stop
on failure. It must demonstrate Valve's actual notification, including fullscreen
behavior, without artificially unlocking or resetting achievements. The owner's
own later test supplies the visible Happy Wheels result below.

## Failed Play test and the signing gate

The owner reported that Shift+Tab did not show an overlay after the restored
insertion request. The launcher logged the validated insertion list, but this
was not a native-load success. A read-only `lsof -Fpcftn` inventory of the owner's
subsequent Happy Wheels session showed DXMT's `winemetal.so`, `dxgi.dll` and
`d3d11.dll` as mapped executable files. The Valve renderer appeared only as
ordinary descriptor 140, including in child processes; neither Valve insertion
library appeared in the mapped executable inventory. An open file is not a
loaded dylib. No debugger was attached and the game was not launched by the agent.

REA CLI signature inspection established a concrete launch-policy blocker:

| Executable | REA evidence | Signing policy |
|---|---|---|
| Development `bin/wineloader` (`[hash removed]`) | `[evidence ID removed]` | Hardened Runtime enabled; no DYLD environment entitlement. |
| Development `lib/wine/x86_64-unix/wine` (`[hash removed]`) | `[evidence ID removed]` | Same restriction on the actual game host. |

Both have `com.apple.security.cs.disable-library-validation`, but lack
`com.apple.security.cs.allow-dyld-environment-variables`. Apple documents that
the latter permits Hardened Runtime executables to read `DYLD_` variables,
including insertion requests. See
[Apple's entitlement documentation](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.allow-dyld-environment-variables).
Thus restoring the environment in the shell does not suffice for this signed
development runtime. This policy accounts for the absent native mapping; native
rendering and IPC still require validation after using a compatible runtime.
Steam, its libraries and system protection settings are not to be modified.

The verified Actions runtime has unsigned native Wine executables, so it has no
Hardened Runtime signing gate. REA evidence
`[evidence ID removed]`
and `[evidence ID removed]`
cover its initial loader and actual native host respectively. Its Steam bridge
and licenses were verified before staging the complete runtime as
`~/Library/Application Support/Gluon/runtime/gluon-wine-186bfc4`.

Happy Wheels now selects that runtime through its per-game settings on the next
normal Steam Play. The old runtime, default runtime link and original game prefix
remain intact; no running game's loader paths were switched. The selection's
rollback record is in `backups/happy-runtime-20261014-153843`. The new runtime
omits Mono/Gecko and several multimedia dependencies, so other games have not
been switched. The owner then tested the real fullscreen overlay on the new
runtime successfully, as recorded below.

`packaging/check-overlay-policy.py` now audits the actual native Wine host
signatures before the build's runtime checks. A hardened executable must have
both DYLD environment and library-validation exceptions; unsigned or unhardened
hosts pass. The static check passes for both new runtime hosts and rejects the
old development loader for its missing entitlement. Nine failure-path fixtures
cover the signing gate and ambiguous inspection results. This prevents packaging
the observed incompatible policy; it does not prove a rendered overlay.

Ghidra analysis of logging function [address removed], evidence
`[evidence ID removed]`,
also established why missing logs alone were inconclusive: native logging is
off unless `STEAM_OVERLAY_LOGGING`, `STEAM_OVERLAY_LOGGING_FLUSH` or frame-time
logging is present. Enabled logs are written to
`/tmp/gameoverlayrenderer.<native-pid>.log`. Ordinary logging may be enabled
for a single diagnostic game session; frame-time logging is unnecessary.

## Owner-confirmed fullscreen result

On 2026-10-14 the owner answered the normal Steam Play / fullscreen Shift+Tab
test: both Steam's actual overlay and an achievement notification appeared.
This closes the reported missing visual notification for Happy Wheels on the
source-built runtime. The rejected custom notification helper remains removed;
no achievement was artificially unlocked or reset by Gluon for this test.

The existing launch log corroborates selection of `gluon-wine-186bfc4`, the
original Happy Wheels executable and prefix, original Steam IDs, the Steam API
bridge and Valve's validated native insertion list. It records user sessions
starting at 16:09:55 and 16:10:54, ending with status zero after
52 and 109 seconds respectively. These logs establish launch configuration and
clean exits; the owner's observation establishes the visible overlay and popup.

This result applies to Happy Wheels' DXMT/Metal path on this Mac. Other games,
OpenGL and Vulkan overlay rendering, sound level and performance have not been
separately validated. Metadata-refresh protection remains a distinct open issue.

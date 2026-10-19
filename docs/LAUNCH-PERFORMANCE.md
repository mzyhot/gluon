# Launch startup and memory diagnostics

The native Steam guard prepares required Cloud paths before Steam synchronizes.
After applying game settings and selecting the final prefix, the launcher runs
one Python helper for its final Cloud check, overlay validation, and app-bundle
preparation. It does not repeat the Cloud check in the compatibility shim.
Games without these features do not start that helper.

The helper logs Cloud and overlay/bundle preparation durations. The native host
records time to shell startup, and default Wine diagnostic lines include elapsed
timestamps. These separate launcher setup from later Wine, Steam IPC, graphics,
and game loading; they do not time the first game window automatically.

The native host starts its owned shell before AppKit registration and Steam
owner lookup. Wine preparation can therefore overlap that native startup work.
The exit-log offset is captured first, and signal handling is installed before
spawning, so an early Steam quit or Stop still ends the same owned session.

For detected Chromium games using in-process GPU presentation, the native overlay
stays in the browser and GPU processes. Renderer, utility and crashpad roles skip
it only when their actual PE image matches the detected game's executable by
file identity. Other executables, unknown roles and ambiguous arguments retain
the overlay. This removes redundant native overlay initialization in background
processes without changing the graphics backend or game files.

Window handoff checks readiness every 50 ms for at most ten seconds. A hidden
window no longer consumes an activation attempt or waits through the old
0.3/1.2/2.5-second backoff. Visible activation requests remain bounded to three,
and switching to another application cancels the delayed handoff. This removes
a possible delay after a window exists; it does not shorten game initialization.

The per-game bundle includes a real ICNS resource and `CFBundleIconFile` using
Steam's locally cached client icon. Conversion happens only when the cached icon
changes. No artwork is fetched during Play. A failed conversion is remembered
until its source changes so it cannot slow every subsequent launch.

CI compares `v0.4.1-fixed` and candidate startup with the same compiled Wine and
the capability manifest used by the installer,
prefix and Windows command fixture. It restarts the Wine server between attempts,
excludes the first launch from each median, and records four samples per version.
The fixture excludes the game, Steam IPC, overlay and GPU. Its result must not be
presented as Happy Wheels' end-to-end launch time.

Native checks include Clang static analysis, Apple's live `leaks` inspection of
an isolated Cocoa host, and a separate Cloud-guard `leaks --atExit` run that
repeatedly replaces its catalogue. ASan/UBSan run separately from Apple's malloc
instrumentation. The host uses a weak timer capture and closes its console-log
descriptor during cleanup. Callback fixtures check private-pipe/user cleanup.
Normal launches with a verified Steam owner and readable exit log do not load
another Steam client SDK into the native host. The existing Cocoa Stop, owner
monitor and exit-log monitor handle shutdown. An unavailable or rotated log
enables the SDK callback fallback, and CI checks that transition explicitly.

The full-runtime job also gates a live Wine native-allocation scan while running
disposable Notepad. A complete report must explicitly confirm zero leaked bytes;
findings, an exited target or a scan error fail the check. The font rejection
patch releases the names and FreeType face owned by a rejected font, including
Apple's Symbol font. A source-extracted sanitizer fixture checks those rejection
paths separately. These scans do not cover a real game's heaps or prove that all
Wine, graphics-driver or Steam-client allocations are leak-free.
Evidence is retained in the native and complete-runtime workflow artifacts.
The native fixtures also block AppKit startup until the real shell has started,
and exercise the exact window-handoff methods with a virtual clock. The complete
runtime checks both PE architectures with an inert native insertion library to
verify overlay initialization is retained or skipped in the intended child roles.

The resolver fallback also frees its temporary hostnames before returning a
canonical-name error, preserving the original error and last-error semantics.
The Steam helper is rebuilt from its pinned public source with balanced launch
buffers, DOS-path allocations, COM references and detached thread handles. Its
bridge DLLs retain their original ABI. Separate source-extracted sanitizer
fixtures exercise repeated resolver failures and helper-managed restarts.

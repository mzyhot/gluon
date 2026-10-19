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

The full-runtime job also records a live Wine native-allocation scan while running
disposable Notepad. Third-party findings or scan errors are reported separately;
they are never called clean. These scans do not cover a real game's heaps or
prove that all Wine, graphics-driver or Steam-client allocations are leak-free.
Evidence is retained in the native and complete-runtime workflow artifacts.

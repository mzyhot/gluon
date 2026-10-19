# Gluon in Steam Settings

Steam > Settings (Preferences on older macOS clients) > Gluon contains **Use
Gluon for 32-bit Mac games**. Turn it on once, then restart Steam to apply it.
Turning it off and restarting removes this automatic default. Existing per-game
compatibility choices, including another tool or No Compatibility Tool, win.

For eligible games Steam uses the Windows version through Gluon, including its
normal depot installation and automatic Steam Cloud preflight. A game with only
a Mac build cannot run through Wine. This setting does not change 64-bit Mac
launches, rewrite app metadata, change save locations or create per-game mappings.

The classifier requires a native Mac launch entry and a Windows launch. It accepts
explicit Mac32 architecture metadata or an installed 32-bit Mach-O executable
(including an app bundle's executable). A 64-bit or Apple Silicon declaration,
64-bit executable or universal binary with a 64-bit slice always vetoes automatic
selection. Legacy Windows EXEs falsely tagged as Mac builds are excluded.

Missing `macos64` metadata is not proof of 32-bit architecture. Games with unknown
architecture stay native until positive evidence becomes available. In particular,
uninstalled old games and script launchers that do not declare their Mac
architecture cannot safely be classified from the local cache alone. The native
catalog refreshes in the background after installation or metadata updates; stale
app metadata is rejected while it refreshes.

The preference lives in `Gluon/steamplay/auto-mac32` in the user's Application
Support folder and persists across installer upgrades. The active Steam process
keeps its startup policy until restart so that its separate Settings and Library
windows agree on launch availability.

`steamplay/check-settings.py` runs only on GitHub macOS runners. It exercises the
built appinfo helper with disposable metadata and Mach-O fixtures, production
preference persistence across process restarts, the actual resolver/Cloud hook
against a fake manager, and the exact Settings UI script against a JSX/DOM bridge.
The existing upstream gate fixtures continue to require exact anchors for both
supported Settings shapes and refuse drifted Steam UI files.

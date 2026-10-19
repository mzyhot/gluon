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
explicit 32-bit architecture on a Mac launch entry, or an installed 32-bit Mach-O
executable (including an app bundle's executable) whose installed Mac/shared depot
manifests match that branch's current versions. The common `osarch=32` field is
not treated as proof of Mac architecture. A 64-bit or Apple Silicon declaration,
64-bit executable or universal binary with a 64-bit slice always vetoes automatic
selection. Legacy Windows EXEs falsely tagged as Mac builds are excluded.

Missing `macos64` metadata is not proof of 32-bit architecture. Games with unknown
architecture stay native until positive evidence becomes available. In particular,
uninstalled old games and script launchers that do not declare their Mac
architecture cannot safely be classified from the local cache alone. The native
catalog refreshes in the background after installation or metadata updates; stale
app metadata is rejected while it refreshes.

Positive observations survive Steam replacing the Mac depots with Windows depots.
The helper saves bounded evidence in `Gluon/state/mac32-evidence.json`, using atomic
writes and a process lock. Each observation is tied to the selected Mac branch,
Mac launch configuration, architecture declarations, and the Mac/shared depot
manifest identities (including resolvable shared-depot references). Windows-only
depot updates and global build IDs do not invalidate it. Mac/shared depot or launch
changes, a branch switch, or any new 64-bit evidence immediately revoke it. An
observation is tied to the installed depot manifests before it is recorded, so
old leftover files cannot attest a newly advertised Mac version. It is exposed to
automatic routing only after it has been persisted;
unversioned Mac content and corrupt or unwritable evidence stay conservative.

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

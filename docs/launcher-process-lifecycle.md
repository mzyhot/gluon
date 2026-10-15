# macOS launcher process lifecycle

Steam tracks the process started by the launch option. A Bash script can remain
alive while Wine runs, but it does not register as a macOS application that can
receive an application quit event. A signal trap alone therefore does not cover
Steam's first termination request.

## Evidence and limits

The installed Steam UI's Stop confirmation calls
`SteamClient.Apps.TerminateApp(gameid, false)`. Its focused REA JavaScript evidence
is `[evidence ID removed]`.
Earlier calls to that API and `app_stop` did not produce a delivered-signal entry
in Gluon's launcher log; injected TERM fixtures did reach the shell trap. These
observations establish a delivery gap, without identifying the native cause by
themselves.

Read-only native inspection used Steam's shipped `steamclient.dylib`, SHA-256
`[hash removed]`, arm64 slice. REA Mach-O inventory evidence is
`[evidence ID removed]`.
The following addresses are unslid arm64 image addresses, observed in static
instruction disassembly and Objective-C selector-reference bytes:

| Location | Observation |
|---|---|

The native path provides a concrete explanation for a shell PID rejecting Stop:
a failed initial Cocoa quit can discard the request before POSIX escalation.
This is a static explanation consistent with the logs; it is not a recording of
the user's Stop click or proof that every Steam termination path uses the same
flags. Symbol presence alone was not used to infer the path.

An additional REA CLI function query selected Ghidra 12.1.4 explicitly, with a
512 MB heap, one available CPU, reduced priority, a 45-second startup deadline and
a 60-second external deadline. Its sandboxed headless launch failed before
analysis. No decompiler result is claimed. Hopper was not used, and no Steam,
Wine or game executable was launched by this investigation.

## Native host

`gluon-launcher-host /absolute/path/to/gluon [original arguments...]` replaces the
initial shell through `exec`, retaining the PID Steam tracks. It registers an
accessory `NSApplication`, which has no Dock icon, then uses `posix_spawn` to start
`/bin/bash` with the launcher and its original argument boundaries. The child
receives `GLUON_LAUNCHER_HOST=1`; the launcher uses that marker to avoid recursion.
Both the shell gate and the native host clear `DYLD_INSERT_LIBRARIES` before
native helpers run. The existing launcher restores only its validated overlay
insertion at the final Wine launch. Other user runtime search paths retain the
launcher's existing behavior.

A Cocoa quit request forwards TERM to that owned child and keeps the application
alive while the shell performs prefix-specific cleanup. TERM, INT and HUP also
reach the same owned child. All child signal dispositions and its signal mask are
reset during spawn, so ignored or blocked signals inherited from Steam cannot
silently disable Bash traps. Signal handlers only record a `sig_atomic_t` flag;
spawn, stop, child observation and application callbacks run on the main thread.
A cancellation recorded before spawn prevents it; one arriving during spawn is
handled immediately after capture of the child PID.

The host polls `waitpid(..., WNOHANG)` ten times per second. It is the only reaper;
an exited child retains its PID until that wait, preventing a signal from racing
with PID reuse. Each host owns one shell and never signals a process group or
another game's prefix. Normal child status is preserved, including `128 + signal`
for signal termination. A 20-second cleanup timeout kills only the owned shell,
reports incomplete cleanup and returns 124; it does not claim that Wine's prefix
has stopped.

`packaging/check-launcher-host.sh` compiles both native architectures and runs
harmless shell fixtures on GitHub's macOS runner only. Fixtures exercise argument
preservation, status propagation, ignored/blocked inherited signals, Cocoa quit
through `NSRunningApplication`, and two independent concurrent hosts. The fixture
process group has a 30-second external deadline. These checks run no Wine or games.

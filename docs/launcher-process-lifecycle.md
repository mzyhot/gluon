# macOS launcher process lifecycle

Steam tracks the process started by the launch option. A Bash script can remain
alive while Wine runs, but it does not register as a macOS application that can
receive an application quit event. A signal trap alone therefore does not cover
Steam's first termination request.

## Evidence and limits

The installed Steam UI's Stop confirmation calls
`SteamClient.Apps.TerminateApp(gameid, false)`. Earlier calls to that API and
`app_stop` did not produce a delivered-signal entry in Gluon's launcher log; injected TERM fixtures did reach the shell trap. These
observations establish a delivery gap, without identifying the native cause by
themselves.

Read-only static inspection of the arm64 slice of Steam's shipped
`steamclient.dylib` found this sequence:

- The app stop path selects an active tracked PID from the game's process record
  and inserts a process-kill request for it. Flags can select alternate or hard-stop
  behavior.
- The request's normal soft-stop path asks Cocoa to quit that PID through
  `NSRunningApplication`'s `terminate` and returns its Boolean result.
- If that result is false, the request is removed instead of being retained.
- Only retained requests reach the later stages, which send INT (2), TERM (15),
  then a hard termination.

The native path provides a concrete explanation for a shell PID rejecting Stop:
a failed initial Cocoa quit can discard the request before POSIX escalation.
This is a static explanation consistent with the logs; it is not a recording of
the user's Stop click or proof that every Steam termination path uses the same
flags. Symbol presence alone was not used to infer the path.

An additional decompiler query failed before analysis, so no decompiler result
is claimed. No Steam, Wine or game executable was launched by this investigation.

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

## Steam quit

Quitting Steam does not send games a Cocoa quit. Steam's logs show `steam://exit`
opening its `Shutdown` popup and waiting while the game keeps running; cleanup
only began after a separate `Apps.TerminateApp`. Steamworks instead delivers
`SteamShutdown_t` (callback 704) to connected clients, which many games ignore.

When Steam launched the game (`SteamClientLaunch=1`), the host loads the user's
installed `steamclient.dylib` from the `STEAM_CLIENT_CONFIG_FILE` directory (or
`STEAM_COMPAT_CLIENT_INSTALL_PATH`) on a background thread. It creates its own
`SteamClient020` pipe, connects to the existing global user and polls only that
pipe, at most 32 callbacks per 100 ms. On 704 the main thread performs the same
TERM request as a Cocoa quit. The game's callbacks remain on its own pipe.

As a fallback, the host identifies the running `com.valvesoftware.steam`
application whose executable is that client's `steam_osx` (or the bootstrapper
named by `STEAM_APP_BUNDLE_PATH`). It never guesses between two matches. If that
application terminates, the host stops its session. Each game has its own host,
so quitting Steam stops every game, and Stop for one game leaves the others.

Steam IPC never runs on the main thread. A missing library, interface or pipe is
logged and leaves Cocoa/signal Stop unchanged. At exit the host waits at most one
second for the watcher to release its pipe, then exits without running image
destructors, because Steam's own threads may still be active. Gluon distributes
no Steam library; `tools/gluon-steam-client.h` declares only the ABI prefix used.

## Cloud fixtures

`packaging/check-launcher-host.sh` compiles both native architectures and runs
harmless shell fixtures on GitHub's macOS runner only. Fixtures exercise argument
preservation, status propagation, ignored/blocked inherited signals, Cocoa quit
through `NSRunningApplication`, and two independent concurrent hosts.

A controlled `steamclient.dylib` and a fake Steam bundle then cover Steam quit:
three concurrent games where one is stopped first, an unrelated callback that
must not stop games, the shutdown callback stopping the rest, Steam exiting
without a callback, a blocked IPC call, missing or failing interfaces, and a
callback flood. The fixture process group has a 60-second external deadline.
These checks run no Steam, Wine or games.

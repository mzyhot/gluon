#!/usr/bin/env python3
"""Harmless native host fixtures; runs only on GitHub's macOS runner."""
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

# A host connected to Steam records these around its private callback pipe.
USER_CONNECTED = ["loaded", "create-interface", "create-pipe", "connect-user"]
CONNECTED = USER_CONNECTED + ["get-utils:SteamUtils010"]
RELEASED = ["release-user", "release-pipe", "release-thread-memory"]


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "darwin":
        raise SystemExit("Launcher host fixtures require a GitHub Actions macOS runner.")
    if len(sys.argv) != 6:
        raise SystemExit("Usage: test-launcher-host.py HOST CONTROLLER STEAM_OWNER "
                         "STEAM_CLIENT STEAM_CLIENT_MISSING_CALLBACK")
    if os.getpgrp() != os.getpid():
        raise SystemExit("Run through check-launcher-host.sh in its owned fixture process group.")
    host, controller, steam_owner, steam_client, steam_client_missing = (
        str(Path(arg).resolve()) for arg in sys.argv[1:])
    active = []
    deadline = time.monotonic() + 60

    def left():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Launcher host fixtures exceeded 60 seconds")
        return remaining

    def wait(process, expected):
        code = process.wait(timeout=min(5, left()))
        if code != expected:
            raise AssertionError(f"Host returned {code}, expected {expected}")

    with tempfile.TemporaryDirectory(prefix="gluon-launcher-host-") as directory:
        root = Path(directory)
        log = (root / "host.log").open("w+")
        stub = root / "launcher stub.sh"
        stub.write_text('''#!/bin/bash
set -eu
[ "${GLUON_LAUNCHER_HOST:-}" = 1 ]
[ -z "${DYLD_INSERT_LIBRARIES:-}" ]
mode="$1"
result="$2"
shift 2
if [ "$mode" = status ]; then
    [ "$#" = 2 ] && [ "$1" = 'one argument' ] && [ "$2" = '*.exe' ]
    exit "$result"
fi
trap 'printf "TERM\\n" >> "$result"; exit 143' TERM
trap 'printf "INT\\n" >> "$result"; exit 130' INT
trap 'printf "HUP\\n" >> "$result"; exit 129' HUP
printf 'ready\\n' > "$result.ready"
while :; do /bin/sleep 0.05; done
''')

        def spawn(*arguments, inherited_ignored=False, environment=None):
            def ignore_signals():
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    signal.signal(number, signal.SIG_IGN)
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
            process = subprocess.Popen([host, str(stub), *arguments],
                stdout=log, stderr=log, env=environment,
                preexec_fn=ignore_signals if inherited_ignored else None)
            active.append(process)
            return process

        def ready(process, name):
            path = Path(str(name) + ".ready")
            while not path.exists():
                if process.poll() is not None:
                    raise AssertionError(f"Fixture exited before ready: {process.returncode}")
                time.sleep(min(0.025, left()))

        def cocoa_quit(process):
            control = subprocess.run([controller, str(process.pid)], stdout=log,
                                     stderr=log, timeout=min(4, left()))
            assert control.returncode == 0, "NSRunningApplication terminate was not accepted"

        def host_output():
            # A separate read leaves the hosts' shared log offset untouched.
            return (root / "host.log").read_text()

        def make_steam(name, client=steam_client, mode=None):
            # Steam.AppBundle/Steam layout: the client library and steam.cfg sit
            # beside the application executable. Only fixture binaries are used.
            bundle = root / name / "Steam.AppBundle" / "Steam"
            macos = bundle / "Contents" / "MacOS"
            macos.mkdir(parents=True)
            with (bundle / "Contents" / "Info.plist").open("wb") as info:
                plistlib.dump({"CFBundleIdentifier": "com.valvesoftware.steam",
                               "CFBundleExecutable": "steam_osx",
                               "CFBundlePackageType": "APPL", "CFBundleName": "Steam",
                               "LSUIElement": True}, info)
            shutil.copy2(steam_owner, macos / "steam_osx")
            shutil.copy2(client, macos / "steamclient.dylib")
            (macos / "steam.cfg").write_text("")
            if mode:
                (macos / "mode").write_text(mode + "\n")
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(("Steam", "STEAM_"))}
            environment.update(SteamClientLaunch="1", SteamAppId="480",
                               STEAM_CLIENT_CONFIG_FILE=str(macos / "steam.cfg"))
            return macos, environment

        def start_steam(macos):
            ready_path = macos / "steam.ready"
            steam_app = subprocess.Popen([str(macos / "steam_osx"), str(ready_path)],
                                         stdout=log, stderr=log)
            active.append(steam_app)
            while not ready_path.exists():
                if steam_app.poll() is not None:
                    raise AssertionError(f"Fixture Steam exited before ready: {steam_app.returncode}")
                time.sleep(min(0.025, left()))
            return steam_app

        def steam_events(macos, process):
            path = macos / f"{process.pid}.client-log"
            return path.read_text().splitlines() if path.exists() else []

        def steam_event(macos, process, event):
            while event not in steam_events(macos, process):
                if process.poll() is not None:
                    raise AssertionError(f"Host exited before Steam {event}: {process.returncode}")
                time.sleep(min(0.025, left()))

        def start_games(macos, environment, prefix, count):
            games = []
            for index in range(count):
                evidence = root / f"{prefix}-{index}"
                games.append((spawn("wait", str(evidence), environment=environment), evidence))
            for game, evidence in games:
                ready(game, evidence)
                steam_event(macos, game, CONNECTED[-1])
            return games

        try:
            for arguments in ([], ["relative-launcher"]):
                invalid = subprocess.run([host, *arguments], stdout=log, stderr=log,
                                         timeout=min(3, left()))
                assert invalid.returncode == 64
            wait(spawn("status", "7", "one argument", "*.exe"), 7)
            print("launcher-host: arguments, marker and exit status passed", flush=True)

            # SIG_IGN and a blocked TERM inherited from Steam must not reach Bash.
            for number, label in ((signal.SIGTERM, "TERM"), (signal.SIGINT, "INT"),
                                  (signal.SIGHUP, "HUP")):
                evidence = root / label
                process = spawn("wait", str(evidence), inherited_ignored=True)
                ready(process, evidence)
                process.send_signal(number)
                wait(process, 128 + number)
                assert evidence.read_text() == label + "\n"
            print("launcher-host: inherited ignored/blocked signals passed", flush=True)

            # Two independent tracked hosts: stop through the same API Steam uses.
            first_log, second_log = root / "first", root / "second"
            first = spawn("wait", str(first_log))
            second = spawn("wait", str(second_log))
            ready(first, first_log)
            ready(second, second_log)
            cocoa_quit(first)
            wait(first, 143)
            assert first_log.read_text() == "TERM\n"
            assert second.poll() is None, "Stopping one host affected another host"
            assert not second_log.exists()
            second.send_signal(signal.SIGTERM)
            wait(second, 143)
            assert second_log.read_text() == "TERM\n"
            print("launcher-host: Cocoa quit and independent concurrent hosts passed", flush=True)

            # Quitting Steam sends SteamShutdown_t to every connected client, then
            # waits for games. Each host stops its own game from its private pipe;
            # Stop for one game still leaves the others running.
            steam, environment = make_steam("quit")
            steam_app = start_steam(steam)
            games = start_games(steam, environment, "steam-quit", 3)
            (first, first_log), remaining = games[0], games[1:]
            cocoa_quit(first)
            wait(first, 143)
            assert first_log.read_text() == "TERM\n"
            assert steam_events(steam, first) == CONNECTED + RELEASED, steam_events(steam, first)
            (steam / "completed").touch()
            for game, _ in remaining:
                steam_event(steam, game, "free:703")
            time.sleep(min(0.3, left()))
            for game, evidence in remaining:
                assert game.poll() is None, "A non-shutdown Steam callback stopped a game"
                assert not evidence.exists()
            (steam / "shutdown").touch()
            for game, evidence in remaining:
                wait(game, 143)
                assert evidence.read_text() == "TERM\n"
                recorded = steam_events(steam, game)
                assert recorded == CONNECTED + ["get:703", "free:703", "get:704",
                                                "free:704"] + RELEASED, recorded
            output = host_output()
            assert output.count("Steam shutdown requested; stopping session") == 2, output
            assert output.count(f"Steam application {steam_app.pid} exit will stop") == 3, output
            assert steam_app.poll() is None, "Fixture Steam exited during callback checks"
            steam_app.terminate()
            steam_app.wait(timeout=min(5, left()))
            print("launcher-host: Steam quit stopped every running game passed", flush=True)

            # Steam exiting without a delivered callback still stops every game.
            steam, environment = make_steam("exit")
            steam_app = start_steam(steam)
            games = start_games(steam, environment, "steam-exit", 2)
            assert host_output().count(f"Steam application {steam_app.pid} exit will stop") == 2
            steam_app.terminate()
            steam_app.wait(timeout=min(5, left()))
            for game, evidence in games:
                wait(game, 143)
                assert evidence.read_text() == "TERM\n"
                assert steam_events(steam, game) == CONNECTED + RELEASED, steam_events(steam, game)
            output = host_output()
            assert output.count("owning Steam application exited; stopping session") == 2, output
            print("launcher-host: Steam exit stopped every running game passed", flush=True)

            # Steam logs an exit request at once, then waits for games before it
            # exits. Each host stops its game from the request; lines from before
            # the session and a request Steam refuses do not count.
            steam, environment = make_steam("console")
            console = steam.parents[3] / "logs" / "console_log.txt"
            console.parent.mkdir()
            exit_line = '[2026-01-01 00:00:0{}] ExecuteSteamURL: "steam://open/steam://exit"\n'
            console.write_text(exit_line.format(0))
            games = start_games(steam, environment, "steam-console", 2)
            assert host_output().count("Steam's exit request will stop this session") == 2
            time.sleep(min(0.3, left()))
            with console.open("a") as output:
                output.write(exit_line.format(1) + "[2026-01-01 00:00:01] src/steamUI/steamurlcontroller.cpp "
                             "(132) : Assertion Failed: CSteamURLController::ExecuteSteamURL exit : "
                             "not allowed yet\n")
            time.sleep(min(1.5, left()))
            for game, evidence in games:
                assert game.poll() is None, "An earlier or refused Steam exit stopped a game"
                assert not evidence.exists()
            # A log line may arrive in more than one write.
            line = exit_line.format(2)
            with console.open("a") as output:
                output.write(line[:40])
            time.sleep(min(0.3, left()))
            with console.open("a") as output:
                output.write(line[40:])
            started = time.monotonic()
            for game, evidence in games:
                wait(game, 143)
                assert evidence.read_text() == "TERM\n"
            assert time.monotonic() - started < 4, "Steam's exit request was not acted on promptly"
            output = host_output()
            assert output.count("Steam is exiting; stopping session") == 2, output
            assert output.count("Steam refused its exit request; session continues") == 2, output
            print("launcher-host: Steam exit request stopped every running game passed", flush=True)

            # A Steam IPC call that never returns must not delay Stop or host exit.
            steam, environment = make_steam("blocked", mode="block-get")
            evidence = root / "steam-blocked"
            game = spawn("wait", str(evidence), environment=environment)
            ready(game, evidence)
            steam_event(steam, game, "blocked-get")
            started = time.monotonic()
            cocoa_quit(game)
            wait(game, 143)
            assert time.monotonic() - started < 4, "Blocked Steam IPC delayed host exit"
            assert evidence.read_text() == "TERM\n"
            assert steam_events(steam, game) == CONNECTED + ["blocked-get"], steam_events(steam, game)
            (steam / "release-block").touch()
            print("launcher-host: blocked Steam IPC passed", flush=True)

            # Missing or failing Steam interfaces leave signal and Cocoa Stop intact.
            cases = [
                ("missing", steam_client_missing, None, ["loaded"],
                 "Steam shutdown callback unavailable"),
                ("interface", steam_client, "interface-fail",
                 ["loaded", "create-interface", "release-thread-memory"],
                 "SteamClient020 shutdown interface unavailable"),
                ("pipe", steam_client, "pipe-fail",
                 ["loaded", "create-interface", "create-pipe", "release-thread-memory"],
                 "Steam shutdown callback pipe could not connect"),
                ("user", steam_client, "user-fail",
                 USER_CONNECTED + ["release-pipe", "release-thread-memory"],
                 "Steam shutdown callback pipe could not connect"),
            ]
            failing = []
            for name, client, mode, expected, _ in cases:
                steam, environment = make_steam("unavailable-" + name, client, mode)
                evidence = root / f"steam-unavailable-{name}"
                failing.append((steam, spawn("wait", str(evidence), environment=environment),
                                evidence, expected))
            for steam, game, evidence, expected in failing:
                ready(game, evidence)
                steam_event(steam, game, expected[-1])
                game.send_signal(signal.SIGTERM)
                wait(game, 143)
                assert evidence.read_text() == "TERM\n"
                assert steam_events(steam, game) == expected, steam_events(steam, game)
            output = host_output()
            for message in {case[4] for case in cases}:
                assert message in output, f"{message!r} missing from host output"
            print("launcher-host: unavailable Steam interfaces passed", flush=True)

            # A pipe without SteamUtils still drains its callbacks and stops on
            # SteamShutdown_t; the Steam exit fallback covers the rest.
            steam, environment = make_steam("utils", mode="utils-fail")
            evidence = root / "steam-utils-unavailable"
            game = spawn("wait", str(evidence), environment=environment)
            ready(game, evidence)
            steam_event(steam, game, "get-utils:SteamUtils010")
            (steam / "shutdown").touch()
            wait(game, 143)
            assert evidence.read_text() == "TERM\n"
            recorded = steam_events(steam, game)
            assert recorded == CONNECTED + ["get:704", "free:704"] + RELEASED, recorded
            assert "SteamUtils010 unavailable on the callback pipe" in host_output()
            print("launcher-host: callback pipe without SteamUtils passed", flush=True)

            # Unrelated callbacks are drained in bounded batches, not a busy loop.
            steam, environment = make_steam("flood", mode="flood")
            evidence = root / "steam-flood"
            game = spawn("wait", str(evidence), environment=environment)
            ready(game, evidence)
            steam_event(steam, game, "free:703")
            before = steam_events(steam, game).count("get:703")
            time.sleep(min(0.5, left()))
            delivered = steam_events(steam, game).count("get:703") - before
            # At most 32 callbacks per 100 ms poll, with scheduling allowance.
            assert 0 < delivered <= 320, delivered
            game.send_signal(signal.SIGTERM)
            wait(game, 143)
            recorded = steam_events(steam, game)
            drained = recorded[len(CONNECTED):-len(RELEASED)]
            assert recorded[:len(CONNECTED)] == CONNECTED, recorded[:8]
            assert recorded[-len(RELEASED):] == RELEASED, recorded[-8:]
            assert drained == ["get:703", "free:703"] * (len(drained) // 2), drained[-8:]
            print("launcher-host: Steam callback flood passed", flush=True)
        finally:
            # These are only the fixture hosts created above. No Wine or game is run.
            for process in active:
                if process.poll() is None:
                    process.terminate()
            for process in active:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            log.seek(0)
            print(log.read(), end="")
            log.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        # The outer driver makes this process its own group leader. While we
        # remain alive the group identity cannot be reused; remove any looping
        # fixture stub left behind by a failed host, including on early failure.
        if os.getpgrp() == os.getpid():
            os.killpg(os.getpid(), signal.SIGKILL)
        raise

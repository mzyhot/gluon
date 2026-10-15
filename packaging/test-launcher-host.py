#!/usr/bin/env python3
"""Harmless native host fixtures; runs only on GitHub's macOS runner."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import traceback


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "darwin":
        raise SystemExit("Launcher host fixtures require a GitHub Actions macOS runner.")
    if len(sys.argv) != 3:
        raise SystemExit("Usage: test-launcher-host.py HOST CONTROLLER")
    if os.getpgrp() != os.getpid():
        raise SystemExit("Run through check-launcher-host.sh in its owned fixture process group.")
    host, controller = (str(Path(arg).resolve()) for arg in sys.argv[1:])
    active = []
    deadline = time.monotonic() + 30

    def left():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Launcher host fixtures exceeded 30 seconds")
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

        def spawn(*arguments, inherited_ignored=False):
            def ignore_signals():
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    signal.signal(number, signal.SIG_IGN)
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
            process = subprocess.Popen([host, str(stub), *arguments],
                stdout=log, stderr=log,
                preexec_fn=ignore_signals if inherited_ignored else None)
            active.append(process)
            return process

        def ready(process, name):
            path = Path(str(name) + ".ready")
            while not path.exists():
                if process.poll() is not None:
                    raise AssertionError(f"Fixture exited before ready: {process.returncode}")
                time.sleep(min(0.025, left()))

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
            control = subprocess.run([controller, str(first.pid)], stdout=log,
                                     stderr=log, timeout=min(4, left()))
            assert control.returncode == 0, "NSRunningApplication terminate was not accepted"
            wait(first, 143)
            assert first_log.read_text() == "TERM\n"
            assert second.poll() is None, "Stopping one host affected another host"
            assert not second_log.exists()
            second.send_signal(signal.SIGTERM)
            wait(second, 143)
            assert second_log.read_text() == "TERM\n"
            print("launcher-host: Cocoa quit and independent concurrent hosts passed", flush=True)
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

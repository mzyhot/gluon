"""Stop only the selected Wine prefix, with bounded shutdown helpers."""
from __future__ import annotations

import argparse
import math
import signal
import subprocess
import sys


def _reset_child_signals():
    # This CLI is single-threaded. The launcher deliberately ignores repeated
    # stop requests during cleanup; do not pass that state into wineserver.
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_SETMASK, [])


def stop_prefix(wineserver: str, timeout: float = 5.0) -> bool:
    """Run -k and -w under the inherited WINEPREFIX, each with its own deadline.

    Only this process's direct shutdown helpers are killed on timeout. Keeping
    child ownership through kill/wait prevents signaling a subsequently reused
    PID; no Wine process discovery or process-group signaling is involved.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("shutdown timeout must be finite and positive")
    successful = True
    for argument in ("-k", "-w"):
        try:
            child = subprocess.Popen([wineserver, argument], preexec_fn=_reset_child_signals)
        except OSError as error:
            print("gluon: cannot start wineserver %s: %s" % (argument, error), file=sys.stderr, flush=True)
            successful = False
            continue
        try:
            status = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print("gluon: wineserver %s timed out after %gs; ending its shutdown helper" %
                  (argument, timeout), file=sys.stderr, flush=True)
            child.kill()
            child.wait()
            successful = False
            continue
        if status:
            print("gluon: wineserver %s failed (exit status %s)" % (argument, status),
                  file=sys.stderr, flush=True)
            successful = False
    if not successful:
        print("gluon: prefix shutdown could not be confirmed", file=sys.stderr, flush=True)
    return successful


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wineserver", required=True)
    args = parser.parse_args(argv)
    return 0 if stop_prefix(args.wineserver) else 1


if __name__ == "__main__":
    sys.exit(main())

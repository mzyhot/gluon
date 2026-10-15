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


def _run_helper(wineserver: str, argument: str, timeout: float):
    """Return the helper's exit status, or None if it could not start or timed out."""
    try:
        child = subprocess.Popen([wineserver, argument], preexec_fn=_reset_child_signals)
    except OSError as error:
        print("gluon: cannot start wineserver %s: %s" % (argument, error), file=sys.stderr, flush=True)
        return None
    try:
        return child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print("gluon: wineserver %s timed out after %gs; ending its shutdown helper" %
              (argument, timeout), file=sys.stderr, flush=True)
        child.kill()
        child.wait()
        return None


def stop_prefix(wineserver: str, timeout: float = 5.0) -> bool:
    """Run -k and -w under the inherited WINEPREFIX, each with its own deadline.

    `wineserver -k` exits 1 both when no server is running and when it could not
    signal one, so only a -w that returns in time confirms the prefix stopped.
    Only this process's direct shutdown helpers are killed on timeout. Keeping
    child ownership through kill/wait prevents signaling a subsequently reused
    PID; no Wine process discovery or process-group signaling is involved.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("shutdown timeout must be finite and positive")
    status = _run_helper(wineserver, "-k", timeout)
    if status:
        print("gluon: wineserver -k exited with status %s (no running server, or it could not "
              "be signalled); confirming with -w" % status, file=sys.stderr, flush=True)
    status = _run_helper(wineserver, "-w", timeout)
    if status:
        print("gluon: wineserver -w failed (exit status %s)" % status, file=sys.stderr, flush=True)
    if status != 0:
        print("gluon: prefix shutdown could not be confirmed", file=sys.stderr, flush=True)
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wineserver", required=True)
    args = parser.parse_args(argv)
    return 0 if stop_prefix(args.wineserver) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Offline shutdown fixtures use only Python children in temporary directories."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from gluon import session


STUB = r'''import json, os, signal, sys, time
argument = sys.argv[1]
event = dict(argument=argument, pid=os.getpid(), prefix=os.environ.get('WINEPREFIX'),
             ignored=[int(number) for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
                      if signal.getsignal(number) == signal.SIG_IGN],
             blocked=[int(number) for number in signal.pthread_sigmask(signal.SIG_BLOCK, [])])
with open(os.environ['GLUON_SESSION_TRACE'], 'a') as stream:
    stream.write(json.dumps(event) + '\n')
mode = os.environ.get('GLUON_SESSION_MODE')
if argument == 'sibling' or mode == 'hang' or (argument == '-w' and mode == 'fail-k-hang-w'):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)
if argument == '-k' and mode in ('fail-k', 'fail-k-hang-w'):
    sys.exit(23)
'''


class SessionShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gluon-session-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trace = self.root / "trace.jsonl"
        self.server = self.root / "Runtime With Spaces/wineserver"
        self.server.parent.mkdir()
        self.server.write_text("#!%s\n%s" % (sys.executable, STUB))
        self.server.chmod(0o755)
        self.environment = mock.patch.dict(os.environ, {
            "WINEPREFIX": str(self.root / "selected prefix"),
            "GLUON_SESSION_TRACE": str(self.trace), "GLUON_SESSION_MODE": "normal",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def events(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def test_shutdown_preserves_prefix_and_resets_inherited_signals(self):
        numbers = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
        dispositions = {number: signal.getsignal(number) for number in numbers}
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, numbers)
        try:
            for number in numbers:
                signal.signal(number, signal.SIG_IGN)
            self.assertTrue(session.stop_prefix(str(self.server), timeout=1))
        finally:
            for number, disposition in dispositions.items():
                signal.signal(number, disposition)
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        events = self.events()
        self.assertEqual([event["argument"] for event in events], ["-k", "-w"])
        self.assertTrue(all(event["prefix"] == os.environ["WINEPREFIX"] for event in events))
        self.assertTrue(all(not event["ignored"] and not event["blocked"] for event in events), events)

    def test_each_timeout_kills_and_reaps_only_its_owned_shutdown_helper(self):
        sibling = subprocess.Popen([str(self.server), "sibling"])
        self.addCleanup(sibling.wait)
        self.addCleanup(sibling.kill)
        os.environ["GLUON_SESSION_MODE"] = "hang"
        output = io.StringIO()
        started = time.monotonic()
        with redirect_stderr(output):
            self.assertFalse(session.stop_prefix(str(self.server), timeout=1))
        self.assertLess(time.monotonic() - started, 4)
        self.assertIsNone(sibling.poll(), "shutdown terminated an unrelated process")
        events = [event for event in self.events() if event["argument"] != "sibling"]
        self.assertEqual([event["argument"] for event in events], ["-k", "-w"])
        for event in events:
            with self.assertRaises(ProcessLookupError):
                os.kill(event["pid"], 0)
        self.assertIn("wineserver -k timed out", output.getvalue())
        self.assertIn("wineserver -w timed out", output.getvalue())
        self.assertIn("prefix shutdown could not be confirmed", output.getvalue())

    def test_kill_status_defers_to_confirmed_server_exit(self):
        # wineserver -k exits 1 when no server is running; -w decides.
        os.environ["GLUON_SESSION_MODE"] = "fail-k"
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertTrue(session.stop_prefix(str(self.server), timeout=1))
        self.assertEqual([event["argument"] for event in self.events()], ["-k", "-w"])
        self.assertIn("wineserver -k exited with status 23", output.getvalue())
        self.assertNotIn("could not be confirmed", output.getvalue())

    def test_failed_kill_with_running_server_reports_failure(self):
        os.environ["GLUON_SESSION_MODE"] = "fail-k-hang-w"
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertFalse(session.stop_prefix(str(self.server), timeout=1))
        self.assertEqual([event["argument"] for event in self.events()], ["-k", "-w"])
        self.assertIn("wineserver -w timed out", output.getvalue())
        self.assertIn("prefix shutdown could not be confirmed", output.getvalue())


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import os
import re
import shlex
import unittest
from unittest.mock import patch

from gluon import __main__ as cli
from gluon import enable, runtime, steam

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SyncWaitForQuitTests(unittest.TestCase):
    def run_sync(self, argv, running=True, quits=True, clock=None):
        args = cli.build_parser().parse_args(["sync"] + argv)
        out = io.StringIO()
        passes = iter([["first pass"], ["after quit"]])
        with patch.object(runtime, "activate_pending", return_value=[]), \
                patch.object(enable, "sync", side_effect=lambda dry_run=False: next(passes)) as sync, \
                patch.object(steam, "is_running", return_value=running), \
                patch.object(steam, "wait_for_exit", return_value=quits) as wait, \
                patch.object(cli, "time") as fake_time, \
                contextlib.redirect_stdout(out):
            fake_time.monotonic.side_effect = clock or [0, 0]
            self.assertEqual(args.fn(args), 0)
        return out.getvalue().splitlines(), sync, wait, fake_time.sleep

    def test_quit_during_the_wait_syncs_again(self):
        lines, sync, wait, sleep = self.run_sync(["--wait-for-quit", "55"])
        self.assertEqual(lines, ["first pass", "after quit"])
        self.assertEqual(sync.call_count, 2)
        sleep.assert_called_once_with(cli.QUIT_SETTLE)

    def test_steam_still_running_at_the_deadline_syncs_once(self):
        lines, sync, wait, sleep = self.run_sync(["--wait-for-quit", "55"], quits=False)
        self.assertEqual(lines, ["first pass"])
        self.assertEqual(sync.call_count, 1)
        sleep.assert_not_called()

    def test_budget_counts_from_the_start_of_the_run(self):
        _, _, wait, _ = self.run_sync(["--wait-for-quit", "55"], clock=[100, 130])
        wait.assert_called_once_with(25)

    def test_closed_steam_is_not_waited_for(self):
        _, sync, wait, _ = self.run_sync(["--wait-for-quit", "55"], running=False)
        wait.assert_not_called()
        self.assertEqual(sync.call_count, 1)

    def test_dry_run_and_manual_sync_never_wait(self):
        for argv in (["--dry-run", "--wait-for-quit", "55"], []):
            with self.subTest(argv=argv):
                _, _, wait, _ = self.run_sync(argv)
                wait.assert_not_called()


class AgentCommandTests(unittest.TestCase):
    def test_agent_arguments_parse_and_fit_the_launchd_interval(self):
        with open(os.path.join(ROOT, "packaging", "gluon-agent")) as f:
            command = [line for line in f if " -m gluon " in line][0]
        args = cli.build_parser().parse_args(shlex.split(command.split(" -m gluon ", 1)[1]))
        self.assertEqual(args.cmd, "sync")
        self.assertTrue(args.log)
        with open(os.path.join(ROOT, "packaging", "scripts", "postinstall")) as f:
            interval = int(re.search(r"<key>StartInterval</key>\s*<integer>(\d+)</integer>", f.read()).group(1))
        # A run must end before launchd's next one is due, or launchd skips that run.
        self.assertGreater(args.wait_for_quit, 0)
        self.assertLess(args.wait_for_quit, interval)


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import call, patch

from gluon import __main__ as cli
from gluon import enable, steam


class SteamProcessTests(unittest.TestCase):
    def test_only_no_match_means_closed(self):
        for code, expected in ((0, True), (1, False)):
            with self.subTest(code=code), patch.object(
                    steam.subprocess, "run", return_value=subprocess.CompletedProcess([], code)):
                self.assertEqual(steam.is_running(), expected)

    def test_denied_process_inspection_fails_closed(self):
        with patch.object(steam.subprocess, "run", return_value=subprocess.CompletedProcess([], 2)):
            with self.assertRaises(steam.SteamError):
                steam.is_running()

    def test_missing_process_tool_fails_closed(self):
        with patch.object(steam.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(steam.SteamError):
                steam.is_running()

    def test_pids_lists_every_client_process(self):
        with patch.object(steam.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, stdout="412\n977\n")):
            self.assertEqual(steam.pids(), [412, 977])
        with patch.object(steam.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="")):
            self.assertEqual(steam.pids(), [])


class SteamExitTests(unittest.TestCase):
    def test_wait_ends_once_steam_has_quit(self):
        with patch.object(steam, "pids", side_effect=[[412], [412], []]), \
                patch.object(steam, "_wait_for_any_exit") as wait:
            self.assertTrue(steam.wait_for_exit(30))
        self.assertEqual(wait.call_count, 2)

    def test_wait_gives_up_at_the_deadline(self):
        with patch.object(steam, "pids", return_value=[412]), \
                patch.object(steam.time, "monotonic", side_effect=[0, 1, 11]), \
                patch.object(steam, "_wait_for_any_exit") as wait:
            self.assertFalse(steam.wait_for_exit(10))
        self.assertEqual(wait.call_args_list, [call([412], 9)])

    @unittest.skipUnless(hasattr(select, "kqueue"), "kqueue process events are macOS/BSD only")
    def test_exit_event_wakes_the_wait(self):
        proc = subprocess.Popen(["sleep", "0.2"])
        self.addCleanup(proc.wait)
        started = time.monotonic()
        steam._wait_for_any_exit([proc.pid], 10)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNotNone(proc.poll())

    @unittest.skipUnless(hasattr(select, "kqueue"), "kqueue process events are macOS/BSD only")
    def test_process_already_gone_does_not_wait(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        started = time.monotonic()
        steam._wait_for_any_exit([proc.pid], 10)
        self.assertLess(time.monotonic() - started, 5)


class ToolCopyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gluon-tool-copy-")
        self.addCleanup(temp.cleanup)
        self.home = os.path.join(temp.name, "Gluon")
        self.tool_dir = os.path.join(temp.name, "compatibilitytools.d", "gluon")
        self.installed = os.path.join(self.home, "bin", "gluon")
        self.copy = os.path.join(self.tool_dir, "gluon")
        self.shim = (Path(__file__).resolve().parents[1] / "tool/gluon-shim").read_text()
        for name, value in (("GLUON_HOME", self.home), ("TOOL_DIR", self.tool_dir),
                            ("INSTALLED_LAUNCHER", self.installed), ("DEV_LAUNCHER", self.copy)):
            patcher = patch.object(steam, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, path, text, mode=0o755):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, mode)

    def status_lines(self):
        out = io.StringIO()
        with patch.object(steam, "is_running", return_value=False), \
                patch.object(enable, "status", return_value=[]), \
                contextlib.redirect_stdout(out):
            self.assertEqual(cli.cmd_status(None), 0)
        return dict(line.split(":", 1) for line in out.getvalue().splitlines() if ":" in line)

    def test_no_copy_is_absent(self):
        self.write(self.installed, "launcher v2\n")
        self.assertEqual(steam.tool_copy_state(), "absent")

    def test_canonical_shim_is_current(self):
        self.write(self.installed, "launcher v2\n")
        self.write(self.copy, self.shim)
        self.assertEqual(steam.tool_copy_state(), "shim")

    def test_full_launcher_is_stale_even_when_identical(self):
        self.write(self.installed, "launcher v2\n")
        self.write(self.copy, "launcher v2\n")
        self.assertEqual(steam.tool_copy_state(), "differs")

    def test_older_copy_differs(self):
        self.write(self.installed, "launcher v2\n")
        self.write(self.copy, "launcher v1\n")
        self.assertEqual(steam.tool_copy_state(), "differs")

    def test_old_copy_is_stale_without_an_installed_launcher(self):
        self.write(self.copy, "launcher v1\n")
        self.assertEqual(steam.tool_copy_state(), "differs")

    def test_shim_reports_a_missing_installed_launcher(self):
        self.write(self.copy, self.shim)
        self.assertEqual(steam.tool_copy_state(), "unavailable")

    def test_shim_reports_a_non_executable_installed_launcher(self):
        self.write(self.installed, "launcher v1\n", mode=0o644)
        self.write(self.copy, self.shim)
        self.assertEqual(steam.tool_copy_state(), "unavailable")

    def test_non_executable_shim_is_unusable(self):
        self.write(self.installed, "launcher v2\n")
        self.write(self.copy, self.shim, mode=0o644)
        self.assertEqual(steam.tool_copy_state(), "unusable")

    def test_packaged_canonical_resource_is_used(self):
        module = os.path.join(self.home, "lib", "gluon", "steam.py")
        self.write(os.path.join(os.path.dirname(module), "tool-shim"), self.shim, mode=0o644)
        self.write(self.copy, self.shim)
        self.write(self.installed, "launcher v2\n")
        with patch.object(steam, "__file__", module):
            self.assertEqual(steam.tool_copy_state(), "shim")

    def test_missing_canonical_resource_cannot_verify_the_shim(self):
        self.write(self.copy, self.shim)
        with patch.object(steam, "__file__", os.path.join(self.home, "lib", "gluon", "steam.py")):
            self.assertEqual(steam.tool_copy_state(), "unknown")

    def test_launch_options_only_name_the_installed_launcher(self):
        # Reloading in a subprocess also covers the missing-launcher import path.
        env = dict(os.environ, GLUON_HOME=self.home, STEAM_ROOT=self.tool_dir,
                   PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-c", "from gluon import enable; print(enable.launch_option())"],
                                env=env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '"%s" run %%command%%' % self.installed)

    def test_enable_refuses_without_an_installed_launcher(self):
        args = cli.build_parser().parse_args(["enable", "123"])
        with patch.object(enable, "enable") as edit:
            with self.assertRaisesRegex(enable.EnableError, "not installed"):
                args.fn(args)
        edit.assert_not_called()

    def test_status_names_a_stale_copy(self):
        self.write(self.installed, "launcher v2\n")
        self.write(self.copy, "launcher v1\n")
        lines = self.status_lines()
        self.assertEqual(lines["Launcher     "].strip(), self.installed)
        self.assertIn("STALE", lines["Steam tool   "])
        self.assertIn(self.tool_dir, lines["Steam tool   "])
        self.assertNotIn("Gluon tool   ", lines)

    def test_status_reports_a_missing_installed_launcher(self):
        self.write(self.copy, self.shim)
        lines = self.status_lines()
        self.assertIn("NOT installed", lines["Launcher     "])
        self.assertIn("shim target unavailable", lines["Steam tool   "])
        self.assertNotIn("IN USE", lines["Steam tool   "])


if __name__ == "__main__":
    unittest.main()

"""The developer probe must be opt-in and bypass the hardened app bootstrapper."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gluon import steam, steam_compat
from gluon.__main__ import build_parser


class SteamCompatProbeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.client = self.root / "Steam Client"
        self.client.mkdir()
        self.executable = self.client / "steam_osx"
        self.executable.write_bytes(b"fixture, never executed")
        self.executable.chmod(0o755)
        self.library = self.root / "probe library.dylib"
        self.library.write_bytes(b"fixture, never loaded")
        self.home = self.root / "Gluon"
        for item in (patch.object(steam, "CLIENT_DIR", str(self.client)),
                     patch.object(steam, "GLUON_HOME", str(self.home))):
            item.start()
            self.addCleanup(item.stop)

    def args(self, *extra):
        return build_parser().parse_args(["steam-probe", "--library", str(self.library), *extra])

    def call(self, args):
        with contextlib.redirect_stdout(io.StringIO()):
            return args.fn(args)

    def test_preview_never_launches_checks_processes_or_creates_files(self):
        self.library.unlink()
        with patch.object(steam_compat.os, "execve") as execute, \
                patch.object(steam, "is_running") as running:
            self.assertEqual(self.call(self.args()), 0)
        execute.assert_not_called()
        running.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_explicit_launch_execs_client_directly_with_private_log(self):
        with patch.object(steam_compat.sys, "platform", "darwin"), \
                patch.object(steam, "is_running", return_value=False), \
                patch.object(steam_compat.os, "chdir") as chdir, \
                patch.object(steam_compat.os, "execve") as execute, \
                patch.dict(os.environ, {"DYLD_INSERT_LIBRARIES": "/old.dylib",
                                        "DYLD_LIBRARY_PATH": "/old", "KEEP_ME": "yes"}):
            self.call(self.args("--launch"))
        chdir.assert_called_once_with(self.client)
        executable, arguments, environment = execute.call_args.args
        self.assertEqual(executable, str(self.executable))
        self.assertEqual(arguments, [str(self.executable)])
        self.assertEqual(environment["DYLD_INSERT_LIBRARIES"], str(self.library))
        self.assertNotIn("DYLD_LIBRARY_PATH", environment)
        self.assertEqual(environment["GLUON_STEAM_COMPAT_PROBE"], "1")
        self.assertEqual(environment["KEEP_ME"], "yes")
        log = Path(environment["GLUON_STEAM_COMPAT_LOG"])
        self.assertEqual(log.parent, self.home / "logs")
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_running_steam_is_refused_without_log_or_exec(self):
        with patch.object(steam_compat.sys, "platform", "darwin"), \
                patch.object(steam, "is_running", return_value=True), \
                patch.object(steam_compat.os, "execve") as execute:
            with self.assertRaisesRegex(steam.SteamError, "quit Steam"):
                self.call(self.args("--launch"))
        execute.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_missing_library_and_unsupported_host_never_launch(self):
        with patch.object(steam_compat.sys, "platform", "linux"), \
                patch.object(steam_compat.os, "execve") as execute:
            with self.assertRaisesRegex(steam.SteamError, "requires macOS"):
                self.call(self.args("--launch"))
        execute.assert_not_called()
        self.library.unlink()
        with patch.object(steam_compat.sys, "platform", "darwin"), \
                patch.object(steam_compat.os, "execve") as execute:
            with self.assertRaisesRegex(steam.SteamError, "library missing"):
                self.call(self.args("--launch"))
        execute.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_missing_client_and_failed_process_check_never_launch(self):
        with patch.object(steam_compat.sys, "platform", "darwin"), \
                patch.object(steam_compat.os, "execve") as execute, \
                patch.object(steam, "is_running", side_effect=steam.SteamError("cannot check")):
            with self.assertRaisesRegex(steam.SteamError, "cannot check"):
                self.call(self.args("--launch"))
            self.executable.chmod(0o644)
            with self.assertRaisesRegex(steam.SteamError, "not executable"):
                self.call(self.args("--launch"))
            self.executable.unlink()
            with self.assertRaisesRegex(steam.SteamError, "missing"):
                self.call(self.args("--launch"))
        execute.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_library_path_cannot_be_a_dyld_list(self):
        with self.assertRaises(steam.SteamError):
            steam_compat.probe_paths("/first.dylib:/second.dylib")


if __name__ == "__main__":
    unittest.main()

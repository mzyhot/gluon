"""Runtime update fixtures preserve running sessions and previous installations."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from gluon import runtime, steam


class RuntimeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gluon-runtime-update-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "Gluon"
        self.base = self.home / "runtime"
        self.base.mkdir(parents=True)
        self.old = self.base / "old-runtime"
        self.old.mkdir()
        (self.old / "preserved.dat").write_bytes(b"old runtime")
        self.new = self.base / "gluon-runtime-0.4.0-dev-123.1"
        (self.new / "bin").mkdir(parents=True)
        (self.new / "licenses").mkdir()
        self.wine = self.new / "bin/wine"
        self.wine.write_bytes(b"fixture, never executed")
        self.wine.chmod(0o755)
        self.wineserver = self.new / "bin/wineserver"
        self.wineserver.write_bytes(b"fixture, never executed")
        self.wineserver.chmod(0o755)
        self.current = self.base / "current"
        self.current.symlink_to(self.old)
        self.pending = self.base / "pending.json"
        self.write_request()
        self.steam = mock.patch.object(steam, "is_running", return_value=False)
        self.process = mock.patch.object(runtime.subprocess, "run", return_value=
                                         subprocess.CompletedProcess([], 1, b"", b""))
        self.steam.start()
        self.process.start()
        self.addCleanup(self.steam.stop)
        self.addCleanup(self.process.stop)

    def write_request(self, **values):
        request = {"schema": 1, "name": self.new.name, "version": "0.4.0-dev"}
        request.update(values)
        self.pending.write_text(json.dumps(request))

    def activate(self, **kwargs):
        return runtime.activate_pending(home=str(self.home), **kwargs)

    def assert_old(self):
        self.assertEqual(os.readlink(self.current), str(self.old))
        self.assertEqual((self.old / "preserved.dat").read_bytes(), b"old runtime")

    def test_idle_update_is_atomic_and_records_exact_previous_selection(self):
        prefix = self.home / "compatdata/1/pfx-wine/save.dat"
        prefix.parent.mkdir(parents=True)
        prefix.write_bytes(b"user save")
        self.assertEqual(len(self.activate()), 1)
        self.assertEqual(os.readlink(self.current), self.new.name)
        backups = list((self.home / "backups").glob("runtime-*/selection.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())["previous"], str(self.old))
        self.assertEqual((self.old / "preserved.dat").read_bytes(), b"old runtime")
        self.assertEqual(prefix.read_bytes(), b"user save")
        self.assertEqual(self.activate(), [])
        self.assertEqual(list(self.base.glob(".current-*")), [])

    def test_first_install_creates_selection(self):
        self.current.unlink()
        self.assertEqual(len(self.activate()), 1)
        self.assertEqual(os.readlink(self.current), self.new.name)
        record = next((self.home / "backups").glob("runtime-*/selection.json"))
        self.assertIsNone(json.loads(record.read_text())["previous"])

    def test_first_install_creates_selection_while_steam_or_wine_is_active(self):
        self.current.unlink()
        with mock.patch.object(steam, "is_running", return_value=True), \
                mock.patch.object(runtime.subprocess, "run", return_value=
                                  subprocess.CompletedProcess([], 0, b"123\n", b"")):
            self.assertEqual(len(self.activate()), 1)
        self.assertEqual(os.readlink(self.current), self.new.name)
        self.assertEqual((self.old / "preserved.dat").read_bytes(), b"old runtime")

    def test_dangling_selection_is_not_a_first_install(self):
        self.current.unlink()
        self.current.symlink_to("missing-runtime")
        with mock.patch.object(steam, "is_running", return_value=True):
            self.assertEqual(self.activate(), [])
        self.assertEqual(os.readlink(self.current), "missing-runtime")
        self.assertFalse((self.home / "backups").exists())

    def test_first_install_does_not_replace_a_concurrent_selection(self):
        self.current.unlink()
        symlink = os.symlink

        def create_concurrent_selection(target, link, **kwargs):
            if Path(link) == self.current:
                symlink("other-runtime", self.current)
            return symlink(target, link, **kwargs)

        with mock.patch.object(runtime.os, "symlink", side_effect=create_concurrent_selection):
            self.assertEqual(self.activate(), [])
        self.assertEqual(os.readlink(self.current), "other-runtime")
        self.assertEqual(list(self.base.glob(".current-*")), [])

    def test_bootstrap_only_keeps_existing_selection_even_while_idle(self):
        self.assertEqual(self.activate(bootstrap_only=True), [])
        self.assert_old()
        self.assertFalse((self.home / "backups").exists())

    def test_bootstrap_cli_creates_missing_selection_and_logs_action(self):
        self.current.unlink()
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(steam, "is_running", return_value=True):
            self.assertEqual(runtime.main(["--bootstrap", "--home", str(self.home)]), 0)
        self.assertEqual(os.readlink(self.current), self.new.name)
        self.assertIn("selected packaged runtime " + self.new.name, output.getvalue())

    def test_bootstrap_cli_keeps_existing_selection(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(runtime.main(["--bootstrap", "--home", str(self.home)]), 0)
        self.assert_old()
        self.assertEqual(output.getvalue(), "")
        self.assertFalse((self.home / "backups").exists())

    def test_bootstrap_cli_reports_validation_failure(self):
        self.current.unlink()
        self.write_request(name="../outside")
        error_output = io.StringIO()
        with redirect_stderr(error_output):
            self.assertEqual(runtime.main(["--bootstrap", "--home", str(self.home)]), 1)
        self.assertIn("invalid packaged runtime request", error_output.getvalue())
        self.assertFalse(self.current.is_symlink())

    def test_steam_or_wine_activity_defers_without_changing_installations(self):
        with mock.patch.object(steam, "is_running", return_value=True):
            self.assertEqual(self.activate(), [])
        with mock.patch.object(runtime.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, b"123\n", b"")):
            self.assertEqual(self.activate(), [])
        self.assert_old()
        self.assertFalse((self.home / "backups").exists())

    def test_denied_process_inventory_keeps_previous_selection(self):
        with mock.patch.object(runtime.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 2, b"", b"denied")):
            with self.assertRaises(runtime.RuntimeSelectionError):
                self.activate()
        self.assert_old()

    def test_steam_starting_just_before_publication_keeps_previous_selection(self):
        with mock.patch.object(steam, "is_running", side_effect=[False, False, True]):
            self.assertEqual(self.activate(), [])
        self.assert_old()
        self.assertEqual(list(self.base.glob(".current-*")), [])

    def test_dry_run_preserves_selection_and_creates_no_backup(self):
        self.assertEqual(len(self.activate(dry_run=True)), 1)
        self.assert_old()
        self.assertFalse((self.home / "backups").exists())

    def test_missing_request_is_a_noop(self):
        self.pending.unlink()
        self.assertEqual(self.activate(), [])
        self.assert_old()

    def test_unsafe_or_malformed_requests_do_not_publish(self):
        for name in ("../outside", "/outside", "current", ".", "", "bad\nname"):
            with self.subTest(name=name):
                self.write_request(name=name)
                with self.assertRaises(runtime.RuntimeSelectionError):
                    self.activate()
                self.assert_old()
        for text in ('{"schema":1,"schema":1}', '{"schema":true,"name":"x","version":"1"}',
                     '{"schema":1,'):
            self.pending.write_text(text)
            with self.assertRaises(runtime.RuntimeSelectionError):
                self.activate()
            self.assert_old()

    def test_symlink_request_or_runtime_is_rejected(self):
        self.pending.rename(self.base / "request-backup.json")
        self.pending.symlink_to("request-backup.json")
        with self.assertRaises(runtime.RuntimeSelectionError):
            self.activate()
        self.pending.unlink()
        self.write_request(name="runtime-alias")
        (self.base / "runtime-alias").symlink_to(self.new, target_is_directory=True)
        with self.assertRaises(runtime.RuntimeSelectionError):
            self.activate()
        self.assert_old()

    def test_incomplete_runtime_and_non_symlink_current_are_kept(self):
        self.wine.chmod(0o644)
        with self.assertRaises(runtime.RuntimeSelectionError):
            self.activate()
        self.assert_old()
        self.wine.chmod(0o755)
        self.current.unlink()
        self.current.mkdir()
        with self.assertRaises(runtime.RuntimeSelectionError):
            self.activate()
        self.assertTrue(self.current.is_dir())

    def test_runtime_without_wineserver_is_rejected(self):
        self.wineserver.unlink()
        with self.assertRaises(runtime.RuntimeSelectionError):
            self.activate()
        self.assert_old()
        self.assertFalse((self.home / "backups").exists())

    def test_proton_runtime_requires_both_wine_and_wineserver(self):
        files = self.new / "files"
        files.mkdir()
        (self.new / "bin").rename(files / "bin")
        proton = self.new / "proton"
        proton.write_bytes(b"fixture, never executed")
        proton.chmod(0o755)
        for name in ("wine", "wineserver"):
            with self.subTest(name=name):
                executable = files / "bin" / name
                executable.chmod(0o644)
                with self.assertRaises(runtime.RuntimeSelectionError):
                    self.activate()
                self.assert_old()
                executable.chmod(0o755)
        self.assertEqual(len(self.activate()), 1)
        self.assertEqual(os.readlink(self.current), self.new.name)

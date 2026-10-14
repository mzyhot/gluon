"""Runtime update fixtures preserve running sessions and previous installations."""
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

    def test_first_install_creates_selection_only_when_idle(self):
        self.current.unlink()
        self.assertEqual(len(self.activate()), 1)
        self.assertEqual(os.readlink(self.current), self.new.name)
        record = next((self.home / "backups").glob("runtime-*/selection.json"))
        self.assertIsNone(json.loads(record.read_text())["previous"])

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

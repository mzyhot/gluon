"""Synthetic packaged-reader fixtures; never execute Steam, Wine or a game."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from gluon import platforms


OUTPUT = b"mac=70,480,999\nwindows=70,480,999\nmac32=70\nwindows_launch_only=480\n"


class CloudSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.installation = SimpleNamespace(home=base / "Gluon", root=base / "Steam",
                                            payload=base / "Gluon/steamplay/payloads/selected")
        self.installation.payload.mkdir(parents=True)
        self.helper = self.installation.payload / "appinfo"
        self.helper.write_bytes(b"synthetic packaged reader; never executed")
        self.helper.chmod(0o755)
        self.source = self.installation.root / "appcache/appinfo.vdf"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"synthetic metadata snapshot")
        self.preference = self.installation.home / "steamplay/auto-mac32"
        self.preference.write_bytes(b"1\n")

    def candidates(self, output=OUTPUT):
        with patch.object(platforms, "_run_packaged_helper", return_value=output):
            return platforms.automatic_mac_candidates(self.installation)

    def test_saved_mac32_and_windows_launch_only_reuse_the_native_reader(self):
        self.assertEqual(self.candidates(), {"70", "480"})

    def test_disabled_or_missing_preference_keeps_windows_launch_fallback(self):
        for value in (b"0\n", b"1", b"1\nx", b"true\n", b""):
            with self.subTest(value=value):
                self.preference.write_bytes(value)
                self.assertEqual(self.candidates(), {"480"})
        self.preference.unlink()
        self.assertEqual(self.candidates(), {"480"})

    def test_symlink_preference_cannot_enable_mac32(self):
        target = self.preference.with_name("other")
        target.write_bytes(b"1\n")
        self.preference.unlink()
        self.preference.symlink_to(target)
        self.assertEqual(self.candidates(), {"480"})

    def test_non_regular_preference_cannot_block_the_installer(self):
        self.preference.unlink()
        os.mkfifo(self.preference)
        self.assertEqual(self.candidates(), {"480"})

    def test_selected_payload_and_sterile_environment_are_explicit(self):
        with patch.dict(os.environ, {"DYLD_INSERT_LIBRARIES": "/foreign.dylib",
                                     "DYLD_LIBRARY_PATH": "/foreign", "LD_PRELOAD": "/foreign.so",
                                     "STEAM_DYLD_INSERT_LIBRARIES": "/steam-overlay.dylib",
                                     "GLUON_HOME": "/other", "STEAM_ROOT": "/other"}), \
                patch.object(platforms, "_run_packaged_helper", return_value=OUTPUT) as run:
            self.assertEqual(platforms.automatic_mac_candidates(self.installation), {"70", "480"})
        helper, source, environment = run.call_args.args
        self.assertEqual(helper, self.helper)
        self.assertEqual(source, self.source)
        self.assertFalse(any(key.startswith(("DYLD_", "STEAM_DYLD_", "LD_")) for key in environment))
        self.assertEqual(environment["GLUON_HOME"], str(self.installation.home))
        self.assertEqual(environment["STEAM_ROOT"], str(self.installation.root))
        self.assertEqual(environment["PATH"], "/usr/bin:/bin:/usr/sbin:/sbin")

    def test_missing_unexecutable_or_symlink_reader_never_runs(self):
        with patch.object(platforms, "_run_packaged_helper") as run:
            self.helper.chmod(0o644)
            self.assertEqual(platforms.automatic_mac_candidates(self.installation), set())
            self.helper.unlink()
            self.assertEqual(platforms.automatic_mac_candidates(self.installation), set())
            self.helper.symlink_to(self.source)
            self.assertEqual(platforms.automatic_mac_candidates(self.installation), set())
        run.assert_not_called()

    def test_changed_metadata_helper_or_preference_does_not_publish_stale_policy(self):
        for path, value in ((self.source, b"new cache"), (self.helper, b"replacement reader"),
                            (self.preference, b"0\n")):
            with self.subTest(path=path):
                self.preference.write_bytes(b"1\n")
                def changed(*args):
                    path.write_bytes(value)
                    return OUTPUT
                with patch.object(platforms, "_run_packaged_helper", side_effect=changed):
                    self.assertEqual(platforms.automatic_mac_candidates(self.installation), set())

    def test_failure_timeout_or_non_protocol_output_stays_conservative(self):
        for error in (OSError("unavailable"), subprocess.TimeoutExpired("appinfo", 15)):
            with self.subTest(error=error), patch.object(platforms, "_run_packaged_helper", side_effect=error):
                self.assertEqual(platforms.automatic_mac_candidates(self.installation), set())
        bad = (None, b"", OUTPUT[:-1], OUTPUT + b"extra=1\n",
               OUTPUT.replace(b"mac32=70", b"mac32=0"),
               OUTPUT.replace(b"mac32=70", b"mac32=070"),
               OUTPUT.replace(b"mac32=70", b"mac32=70,70"),
               OUTPUT.replace(b"mac32=70", b"mac32=4294967296"),
               OUTPUT.replace(b"mac32=70", b"mac32=1000"),
               OUTPUT.replace(b"mac32=70", b"mac32=480"),
               OUTPUT.replace(b"windows=", b"mac="),
               OUTPUT.replace(b"mac32=70", b"mac32=70]);alert(1)//"),
               b"x" * (platforms.MAX_OUTPUT + 1))
        for output in bad:
            with self.subTest(output=repr(output)[:80]):
                self.assertEqual(self.candidates(output), set())

    def test_bounded_capture_kills_and_reaps_on_output_overflow(self):
        process = MagicMock()
        process.poll.return_value = None
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [(None, None)]
        with patch.object(platforms.subprocess, "Popen", return_value=process) as spawn, \
                patch.object(platforms.selectors, "DefaultSelector", return_value=selector), \
                patch.object(platforms.os, "read", side_effect=[b"x" * 65536] * (platforms.MAX_OUTPUT // 65536) + [b"x"]):
            self.assertIsNone(platforms._run_packaged_helper(self.helper, self.source, {}))
        process.kill.assert_called_once()
        process.wait.assert_called_once()
        process.stdout.close.assert_called_once()
        self.assertEqual(spawn.call_args.args[0], [str(self.helper), str(self.source), "--platforms"])

    def test_bounded_capture_accepts_only_a_successful_reader(self):
        for status in (0, 1):
            with self.subTest(status=status):
                process = MagicMock()
                process.poll.return_value = status
                process.wait.return_value = status
                selector = MagicMock()
                selector.__enter__.return_value = selector
                selector.select.return_value = [(None, None)]
                with patch.object(platforms.subprocess, "Popen", return_value=process), \
                        patch.object(platforms.selectors, "DefaultSelector", return_value=selector), \
                        patch.object(platforms.os, "read", side_effect=[OUTPUT[:20], OUTPUT[20:], b""]):
                    output = platforms._run_packaged_helper(self.helper, self.source, {})
                self.assertEqual(output, OUTPUT if status == 0 else None)
                process.kill.assert_not_called()
                process.stdout.close.assert_called_once()
    def test_bounded_capture_kills_and_reaps_on_timeout(self):
        process = MagicMock()
        process.poll.return_value = None
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = []
        with patch.object(platforms.subprocess, "Popen", return_value=process), \
                patch.object(platforms.selectors, "DefaultSelector", return_value=selector):
            self.assertIsNone(platforms._run_packaged_helper(self.helper, self.source, {}))
        process.kill.assert_called_once()
        process.wait.assert_called_once()
        process.stdout.close.assert_called_once()

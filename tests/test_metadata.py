import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gluon import metadata, vdf


class MetadataProtectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = self.root / "library"
        self.common = self.library / "steamapps" / "common"
        (self.common / "Example Game").mkdir(parents=True)
        self.manifest = self.library / "steamapps" / "appmanifest_123.acf"
        self.policies = self.root / "gluon" / "state" / "policies.json"
        self.observed = self.root / "gluon" / "state" / "observed.json"
        self.cache = self.root / "appinfo.vdf"
        self.cache.write_bytes(b"fixture")
        patches = [patch.object(metadata, "POLICIES", str(self.policies)),
                   patch.object(metadata, "OBSERVED", str(self.observed)),
                   patch.object(metadata.steam, "GLUON_HOME", str(self.root / "gluon")),
                   patch.object(metadata.steam, "APPINFO", str(self.cache)),
                   patch.object(metadata.steam, "is_running", return_value=False)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def write_manifest(self, mode="0", flags="4"):
        app = {"appid": "123", "StateFlags": flags, "installdir": "Example Game",
               "InstalledDepots": {"456": {"manifest": "789", "size": "100"}}}
        if mode is not None:
            app["AutoUpdateBehavior"] = mode
        text = vdf.dumps({"AppState": app})
        self.manifest.write_text(text)
        return text

    def guard(self):
        return metadata.guard_updates([123], [str(self.library)])

    def app(self):
        return vdf.load(str(self.manifest))["AppState"]

    def test_guard_preserves_depots_and_original_backup_then_restores(self):
        original = self.write_manifest()
        self.assertEqual(len(self.guard()), 1)
        self.assertEqual(self.app()["AutoUpdateBehavior"], "1")
        self.assertEqual(self.app()["InstalledDepots"], {"456": {"manifest": "789", "size": "100"}})
        record = json.loads(self.policies.read_text())[str(self.manifest)]
        self.assertEqual(Path(record["backup"]).read_text(), original)
        self.assertEqual(self.guard(), [])
        self.assertEqual(len(metadata.restore_updates([123])), 1)
        self.assertEqual(self.manifest.read_text(), original)
        self.assertEqual(json.loads(self.policies.read_text()), {})

    def test_already_update_on_launch_is_not_claimed_or_changed(self):
        original = self.write_manifest("1")
        self.assertEqual(self.guard(), [])
        self.assertFalse(self.policies.exists())
        self.assertEqual(metadata.restore_updates([123]), [])
        self.assertEqual(self.manifest.read_text(), original)

    def test_absent_preference_is_restored_as_absent(self):
        original = self.write_manifest(None)
        self.guard()
        metadata.restore_updates([123])
        self.assertEqual(self.manifest.read_text(), original)

    def test_explicit_user_change_survives_disable(self):
        self.write_manifest()
        self.guard()
        changed = self.write_manifest("2")
        self.assertEqual(metadata.restore_updates([123]), [])
        self.assertEqual(self.manifest.read_text(), changed)

    def test_reapplying_preserves_latest_user_preference_for_restore(self):
        self.write_manifest()
        self.guard()
        changed = self.write_manifest("3")
        self.guard()
        metadata.restore_updates([123])
        self.assertEqual(self.manifest.read_text(), changed)

    def test_running_steam_never_writes_manifest_or_policy(self):
        original = self.write_manifest()
        with patch.object(metadata.steam, "is_running", return_value=True):
            self.assertEqual(self.guard(), [])
            self.assertEqual(metadata.restore_updates([123]), [])
        self.assertEqual(self.manifest.read_text(), original)
        self.assertFalse(self.policies.exists())

    def test_inaccessible_library_keeps_rollback_record(self):
        self.write_manifest()
        self.guard()
        before = self.policies.read_bytes()
        with patch.object(metadata.install, "_ready_common", side_effect=FileNotFoundError):
            self.assertEqual(metadata.restore_updates([123]), [])
        self.assertEqual(self.policies.read_bytes(), before)

    def test_malformed_library_list_is_left_alone(self):
        original = self.write_manifest()
        with patch.object(metadata.steam, "library_dirs", side_effect=ValueError("invalid VDF")):
            self.assertEqual(metadata.guard_updates([123]), [])
        self.assertEqual(metadata.guard_updates([123], [None]), [])
        self.assertEqual(self.manifest.read_text(), original)

    def test_incomplete_unknown_and_unmanaged_manifests_are_left_alone(self):
        for mode, flags in [("0", "1026"), ("99", "4")]:
            original = self.write_manifest(mode, flags)
            self.assertEqual(self.guard(), [])
            self.assertEqual(self.manifest.read_text(), original)
        original = self.write_manifest()
        self.assertEqual(metadata.guard_updates([124], [str(self.library)]), [])
        self.assertEqual(self.manifest.read_text(), original)

    def test_symlink_and_duplicate_keys_are_left_alone(self):
        original = self.write_manifest()
        target = self.root / "target.acf"
        self.manifest.rename(target)
        self.manifest.symlink_to(target)
        self.assertEqual(self.guard(), [])
        self.assertEqual(target.read_text(), original)
        self.manifest.unlink()
        self.manifest.write_text(original.replace('"appid"\t\t"123"', '"appid"\t\t"123"\n\t"APPID"\t\t"123"'))
        self.assertEqual(self.guard(), [])

    def refreshed_cache(self, change=5):
        entry = SimpleNamespace(change_number=change, kv={"appinfo": {"needs_repair": True}})
        cache = Mock()
        cache.get.return_value = entry
        return entry, cache

    def test_restart_is_offered_once_per_refreshed_set(self):
        entry, cache = self.refreshed_cache()
        needed = lambda value: value.get("needs_repair")
        with patch.object(metadata.appinfo, "AppInfoFile", return_value=cache) as reader, \
                patch.object(metadata, "_game_running", return_value=False), \
                patch.object(metadata, "_ask_restart", return_value=False) as ask:
            self.assertEqual(len(metadata.watch_refresh({"123": {}}, needed, repair=Mock())), 1)
            self.assertEqual(metadata.watch_refresh({"123": {}}, needed, repair=Mock()), [])
            self.assertEqual(reader.call_count, 1, "an unchanged cache was read again")
            self.cache.write_bytes(b"another cache write")
            self.assertEqual(metadata.watch_refresh({"123": {}}, needed, repair=Mock()), [])
            self.assertEqual(ask.call_count, 1)
            entry.kv["appinfo"]["needs_repair"] = False
            self.cache.write_bytes(b"healed cache")
            metadata.watch_refresh({"123": {}}, needed, repair=Mock())
            entry.kv["appinfo"]["needs_repair"] = True
            self.cache.write_bytes(b"refreshed again")
            self.assertEqual(len(metadata.watch_refresh({"123": {}}, needed, repair=Mock())), 1)
            self.assertEqual(ask.call_count, 2)

    def test_offer_waits_until_no_game_is_running(self):
        _, cache = self.refreshed_cache()
        running = [True]
        with patch.object(metadata.appinfo, "AppInfoFile", return_value=cache), \
                patch.object(metadata, "_game_running", side_effect=lambda: running[0]), \
                patch.object(metadata, "_ask_restart", return_value=False) as ask:
            self.assertEqual(metadata.watch_refresh({"123": {}}, lambda _: True, repair=Mock()), [])
            ask.assert_not_called()
            running[0] = False
            self.assertEqual(len(metadata.watch_refresh({"123": {}}, lambda _: True, repair=Mock())), 1)
            ask.assert_called_once()

    def test_restart_quits_steam_repairs_then_reopens_it(self):
        _, cache = self.refreshed_cache()
        order = []
        repair = Mock(side_effect=lambda: order.append("repair") or ["re-applied 123"])
        quitting = iter([True, True, False])
        run = Mock(side_effect=lambda command, **_: order.append(" ".join(command[1:])))
        with patch.object(metadata.appinfo, "AppInfoFile", return_value=cache), \
                patch.object(metadata, "_game_running", return_value=False), \
                patch.object(metadata, "_ask_restart", return_value=True), \
                patch.object(metadata.steam, "is_running", side_effect=lambda: next(quitting)), \
                patch.object(metadata.time, "sleep"), patch.object(metadata.subprocess, "run", run):
            lines = metadata.watch_refresh({"123": {}}, lambda _: True, repair=repair)
        self.assertEqual(order, ["steam://exit", "repair", "-b com.valvesoftware.steam"])
        self.assertIn("re-applied 123", lines)

    def test_steam_that_does_not_quit_is_left_alone(self):
        repair = Mock()
        with patch.object(metadata.steam, "is_running", return_value=True), \
                patch.object(metadata.subprocess, "run") as run, patch.object(metadata, "_notify") as notify:
            lines = metadata._restart_steam(repair, wait=3, sleep=lambda _: None)
        repair.assert_not_called()
        notify.assert_called_once()
        self.assertEqual([call.args[0] for call in run.call_args_list], [["/usr/bin/open", "steam://exit"]])
        self.assertIn("did not quit", lines[0])

    def test_incomplete_cache_is_not_recorded_as_a_refresh(self):
        with patch.object(metadata.appinfo, "AppInfoFile", side_effect=ValueError("incomplete")), \
                patch.object(metadata, "_ask_restart") as ask:
            self.assertEqual(metadata.watch_refresh({"123": {}}, lambda _: True, repair=Mock()), [])
        self.assertFalse(self.observed.exists())
        ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()

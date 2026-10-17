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

    def test_refresh_is_reported_once_per_refreshed_set_without_prompts(self):
        entry, cache = self.refreshed_cache()
        needed = lambda value, *_: value.get("needs_repair")
        with patch.object(metadata.appinfo, "AppInfoFile", return_value=cache) as reader:
            lines = metadata.watch_refresh({"123": {}}, needed)
            self.assertEqual(len(lines), 1)
            self.assertIn("re-applies its details when Steam quits", lines[0])
            self.assertEqual(metadata.watch_refresh({"123": {}}, needed), [])
            self.assertEqual(reader.call_count, 1, "an unchanged cache was read again")
            self.cache.write_bytes(b"another cache write")
            self.assertEqual(metadata.watch_refresh({"123": {}}, needed), [])
            entry.kv["appinfo"]["needs_repair"] = False
            self.cache.write_bytes(b"healed cache")
            self.assertEqual(metadata.watch_refresh({"123": {}}, needed), [])
            entry.kv["appinfo"]["needs_repair"] = True
            self.cache.write_bytes(b"refreshed again")
            self.assertEqual(len(metadata.watch_refresh({"123": {}}, needed)), 1)

    def test_watcher_never_quits_or_prompts(self):
        for name in ("_ask_restart", "_restart_steam", "_notify", "_game_running"):
            self.assertFalse(hasattr(metadata, name), name)

    def flagged_manifest(self, flags="6", buildid="25734961", manifest="491856728672780704", beta=None):
        app = {"appid": "123", "Universe": "1", "StateFlags": flags, "installdir": "Example Game",
               "buildid": buildid, "TargetBuildID": buildid, "AutoUpdateBehavior": "1",
               "ScheduledAutoUpdate": "1792222222",
               "InstalledDepots": {"124": {"manifest": manifest, "size": "100"}},
               "UserConfig": {"language": "english"}}
        if beta:
            app["UserConfig"]["BetaKey"] = beta
        self.manifest.write_text(vdf.dumps({"AppState": app}))

    def details(self, buildid=25734961, gid="491856728672780704"):
        entry = SimpleNamespace(change_number=5, kv={"appinfo": {"depots": {
            "124": {"config": {"oslist": "windows,macos"},
                    "manifests": {"public": {"gid": gid, "size": 100}}},
            "branches": {"public": {"buildid": buildid}}}}})
        cache = Mock()
        cache.get.side_effect = lambda appid: entry if appid == 123 else None
        return cache

    def settle(self, cache):
        with patch.object(metadata.appinfo, "AppInfoFile", return_value=cache):
            return metadata.settle_update_flags([123], [str(self.library)])

    def test_update_flag_left_by_a_refresh_is_cleared(self):
        self.flagged_manifest()
        notes = self.settle(self.details())
        app = vdf.load(str(self.manifest))["AppState"]
        self.assertEqual(app["StateFlags"], "4")
        self.assertEqual(app["ScheduledAutoUpdate"], "0")
        self.assertIn("cleared the update flag", notes[0])
        backup = notes[0].split("manifest backup: ", 1)[1].rstrip(")")
        self.assertIn('"StateFlags"\t\t"6"', Path(backup).read_text())
        self.assertEqual(self.settle(self.details()), [])

    def test_real_updates_keep_their_flag(self):
        cases = [
            (dict(), self.details(buildid=25800000)),          # a new build
            (dict(), self.details(gid="111")),                 # a new depot manifest
            (dict(beta="beta"), self.details()),               # another branch
            (dict(flags="1030"), self.details()),              # an update already started
            (dict(flags="4"), self.details()),                 # nothing flagged
        ]
        for manifest, cache in cases:
            self.flagged_manifest(**manifest)
            before = self.manifest.read_text()
            self.assertEqual(self.settle(cache), [], manifest)
            self.assertEqual(self.manifest.read_text(), before, manifest)

    def test_update_flag_is_left_while_steam_runs(self):
        self.flagged_manifest()
        with patch.object(metadata.steam, "is_running", return_value=True):
            self.assertEqual(self.settle(self.details()), [])
        self.assertEqual(vdf.load(str(self.manifest))["AppState"]["StateFlags"], "6")

    def test_incomplete_cache_is_not_recorded_as_a_refresh(self):
        with patch.object(metadata.appinfo, "AppInfoFile", side_effect=ValueError("incomplete")):
            self.assertEqual(metadata.watch_refresh({"123": {}}, lambda *_: True), [])
        self.assertFalse(self.observed.exists())


if __name__ == "__main__":
    unittest.main()

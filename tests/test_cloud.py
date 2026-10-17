import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gluon import cloud, enable, vdf


def rule(root, path="Studio/Game", platforms=None, **extra):
    item = {"root": root, "path": path, "pattern": "*.sav", **extra}
    if platforms:
        item["platforms"] = {str(i + 1): name for i, name in enumerate(platforms)}
    return item


def section(rules, overrides=()):
    ufs = {"quota": 100000, "maxnumfiles": 10,
           "savefiles": {str(i): r for i, r in enumerate(rules)}}
    if overrides:
        ufs["rootoverrides"] = {str(i): o for i, o in enumerate(overrides)}
    return ufs


class CloudMappingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.home = self.root / "home"
        self.steam_root = self.home / "Library" / "Application Support" / "Steam"
        self.gluon = self.home / "Library" / "Application Support" / "Gluon"
        (self.steam_root / "steamapps").mkdir(parents=True)
        self.gluon.mkdir(parents=True)
        self.profile = str(self.steam_root / "steamapps/compatdata/123/pfx-wine/drive_c/users/crossover")
        patches = [patch.dict(os.environ, {"HOME": str(self.home), "USER": "someone"}),
                   patch.object(cloud.steam, "STEAM_ROOT", str(self.steam_root)),
                   patch.object(cloud.steam, "GLUON_HOME", str(self.gluon)),
                   patch.object(cloud.steam, "library_dirs", return_value=[str(self.steam_root)])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def mapped(self, ufs, profile=None):
        return cloud.mapped(ufs, 123, profile or self.profile)

    def mac_overrides(self, ufs):
        return [o for o in ufs.get("rootoverrides", {}).values() if o.get("os") == "MacOS"]

    def test_windows_root_is_mapped_into_the_prefix(self):
        result = self.mapped(section([rule("WinAppDataLocalLow")]))
        self.assertEqual(self.mac_overrides(result), [{
            "root": "WinAppDataLocalLow", "os": "MacOS", "oscompare": "=", "useinstead": "MacAppSupport",
            "addpath": "Steam/steamapps/compatdata/123/pfx-wine/drive_c/users/crossover/AppData/LocalLow/"}])
        self.assertTrue(cloud.is_gluon_mapping(result))
        self.assertEqual(result["savefiles"], section([rule("WinAppDataLocalLow")])["savefiles"])

    def test_every_windows_root_has_a_profile_folder(self):
        for root, folder in (("WinMyDocuments", "Documents"), ("WinAppDataLocal", "AppData/Local"),
                             ("WinAppDataRoaming", "AppData/Roaming"), ("WinSavedGames", "Saved Games")):
            override, = self.mac_overrides(self.mapped(section([rule(root)])))
            self.assertTrue(override["addpath"].endswith("/crossover/%s/" % folder), override)

    def test_a_developers_mac_override_is_replaced_and_others_are_kept(self):
        linux = {"root": "WinAppDataLocalLow", "os": "Linux", "oscompare": "=", "useinstead": "LinuxHome",
                 "addpath": ".config/unity3d/"}
        native = {"root": "WinAppDataLocalLow", "os": "MacOS", "oscompare": "=", "useinstead": "MacAppSupport",
                  "pathtransforms": {"0": {"find": "Studio/Game", "replace": "unity.Studio.Game"}}}
        result = self.mapped(section([rule("WinAppDataLocalLow")], [linux, native]))
        overrides = list(result["rootoverrides"].values())
        self.assertEqual(overrides[0], linux)
        self.assertEqual(len(overrides), 2)
        self.assertIn("/pfx-wine/drive_c/users/crossover/AppData/LocalLow/", overrides[1]["addpath"])
        self.assertNotIn("pathtransforms", overrides[1])

    def test_the_windows_location_of_an_overridden_root_is_used(self):
        # A Linux-rooted rule that the Windows client places in AppData\\Local.
        windows = {"root": "LinuxHome", "os": "Windows", "oscompare": "=", "useinstead": "WinAppDataLocal",
                   "pathtransforms": {"0": {"find": ".config/Game", "replace": "Game"}}}
        result = self.mapped(section([rule("LinuxHome", ".config/Game")], [windows]))
        override, = self.mac_overrides(result)
        self.assertEqual(override["root"], "LinuxHome")
        self.assertTrue(override["addpath"].endswith("/AppData/Local/"))
        self.assertEqual(override["pathtransforms"], windows["pathtransforms"])
        # The Windows override's own subfolder follows the profile folder.
        windows = {"root": "gameinstall", "os": "Windows", "oscompare": "=", "useinstead": "WinAppDataRoaming",
                   "addpath": "Game/Store"}
        override, = self.mac_overrides(self.mapped(section([rule("gameinstall", "")], [windows])))
        self.assertTrue(override["addpath"].endswith("/AppData/Roaming/Game/Store/"), override)

    def test_install_folder_rules_need_no_override(self):
        native = {"root": "gameinstall", "os": "MacOS", "oscompare": "=", "useinstead": "MacAppSupport"}
        result = self.mapped(section([rule("gameinstall", "Saves")], [native]))
        self.assertNotIn("rootoverrides", result)
        self.assertFalse(cloud.is_gluon_mapping(result))

    def test_rules_for_another_os_are_left_for_steam_to_skip(self):
        ufs = section([rule("WinAppDataRoaming", platforms=["Windows"]),
                       rule("LinuxHome", ".game", platforms=["Linux"])])
        result = self.mapped(ufs)
        self.assertEqual(result, ufs)
        mixed = section([rule("WinAppDataRoaming", platforms=["Windows", "MacOS"])])
        self.assertEqual(len(self.mac_overrides(self.mapped(mixed))), 1)

    def test_steam_api_only_cloud_is_kept(self):
        ufs = {"quota": 5000000, "maxnumfiles": 1}
        self.assertEqual(self.mapped(ufs), ufs)

    def test_rules_gluon_cannot_place_keep_cloud_off(self):
        cases = [
            section([rule("SteamCloudDocuments")]),
            section([rule("MacAppSupport")]),
            section([rule("WinAppDataLocal")], [{"root": "WinAppDataLocal", "os": "MacOS", "oscompare": "<>",
                                                 "useinstead": "MacHome"}]),
            section([rule("WinAppDataLocal")], [{"root": "WinAppDataLocal", "os": "Windows", "oscompare": "=",
                                                 "useinstead": "WinAppDataLocal", "replacepath": 1}]),
            section([rule("WinAppDataLocal")], [{"root": "WinAppDataLocal", "os": "Windows", "oscompare": "=",
                                                 "useinstead": "gameinstall"}]),
        ]
        for ufs in cases:
            self.assertIsNone(self.mapped(ufs), ufs)
        outside = "/Volumes/Games/steamapps/compatdata/123/pfx-wine/drive_c/users/crossover"
        self.assertIsNone(self.mapped(section([rule("WinAppDataLocal")]), outside))
        under_home = str(self.home / "Games/steamapps/compatdata/123/pfx-wine/drive_c/users/crossover")
        override, = self.mac_overrides(self.mapped(section([rule("WinAppDataLocal")]), under_home))
        self.assertEqual(override["useinstead"], "MacHome")
        self.assertTrue(override["addpath"].startswith("Games/steamapps/"))

    def test_setting_turns_cloud_off_for_one_game(self):
        (self.gluon / "games").mkdir()
        (self.gluon / "games" / "123.env").write_text("GLUON_CLOUD=0\n")
        self.assertIsNone(self.mapped({"quota": 1, "maxnumfiles": 1}))
        self.assertIsNone(self.mapped(section([rule("WinAppDataLocal")])))
        self.assertIsNotNone(cloud.mapped({"quota": 1, "maxnumfiles": 1}, 456, self.profile))
        (self.gluon / "settings.env").write_text("GLUON_CLOUD=0\n")
        (self.gluon / "games" / "123.env").write_text("GLUON_CLOUD=1\n")
        self.assertIsNotNone(self.mapped(section([rule("WinAppDataLocal")])))

    def test_mapping_is_idempotent(self):
        windows = {"root": "LinuxHome", "os": "Windows", "oscompare": "=", "useinstead": "WinAppDataLocal"}
        ufs = section([rule("WinAppDataLocalLow"), rule("LinuxHome"), rule("gameinstall", "Saves"),
                       rule("WinMyDocuments", platforms=["Windows"])], [windows])
        once = self.mapped(ufs)
        self.assertEqual(self.mapped(once), once)
        self.assertEqual(self.mapped(copy.deepcopy(once)), once)

    def test_profile_follows_the_runtime_and_prefix(self):
        runtime = self.gluon / "runtime" / "current"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "bin" / "wine").write_text("")
        (runtime / "bin" / "wine").chmod(0o755)
        (runtime / ".gluon-wine-patches").write_text("")
        self.assertEqual(cloud.profile_dir(123), self.profile)
        (runtime / ".gluon-wine-patches").unlink()
        self.assertTrue(cloud.profile_dir(123).endswith("/pfx-wine/drive_c/users/someone"))
        users = Path(self.profile).parent
        (users / "Public").mkdir(parents=True)
        (users / "player").mkdir()
        self.assertTrue(cloud.profile_dir(123).endswith("/pfx-wine/drive_c/users/player"))
        (users / "other").mkdir()
        with self.assertRaises(cloud.Unmappable):
            cloud.profile_dir(123)
        proton = self.gluon / "runtime" / "proton-tool"
        (proton / "files" / "bin").mkdir(parents=True)
        for name in ("proton", "files/bin/wine"):
            (proton / name).write_text("")
            (proton / name).chmod(0o755)
        (self.gluon / "games").mkdir()
        (self.gluon / "games" / "123.env").write_text("GLUON_RUNTIME=proton-tool\n")
        self.assertTrue(cloud.profile_dir(123).endswith("/compatdata/123/pfx/drive_c/users/steamuser"))

    def test_profile_uses_the_library_the_game_is_installed_in(self):
        other = self.home / "Games"
        (other / "steamapps").mkdir(parents=True)
        (other / "steamapps" / "appmanifest_123.acf").write_text("")
        with patch.object(cloud.steam, "library_dirs", return_value=[str(self.steam_root), str(other)]):
            self.assertTrue(cloud.profile_dir(123).startswith(str(other / "steamapps/compatdata/123/")))

    def test_backup_copies_cloud_records_and_rule_folders_within_the_limit(self):
        records = self.steam_root / "userdata" / "42" / "123"
        records.mkdir(parents=True)
        (records / "remotecache.vdf").write_text("cache")
        saves = Path(self.profile) / "AppData" / "LocalLow" / "Studio" / "Game"
        saves.mkdir(parents=True)
        (saves / "slot.sav").write_text("progress")
        install = self.steam_root / "steamapps" / "common" / "Game"
        (install / "Saves").mkdir(parents=True)
        (install / "Saves" / "big.sav").write_bytes(b"x" * 64)
        (self.steam_root / "steamapps" / "appmanifest_123.acf").write_text(
            vdf.dumps({"AppState": {"appid": "123", "installdir": "Game"}}))
        ufs = self.mapped(section([rule("WinAppDataLocalLow"), rule("gameinstall", "Saves"),
                                   rule("WinAppDataRoaming", platforms=["Windows"])]))
        with patch.object(cloud, "BACKUP_LIMIT", 32):
            notes = cloud.backup(ufs, 123, now=0)
        target = Path(notes[-1].split("cloud backup ", 1)[1])
        self.assertEqual((target / "steam-userdata/42/remotecache.vdf").read_text(), "cache")
        self.assertEqual((target / "rule-0/slot.sav").read_text(), "progress")
        self.assertFalse((target / "rule-1").exists())
        self.assertIn("over the limit", notes[0])
        self.assertIn(str(saves), (target / "SOURCES.txt").read_text())


class EnableCloudTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.profile = "/Users/x/Library/Application Support/Steam/steamapps/compatdata/123/pfx-wine/drive_c/users/crossover"
        self.steam_ufs = section([rule("WinAppDataLocalLow")])
        patches = [patch.object(enable, "STATE", str(self.root / "apps.json")),
                   patch.dict(os.environ, {"HOME": "/Users/x"}),
                   patch.object(cloud, "profile_dir", side_effect=lambda appid: self.profile),
                   patch.object(cloud, "enabled", return_value=True),
                   patch.object(enable, "_backup_cache", return_value="cache-backup"),
                   patch.object(enable, "_edit_launch_options", return_value=[]),
                   patch("gluon.metadata.guard_updates", return_value=[])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def entry(self, ufs, change=7):
        appinfo = {"common": {"name": "Game", "oslist": "windows"}}
        if ufs is not None:
            appinfo["ufs"] = copy.deepcopy(ufs)
        item = SimpleNamespace(change_number=change, kv={"appinfo": appinfo})
        item.set_kv = lambda kv: setattr(item, "kv", kv)
        return item

    def run_enable(self, entry, backup=None):
        cache = Mock()
        cache.get.side_effect = lambda appid: entry if appid == 123 else None
        backup = backup or Mock(return_value=["123: cloud backup /backups/123"])
        with patch.object(enable, "_open_cache", return_value=cache), patch.object(cloud, "backup", backup):
            notes = enable.enable([123])
        return notes, backup

    def test_cloud_is_mapped_once_with_a_backup_and_steams_section_kept(self):
        entry = self.entry(self.steam_ufs)
        notes, backup = self.run_enable(entry)
        ufs = entry.kv["appinfo"]["ufs"]
        self.assertTrue(cloud.is_gluon_mapping(ufs))
        backup.assert_called_once()
        self.assertIn("123: cloud backup /backups/123", notes)
        record = enable.load_state()["123"]
        self.assertEqual(enable._unpack(record["original"]["ufs"]), self.steam_ufs)
        self.assertIn("cloud_backup", record)
        self.assertEqual(enable.planned_changes(entry.kv["appinfo"], 123, 7), [])
        # The prefix moves: Gluon's own mapping is replaced, Steam's section stays recorded.
        self.profile = self.profile.replace("/Steam/", "/Other/")
        notes, backup = self.run_enable(entry)
        backup.assert_not_called()
        self.assertTrue(entry.kv["appinfo"]["ufs"]["rootoverrides"]["0"]["addpath"].startswith("Other/steamapps/"))
        record = enable.load_state()["123"]
        self.assertEqual(enable._unpack(record["original"]["ufs"]), self.steam_ufs)

    def test_section_left_out_by_an_earlier_gluon_is_brought_back_mapped(self):
        enable.save_state({"123": {"name": "Game", "change_number": 7,
                                   "original": {"ufs": enable._pack(self.steam_ufs)}}})
        entry = self.entry(None)
        self.run_enable(entry)
        self.assertTrue(cloud.is_gluon_mapping(entry.kv["appinfo"]["ufs"]))
        # After Steam refreshed the entry without a cloud section, none is invented.
        enable.save_state({"123": {"name": "Game", "change_number": 7,
                                   "original": {"ufs": enable._pack(self.steam_ufs)}}})
        refreshed = self.entry(None, change=8)
        self.assertEqual([c for c in enable.planned_changes(refreshed.kv["appinfo"], 123, 8)
                          if c[0] == ("ufs",)], [])

    def test_failed_backup_keeps_cloud_off(self):
        entry = self.entry(self.steam_ufs)
        notes, _ = self.run_enable(entry, backup=Mock(side_effect=OSError("disk full")))
        self.assertNotIn("ufs", entry.kv["appinfo"])
        self.assertTrue(any("Steam Cloud left off" in n for n in notes), notes)
        record = enable.load_state()["123"]
        self.assertEqual(enable._unpack(record["original"]["ufs"]), self.steam_ufs)
        self.assertNotIn("cloud_backup", record)

    def test_disable_restores_steams_section(self):
        entry = self.entry(self.steam_ufs)
        self.run_enable(entry)
        cache = Mock()
        cache.get.return_value = entry
        with patch.object(enable, "_open_cache", return_value=cache), \
                patch("gluon.metadata.restore_updates", return_value=[]):
            enable.disable([123])
        self.assertEqual(entry.kv["appinfo"]["ufs"], self.steam_ufs)


if __name__ == "__main__":
    unittest.main()

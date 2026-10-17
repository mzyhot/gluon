import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gluon import cloud, enable, metadata, vdf

FROZEN = enable.FROZEN_CHANGE_NUMBER


class RefreshFreezeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = self.root / "library"
        (self.library / "steamapps" / "common").mkdir(parents=True)
        self.gluon = self.root / "gluon"
        (self.gluon / "state").mkdir(parents=True)
        patches = [patch.object(enable, "STATE", str(self.gluon / "state" / "apps.json")),
                   patch.object(enable, "UPDATES", str(self.gluon / "state" / "game-updates.json")),
                   patch.object(enable.steam, "GLUON_HOME", str(self.gluon)),
                   patch.object(enable.steam, "library_dirs", return_value=[str(self.library)]),
                   patch.object(cloud, "mapped", side_effect=lambda ufs, appid, profile=None: copy.deepcopy(ufs)),
                   patch.object(enable, "_backup_cache", return_value="cache-backup"),
                   patch.object(enable, "_edit_launch_options", return_value=[]),
                   patch("gluon.metadata.guard_updates", return_value=[])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def install(self, appid, flags="4", folder=True):
        if folder:
            (self.library / "steamapps" / "common" / "Game").mkdir(exist_ok=True)
        (self.library / "steamapps" / ("appmanifest_%d.acf" % appid)).write_text(
            vdf.dumps({"AppState": {"appid": str(appid), "StateFlags": flags, "installdir": "Game"}}))

    def entry(self, change=39924344, oslist="windows"):
        appinfo = {"common": {"name": "Game", "oslist": oslist, "osextended": ""}}
        item = SimpleNamespace(change_number=change, kv={"appinfo": appinfo})
        item.set_kv = lambda kv: setattr(item, "kv", kv)
        return item

    def run_enable(self, entry, freeze=True):
        cache = Mock()
        cache.get.side_effect = lambda appid: entry if appid == 123 else None
        with patch.object(enable, "_open_cache", return_value=cache):
            notes = enable.enable([123], freeze=freeze)
        return notes, cache

    def test_installed_game_keeps_gluons_details(self):
        self.install(123)
        entry = self.entry()
        self.run_enable(entry)
        self.assertEqual(entry.change_number, FROZEN)
        self.assertIn("macos", entry.kv["appinfo"]["common"]["oslist"])
        self.assertEqual(enable.load_state()["123"]["change_number"], 39924344)
        notes, cache = self.run_enable(entry)
        self.assertEqual(notes, ["123 (Game): already enabled"])
        cache.save.assert_not_called()
        # An entry enabled by an earlier Gluon only needs freezing.
        entry.change_number = 39924999
        notes, cache = self.run_enable(entry)
        self.assertEqual(entry.change_number, FROZEN)
        self.assertEqual(enable.load_state()["123"]["change_number"], 39924999)
        cache.save.assert_called_once()

    def test_games_without_installed_files_refresh_normally(self):
        entry = self.entry()
        self.run_enable(entry)
        self.assertEqual(entry.change_number, 39924344)
        self.install(123, folder=False)
        self.assertEqual(enable.installed_appids([123]), set())
        self.install(123, flags="6")
        self.assertEqual(enable.installed_appids([123]), {123})
        self.run_enable(entry)
        self.assertEqual(entry.change_number, FROZEN)
        # Uninstalled again: Steam may refresh it, with its real change number.
        (self.library / "steamapps" / "appmanifest_123.acf").unlink()
        notes, _ = self.run_enable(entry)
        self.assertEqual(entry.change_number, 39924344)
        self.assertIn("released", notes[0])

    def test_update_window_releases_installed_games_for_one_session(self):
        self.install(123)
        entry = self.entry()
        self.run_enable(entry)
        self.assertEqual(enable._advance_update_window(now=1000), [])  # starts the interval
        self.assertEqual(enable._advance_update_window(now=1000 + 86400), [])
        notes = enable._advance_update_window(now=1000 + 7 * 86400)
        self.assertIn("update window opened", notes[0])
        self.assertTrue(enable.update_window_open())
        self.run_enable(entry, freeze=not enable.update_window_open())
        self.assertEqual(entry.change_number, 39924344)
        # Steam has not run yet: the window stays open across helper runs.
        self.assertEqual(enable._advance_update_window(now=1000 + 7 * 86400 + 60), [])
        enable.note_steam_running()
        self.assertEqual(enable._advance_update_window(now=1000 + 7 * 86400 + 120), ["game update window closed"])
        # Steam refreshed the game meanwhile: Gluon re-applies and freezes it.
        refreshed = self.entry(change=39930000)
        self.run_enable(refreshed, freeze=not enable.update_window_open())
        self.assertEqual(refreshed.change_number, FROZEN)
        self.assertEqual(enable.load_state()["123"]["change_number"], 39930000)

    def test_update_interval_setting(self):
        (self.gluon / "settings.env").write_text("GLUON_GAME_UPDATES=never\n")
        enable._advance_update_window(now=1000)
        self.assertEqual(enable._advance_update_window(now=1000 + 365 * 86400), [])
        (self.gluon / "settings.env").write_text("GLUON_GAME_UPDATES=daily\n")
        self.assertIn("opened", enable._advance_update_window(now=1000 + 86401)[0])

    def test_disable_lets_steam_refresh_again(self):
        self.install(123)
        entry = self.entry()
        self.run_enable(entry)
        cache = Mock()
        cache.get.return_value = entry
        with patch.object(enable, "_open_cache", return_value=cache), \
                patch("gluon.metadata.restore_updates", return_value=[]):
            enable.disable([123])
        self.assertEqual(entry.change_number, 39924344)
        self.assertEqual(entry.kv["appinfo"]["common"]["oslist"], "windows")

    def test_section_left_out_earlier_is_found_while_frozen(self):
        enable.save_state({"123": {"name": "Game", "change_number": 7,
                                   "original": {"ufs": enable._pack({"quota": 1, "maxnumfiles": 1})}}})
        self.assertEqual(enable._cloud_source({}, 123, FROZEN, None), {"quota": 1, "maxnumfiles": 1})
        self.assertIsNone(enable._cloud_source({}, 123, 8, None))

    def test_only_installed_games_reach_the_restart_offer(self):
        self.install(123)
        enable.save_state({"123": {"name": "Game"}, "456": {"name": "Other"}})
        with patch.object(enable.steam, "is_running", return_value=True), \
                patch.object(metadata, "watch_refresh", return_value=[]) as watch:
            enable.sync()
        state, _ = watch.call_args.args
        self.assertEqual(list(state), ["123"])
        self.assertFalse(watch.call_args.kwargs["updating"])


class RefreshMessageTests(unittest.TestCase):
    def test_update_window_offer_says_it_is_an_update(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "appinfo.vdf"
            cache.write_bytes(b"fixture")
            entry = SimpleNamespace(change_number=5, kv={"appinfo": {}})
            reader = Mock()
            reader.get.return_value = entry
            with patch.object(metadata, "OBSERVED", os.path.join(directory, "observed.json")), \
                    patch.object(metadata.steam, "APPINFO", str(cache)), \
                    patch.object(metadata.appinfo, "AppInfoFile", return_value=reader), \
                    patch.object(metadata, "_game_running", return_value=False), \
                    patch.object(metadata, "_ask_restart", return_value=False) as ask:
                metadata.watch_refresh({"123": {}}, lambda *_: True, repair=Mock(), updating=True)
        self.assertIn("update check", ask.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

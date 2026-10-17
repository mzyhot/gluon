import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gluon import cloud, enable


def portal2(osextended=""):
    """Portal 2's layout: a 32-bit Mac build beside Windows and Linux builds."""
    return {
        "common": {"name": "Portal 2", "type": "Game", "oslist": "windows,macos,linux",
                   "osextended": osextended},
        "depots": {
            "621": {"manifests": {"public": {"gid": "1"}}},
            "622": {"config": {"oslist": "windows"}, "manifests": {"public": {"gid": "2"}}},
            "623": {"config": {"oslist": "macos"}, "manifests": {"public": {"gid": "3"}}},
            "629": {"config": {"oslist": "windows,macos"}},
            "661": {"config": {"oslist": "linux"}},
            "branches": {"public": {"buildid": "7"}},
        },
        "config": {"launch": {
            "0": {"executable": "portal2.exe", "config": {"oslist": "windows"}},
            "2": {"executable": "portal2.sh", "config": {"oslist": "linux"}},
            "3": {"executable": "portal2.sh", "type": "none", "config": {"oslist": "macos"}},
        }},
    }


class Mac32SelectionTests(unittest.TestCase):
    def test_which_games_run_through_gluon(self):
        cases = [
            ({"oslist": "windows"}, True),
            ({}, True),
            ({"oslist": "windows,macos", "osextended": ""}, True),
            ({"oslist": "windows, macos"}, True),
            ({"oslist": "windows,macos", "osextended": "macos64"}, False),
            ({"oslist": "windows,macos", "osextended": "macosapplesilicon"}, False),
            ({"oslist": "macos,linux", "osextended": ""}, False),
        ]
        for common, expected in cases:
            self.assertEqual(enable.runs_through_gluon({"common": common}), expected, common)

    def test_mac_build_is_replaced_by_the_windows_build(self):
        ai = portal2()
        changes = enable.planned_changes(ai)
        by_path = {"/".join(path): (cur, new) for path, cur, new in changes}
        self.assertEqual(by_path["depots/623"], (ai["depots"]["623"], None))
        self.assertEqual(by_path["config/launch/3"], (ai["config"]["launch"]["3"], None))
        self.assertEqual(by_path["depots/622/config/oslist"][1], "windows,macos")
        self.assertEqual(by_path["config/launch/0/config/oslist"][1], "windows,macos")
        self.assertEqual(by_path["common/osextended"][1], "macos64")
        for untouched in ("depots/621", "depots/629/config/oslist", "depots/661", "config/launch/2"):
            self.assertFalse(any(p.startswith(untouched) for p in by_path), untouched)
        for path, _, new in changes:
            enable._set(ai, path, new)
        self.assertEqual(enable.planned_changes(ai), [])
        self.assertNotIn("623", ai["depots"])
        self.assertEqual(sorted(ai["config"]["launch"]), ["0", "2"])

    def test_native_mac_builds_are_left_alone(self):
        for extended in ("macos64", "macos64,macosapplesilicon", "macosapplesilicon"):
            changes = enable.planned_changes(portal2(extended))
            self.assertFalse(any(new is None for _, _, new in changes), extended)


class Mac32EnableTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        patches = [patch.object(enable, "STATE", str(root / "apps.json")),
                   patch.object(enable, "UPDATES", str(root / "game-updates.json")),
                   patch.object(enable, "installed_appids", return_value=set()),
                   patch.object(enable, "_backup_cache", return_value="cache-backup"),
                   patch.object(enable, "_edit_launch_options", return_value=[]),
                   patch("gluon.metadata.guard_updates", return_value=[]),
                   patch("gluon.metadata.restore_updates", return_value=[])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def cache_for(self, ai, appid=620):
        entry = SimpleNamespace(change_number=5, kv={"appinfo": ai})
        entry.set_kv = lambda kv: setattr(entry, "kv", kv)
        cache = Mock()
        cache.get.side_effect = lambda a: entry if a == appid else None
        return entry, cache

    def test_enable_and_disable_round_trip(self):
        original = portal2()
        entry, cache = self.cache_for(copy.deepcopy(original))
        with patch.object(enable, "_open_cache", return_value=cache):
            notes = enable.enable([620])
            self.assertIn("enabled", notes[0])
            self.assertNotIn("623", entry.kv["appinfo"]["depots"])
            enable.disable([620])
        self.assertEqual(entry.kv["appinfo"], original)

    def test_64bit_mac_game_is_not_enabled(self):
        entry, cache = self.cache_for(portal2("macos64"))
        with patch.object(enable, "_open_cache", return_value=cache):
            notes = enable.enable([620])
        self.assertIn("left alone", notes[0])
        self.assertNotIn("620", enable.load_state())

    def test_candidates_include_32bit_mac_games(self):
        entries = [SimpleNamespace(appid=620, kv={"appinfo": portal2()}),
                   SimpleNamespace(appid=621, kv={"appinfo": portal2("macos64")})]
        with patch.object(enable, "library_appids", return_value={620, 621}), \
                patch.object(enable.appinfo, "AppInfoFile", return_value=SimpleNamespace(entries=entries)):
            self.assertEqual(enable.candidates(), [(620, "Portal 2")])


class Mac32CloudTests(unittest.TestCase):
    def test_mac_only_rules_keep_their_locations(self):
        profile = "/Users/x/Library/Application Support/Steam/steamapps/compatdata/620/pfx-wine/drive_c/users/crossover"
        mac_rule = {"root": "MacAppSupport", "path": "Game", "pattern": "*", "platforms": {"1": "MacOS"}}
        shared = {"root": "WinMyDocuments", "path": "Game", "pattern": "*"}
        mac_override = {"root": "MacAppSupport", "os": "MacOS", "oscompare": "=", "useinstead": "MacHome",
                        "addpath": "Games/"}
        native = {"root": "WinMyDocuments", "os": "MacOS", "oscompare": "=", "useinstead": "MacAppSupport"}
        ufs = {"quota": 1, "maxnumfiles": 1, "savefiles": {"0": mac_rule, "1": shared},
               "rootoverrides": {"0": mac_override, "1": native}}
        with patch.dict(os.environ, {"HOME": "/Users/x"}), patch.object(cloud, "enabled", return_value=True):
            result = cloud.mapped(ufs, 620, profile)
            self.assertEqual(cloud.mapped(result, 620, profile), result)
        overrides = list(result["rootoverrides"].values())
        self.assertEqual(overrides[0], mac_override)
        self.assertEqual(len(overrides), 2)
        self.assertEqual(overrides[1]["root"], "WinMyDocuments")
        self.assertIn("/drive_c/users/crossover/Documents/", overrides[1]["addpath"])


if __name__ == "__main__":
    unittest.main()

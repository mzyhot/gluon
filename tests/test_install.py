import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from gluon import enable, install, metadata, steam, vdf


class MissingInstallTests(unittest.TestCase):
    appid = 4705510

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gluon-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name) / "library"
        self.common = self.library / "steamapps" / "common"
        self.common.mkdir(parents=True)
        self.manifest = self.library / "steamapps" / ("appmanifest_%d.acf" % self.appid)
        self.directory = self.common / "Happy Wheels"
        self.running = mock.patch.object(steam, "is_running", return_value=False)
        self.running.start()
        self.addCleanup(self.running.stop)
        self.write_manifest()

    def write_manifest(self, **fields):
        app = {"appid": str(self.appid), "StateFlags": "4", "installdir": "Happy Wheels",
               "SizeOnDisk": "0", "InstalledDepots": {}}
        app.update(fields)
        self.manifest.write_text(vdf.dumps({"AppState": app}), encoding="utf-8")

    def recover(self, appids=None):
        return install.recover_missing_installs(
            [self.appid] if appids is None else appids, [str(self.library)])

    def assert_untouched(self):
        before = self.manifest.read_bytes()
        self.assertEqual(self.recover(), [])
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(list(self.manifest.parent.glob("*.gluon-missing-*")), [])

    def test_missing_directory_preserves_manifest_and_saves(self):
        before = self.manifest.read_bytes()
        save = self.library / "steamapps" / "compatdata" / str(self.appid) / "save.dat"
        save.parent.mkdir(parents=True)
        save.write_bytes(b"user save")
        notes = self.recover()
        self.assertEqual(len(notes), 1)
        self.assertIn("recovered missing installation", notes[0])
        self.assertFalse(self.manifest.exists())
        parked = list(self.manifest.parent.glob(self.manifest.name + ".gluon-missing-*"))
        self.assertEqual(len(parked), 1)
        self.assertEqual(parked[0].read_bytes(), before)
        self.assertEqual(save.read_bytes(), b"user save")
        self.assertEqual(self.recover(), [])

    def test_existing_directory_even_empty_is_kept(self):
        self.directory.mkdir()
        self.assert_untouched()
        (self.directory / "game.exe").write_bytes(b"game")
        self.assert_untouched()

    def test_existing_file_is_kept(self):
        self.directory.write_bytes(b"unexpected file")
        self.assert_untouched()

    def test_dangling_install_symlink_is_kept(self):
        self.directory.symlink_to(self.common / "gone")
        self.assert_untouched()

    def test_symlink_manifest_is_kept(self):
        target = self.manifest.with_name("manifest-backup")
        self.manifest.rename(target)
        self.manifest.symlink_to(target)
        self.assert_untouched()

    def test_unmanaged_app_is_kept(self):
        before = self.manifest.read_bytes()
        self.assertEqual(self.recover([]), [])
        self.assertEqual(self.recover([1118200]), [])
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_wrong_manifest_appid_is_kept(self):
        self.write_manifest(appid="1118200")
        self.assert_untouched()

    def test_unsafe_install_paths_are_kept(self):
        for leaf in ("", ".", "..", "../Happy Wheels", "/tmp/game", "folder/game",
                     "folder\\game", "C:\\game", " game", "game\n"):
            with self.subTest(leaf=leaf):
                self.write_manifest(installdir=leaf)
                self.assert_untouched()

    def test_download_and_update_states_are_kept(self):
        for state in ("0", "1", "2", "6", "1028", "1048580", "04", "invalid"):
            with self.subTest(state=state):
                self.write_manifest(StateFlags=state)
                self.assert_untouched()

    def test_malformed_manifests_are_kept(self):
        for text in ('"AppState" {', '"AppState" { "appid" }',
                     '"AppState" { "appid" "4705510" "StateFlags" "4" '
                     '"stateflags" "4" "installdir" "Happy Wheels" }',
                     '"AppState" { "appid" "4705510" "StateFlags" "4" '
                     '"installdir" "Happy Wheels" } "dangling"'):
            with self.subTest(text=text):
                self.manifest.write_text(text, encoding="utf-8")
                self.assert_untouched()

    def test_missing_common_directory_is_kept(self):
        self.common.rmdir()
        self.assert_untouched()

    def test_inaccessible_common_directory_is_kept(self):
        with mock.patch.object(install.os, "access", return_value=False):
            self.assert_untouched()

    def test_unmounted_external_library_is_not_inspected(self):
        with mock.patch.object(install.os.path, "ismount", return_value=False), \
                mock.patch.object(install.os, "lstat") as inspect:
            self.assertEqual(install.recover_missing_installs(
                [self.appid], ["/Volumes/MissingDrive/Games"]), [])
            inspect.assert_not_called()

    def test_target_stat_permission_error_is_kept(self):
        original = os.stat

        def inspect(path, *args, **kwargs):
            if os.fspath(path) == str(self.directory):
                raise PermissionError("cannot determine whether game exists")
            return original(path, *args, **kwargs)

        with mock.patch.object(install.os, "stat", side_effect=inspect):
            self.assert_untouched()

    def test_manifest_changed_during_recovery_is_kept(self):
        original = os.lstat
        calls = 0

        def inspect(path, *args, **kwargs):
            nonlocal calls
            if os.fspath(path) == str(self.manifest):
                calls += 1
                if calls == 2:
                    self.write_manifest(StateFlags="1028")
            return original(path, *args, **kwargs)

        with mock.patch.object(install.os, "lstat", side_effect=inspect):
            self.assertEqual(self.recover(), [])
        self.assertEqual(vdf.load(str(self.manifest))["AppState"]["StateFlags"], "1028")
        self.assertEqual(list(self.manifest.parent.glob("*.gluon-missing-*")), [])

    def test_running_steam_is_kept(self):
        steam.is_running.return_value = True
        self.assert_untouched()

    def test_steam_starting_before_rename_is_kept(self):
        steam.is_running.side_effect = [False, False, True]
        self.assert_untouched()


class SyncRecoveryTests(unittest.TestCase):
    def test_recovery_follows_enable_and_uses_managed_state(self):
        order = []
        state = {"4705510": {}}
        with mock.patch.object(steam, "is_running", return_value=False), \
                mock.patch.object(enable, "candidates", return_value=[(4705510, "Happy Wheels")]), \
                mock.patch.object(enable, "load_state", return_value=state), \
                mock.patch.object(enable, "status", return_value=[]), \
                mock.patch.object(enable, "enable", side_effect=lambda ids: order.append("enable") or []), \
                mock.patch.object(install, "recover_missing_installs", side_effect=lambda ids:
                                  order.append(("recover", ids)) or ["recovered"]):
            self.assertEqual(enable.sync(), ["recovered"])
        self.assertEqual(order, ["enable", ("recover", [4705510])])

    def test_dry_run_does_not_recover(self):
        with mock.patch.object(steam, "is_running", return_value=False), \
                mock.patch.object(enable, "candidates", return_value=[]), \
                mock.patch.object(enable, "load_state", return_value={}), \
                mock.patch.object(enable, "status", return_value=[]), \
                mock.patch.object(install, "recover_missing_installs") as recover:
            enable.sync(dry_run=True)
            recover.assert_not_called()

    def test_running_steam_does_not_enable_or_recover(self):
        with mock.patch.object(steam, "is_running", return_value=True), \
                mock.patch.object(enable, "load_state", return_value={}), \
                mock.patch.object(metadata, "watch_refresh", return_value=[]) as observe, \
                mock.patch.object(enable, "enable") as reenable, \
                mock.patch.object(install, "recover_missing_installs") as recover:
            self.assertEqual(enable.sync(), [])
            observe.assert_called_once()
            reenable.assert_not_called()
            recover.assert_not_called()

    def test_only_installed_games_reach_the_restart_offer(self):
        with mock.patch.object(steam, "is_running", return_value=True), \
                mock.patch.object(enable, "load_state", return_value={"123": {}, "456": {}}), \
                mock.patch.object(enable, "installed_appids", return_value={123}), \
                mock.patch.object(metadata, "watch_refresh", return_value=[]) as observe:
            enable.sync()
        self.assertEqual(list(observe.call_args.args[0]), ["123"])


if __name__ == "__main__":
    unittest.main()

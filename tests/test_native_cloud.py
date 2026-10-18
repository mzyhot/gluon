"""Filesystem-only fixtures for native Cloud preparation; never run Wine or Steam."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gluon import cloud, steam, steamplay


class NativeCloudTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'Gluon'
        self.library = self.root / 'Steam'
        (self.library / 'steamapps').mkdir(parents=True)
        (self.library / 'steamapps/appmanifest_123.acf').write_text('fixture')
        self.compat = self.library / 'steamapps/compatdata/123'
        self.prefix = self.compat / 'pfx-wine'
        for target, name, value in ((steam, 'GLUON_HOME', str(self.home)),
                                    (steam, 'STEAM_ROOT', str(self.library)),
                                    (steam, 'APPINFO', str(self.library / 'appcache/appinfo.vdf'))):
            context = patch.object(target, name, value)
            context.start(); self.addCleanup(context.stop)
        context = patch.object(steam, 'library_dirs', return_value=[str(self.library)])
        context.start(); self.addCleanup(context.stop)
        context = patch.object(steamplay.Installation, 'idle')
        self.idle = context.start(); self.addCleanup(context.stop)
        context = patch.object(cloud, '_wine_idle')
        self.wine_idle = context.start(); self.addCleanup(context.stop)

    def prepare(self):
        return cloud.prepare('123')

    def rules(self, content):
        from test_migration import cache_bytes
        cache = Path(steam.APPINFO)
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(cache_bytes({'ufs': content}))

    def legacy(self):
        old = self.compat / 'pfx'
        (old / 'drive_c/users/steamuser').mkdir(parents=True)
        (old / 'system.reg').write_text('old Proton registry')
        (old / 'creation_sync_guard').touch()
        (old / 'drive_c/users/steamuser/old-save.dat').write_bytes(b'older independent save')
        profile = self.prefix / 'drive_c/users/crossover'
        profile.mkdir(parents=True)
        (profile / 'save.dat').write_bytes(b'active Wine save')
        (self.prefix / 'system.reg').write_text('active Wine registry')
        (self.compat / '.gluon-steam-bridge').touch()
        (self.compat / 'version').write_text('11.0-100\n')
        (self.compat / 'tracked_files').write_text('drive_c/users/steamuser/\n')
        current = self.home / 'runtime/current'
        (current / 'bin').mkdir(parents=True)
        (current / 'bin/wine').write_text('fixture Wine')
        (self.compat / 'config_info').write_text('\n'.join((
            '11.0-100', str(current / 'files/share/fonts') + '/',
            str(current / 'files/lib') + '/', 'fixture Steam client',
            '0', '0', '0', str(current / 'files/share/default_pfx') + '/', '')))
        return old, profile

    def test_api_cloud_does_not_touch_separate_prefixes(self):
        self.legacy()
        self.rules({'quota': 5000000, 'maxnumfiles': 1})
        before = steamplay._tree(self.compat)
        self.prepare()
        self.assertEqual(cloud.check_app('123', self.compat), 'native')
        self.assertEqual(steamplay._tree(self.compat), before)
        self.assertTrue(cloud.index()['apps']['123']['ready'])

    def test_game_install_cloud_does_not_require_prefix_aliases(self):
        self.legacy()
        self.rules({'savefiles': {'0': {'root': 'gameinstall', 'path': 'Contraptions', 'pattern': '*'}}})
        before = steamplay._tree(self.compat)
        self.prepare()
        self.assertFalse(cloud.needs_prefix('123'))
        self.assertEqual(steamplay._tree(self.compat), before)

    def test_windows_override_still_requires_preparation(self):
        self.rules({'savefiles': {'0': {'root': 'gameinstall', 'path': 'Save'}},
                    'rootoverrides': {'0': {'root': 'gameinstall', 'useinstead': 'WinAppDataRoaming'}}})
        self.assertTrue(cloud.needs_prefix('123'))

    def test_known_legacy_gluon_prefix_is_retired_whole_without_merging_saves(self):
        old, profile = self.legacy()
        old_tree = steamplay._tree(old)
        active_inode = (profile / 'save.dat').stat().st_ino
        retired_inode = (old / 'drive_c/users/steamuser/old-save.dat').stat().st_ino
        self.prepare()
        self.assertEqual(cloud.check_app('123', self.compat), 'prefix')
        retired = list(self.compat.glob('.gluon-retired-pfx-*'))
        self.assertEqual(len(retired), 1)
        self.assertEqual(steamplay._tree(retired[0]), old_tree)
        self.assertEqual((retired[0] / 'drive_c/users/steamuser/old-save.dat').stat().st_ino, retired_inode)
        self.assertEqual((profile / 'save.dat').stat().st_ino, active_inode)
        self.assertEqual((profile / 'save.dat').read_bytes(), b'active Wine save')
        self.assertFalse((profile / 'old-save.dat').exists())
        self.assertEqual((self.compat / 'pfx').resolve(), self.prefix)
        self.prepare()
        self.assertEqual(list(self.compat.glob('.gluon-retired-pfx-*')), retired)

    def test_foreign_runtime_provenance_is_not_migrated(self):
        old, _ = self.legacy()
        path = self.compat / 'config_info'
        path.write_text(path.read_text().replace(str(self.home), '/another/tool'))
        before = steamplay._tree(self.compat)
        with self.assertRaisesRegex(cloud.CloudError, 'separate pfx'):
            self.prepare()
        self.assertEqual(steamplay._tree(self.compat), before)

    def test_explicit_runtime_override_requires_reconciliation(self):
        self.legacy()
        (self.home / 'settings.env').write_text('GLUON_RUNTIME=another-runtime\n')
        with self.assertRaisesRegex(cloud.CloudError, 'separate pfx'):
            self.prepare()
        self.assertFalse((self.compat / cloud.MIGRATION).exists())

    def test_failed_backup_is_detected_before_retiring_old_prefix(self):
        import shutil
        self.legacy()
        copy = shutil.copytree
        def corrupt(source, target, **kwargs):
            result = copy(source, target, **kwargs)
            (target / 'save.dat').write_bytes(b'incomplete backup')
            return result
        with patch.object(cloud.shutil, 'copytree', side_effect=corrupt):
            with self.assertRaisesRegex(cloud.CloudError, 'backup verification'):
                self.prepare()
        self.assertTrue((self.compat / 'pfx').is_dir())
        self.assertFalse((self.compat / 'pfx').is_symlink())
        self.assertFalse((self.compat / cloud.MIGRATION).exists())

    def test_interrupted_prefix_retirement_recovers_on_retry(self):
        old, profile = self.legacy()
        rename = os.rename
        def interrupt(source, target):
            rename(source, target)
            raise KeyboardInterrupt('interrupted after rename')
        with patch.object(cloud.os, 'rename', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertFalse(old.exists())
        self.assertTrue((self.compat / cloud.MIGRATION).exists())
        self.prepare()
        self.assertEqual(cloud.check(self.compat), profile)
        self.assertEqual((profile / 'save.dat').read_bytes(), b'active Wine save')
        self.assertEqual(len(list(self.compat.glob('.gluon-retired-pfx-*'))), 1)

    def test_missing_readiness_record_after_alias_creation_recovers_on_retry(self):
        self.legacy()
        write = steamplay._write_json
        def interrupt(path, value):
            if path.name == cloud.MARKER:
                raise OSError('interrupted before readiness record')
            return write(path, value)
        with patch.object(steamplay, '_write_json', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertTrue((self.compat / 'pfx').is_symlink())
        self.prepare()
        self.assertEqual(cloud.check(self.compat), self.prefix / 'drive_c/users/crossover')

    def test_rule_change_requires_a_new_backup_before_readiness(self):
        self.rules({'savefiles': {'0': {'root': 'WinAppDataRoaming', 'path': 'FirstGame'}}})
        self.prepare()
        self.assertEqual(cloud.check_app('123'), 'prefix')
        before = list((self.home / 'backups/cloud').iterdir())
        self.rules({'savefiles': {'0': {'root': 'WinAppDataRoaming', 'path': 'SecondGame'}}})
        with self.assertRaisesRegex(cloud.CloudError, 'save rules changed'):
            cloud.check_app('123')
        self.assertFalse(cloud.index()['apps']['123']['ready'])
        self.prepare()
        self.assertEqual(cloud.check_app('123'), 'prefix')
        self.assertEqual(len(list((self.home / 'backups/cloud').iterdir())), len(before) + 1)

    def test_install_preflight_prepares_good_games_and_reports_conflicts(self):
        other = self.library / 'steamapps/compatdata/456/pfx'
        other.mkdir(parents=True)
        (other / 'save').write_bytes(b'foreign save')
        (self.library / 'steamapps/appmanifest_456.acf').write_text('fixture')
        notes = cloud.prepare_installed()
        self.assertEqual(cloud.check_app('123'), 'prefix')
        self.assertTrue(any('Cloud needs attention for 456' in line for line in notes))
        self.assertEqual((other / 'save').read_bytes(), b'foreign save')
        self.assertIn('separate pfx', cloud.status('456')[0])

    def test_native_root_change_to_windows_is_not_treated_as_ready(self):
        self.rules({'quota': 10})
        self.assertTrue(cloud.index()['apps']['123']['ready'])
        self.rules({'savefiles': {'0': {'root': 'WinMyDocuments', 'path': 'Game'}}})
        self.assertFalse(cloud.index()['apps']['123']['ready'])

    def test_malformed_metadata_cannot_bypass_preflight(self):
        self.rules({'savefiles': {'0': 'invalid'}})
        with self.assertRaisesRegex(cloud.CloudError, 'save root'):
            self.prepare()
        self.assertFalse(self.compat.exists())

    def test_layout_change_during_catalogue_validation_is_not_cached_as_ready(self):
        self.prepare()
        validate = cloud.check_app
        def replace_view(appid, compat=None):
            result = validate(appid, compat)
            (self.compat / 'pfx').unlink()
            (self.compat / 'pfx').mkdir()
            return result
        with patch.object(cloud, 'check_app', side_effect=replace_view):
            data = cloud.index()
        self.assertFalse(data['apps']['123']['ready'])

    def test_metadata_changed_during_index_invalidates_its_global_stamps(self):
        self.rules({'quota': 1})
        classify = cloud.needs_prefix
        changed = False
        def change(appid):
            nonlocal changed
            result = classify(appid)
            if not changed:
                changed = True
                self.rules({'savefiles': {'0': {'root': 'WinAppDataRoaming', 'path': 'Game'}}})
            return result
        with patch.object(cloud, 'needs_prefix', side_effect=change):
            data = cloud.index()
        self.assertNotEqual(data['watch'][steam.APPINFO], cloud._stamp(Path(steam.APPINFO)))

    def test_userprofile_documents_rule_backs_up_only_affected_host_files(self):
        mac = self.root / 'MacHome'
        documents = mac / 'Documents'
        game = documents / 'FixtureGame'
        game.mkdir(parents=True)
        (game / 'save').write_bytes(b'host save')
        (documents / 'unrelated').write_bytes(b'personal')
        profile = self.prefix / 'drive_c/users/crossover'
        profile.mkdir(parents=True)
        (profile / 'Documents').symlink_to(documents)
        self.rules({'savefiles': {'0': {'root': 'WinUserProfile', 'path': 'Documents/FixtureGame'}}})
        with patch.object(Path, 'home', return_value=mac):
            self.prepare()
        self.assertEqual(len(list((self.home / 'backups/cloud').glob('*/documents-*/save'))), 1)
        self.assertFalse(list((self.home / 'backups/cloud').rglob('unrelated')))
        self.assertEqual((game / 'save').read_bytes(), b'host save')

    def test_wine_mac_desktop_link_preserves_and_protects_the_game_subtree(self):
        mac = self.root / 'MacHome'
        desktop = mac / 'Desktop'
        game = desktop / 'FixtureGame'
        game.mkdir(parents=True)
        (game / 'save').write_bytes(b'desktop save')
        (desktop / 'unrelated').write_bytes(b'personal')
        profile = self.prefix / 'drive_c/users/crossover'
        (profile / 'Desktop').mkdir(parents=True)
        (profile / 'Desktop/My Mac Desktop').symlink_to(desktop)
        self.rules({'savefiles': {'0': {'root': 'WinUserProfile', 'path': 'Desktop/My Mac Desktop/FixtureGame'}}})
        with patch.object(Path, 'home', return_value=mac):
            self.prepare()
        self.assertEqual(len(list((self.home / 'backups/cloud').glob('*/documents-*/save'))), 1)
        self.assertFalse(list((self.home / 'backups/cloud').rglob('unrelated')))

    def test_fresh_prefix_shares_one_profile_without_metadata_edits(self):
        before = (self.library / 'steamapps/appmanifest_123.acf').read_bytes()
        self.prepare()
        profile = cloud.check(self.compat)
        self.assertEqual(profile, self.prefix / 'drive_c/users/steamuser')
        self.assertEqual((self.compat / 'pfx').resolve(), self.prefix)
        self.assertEqual((profile.parent / 'crossover').resolve(), profile)
        self.assertEqual((profile / 'My Documents').resolve(), profile / 'Documents')
        self.assertEqual((profile / 'Local Settings/Application Data').resolve(), profile / 'AppData/Local')
        self.assertEqual((self.library / 'steamapps/appmanifest_123.acf').read_bytes(), before)
        self.assertFalse((self.library / 'appcache').exists())

    def test_existing_wine_profile_and_saves_stay_in_place_and_are_backed_up(self):
        profile = self.prefix / 'drive_c/users/crossover'
        save = profile / 'Documents/Game/save.dat'
        save.parent.mkdir(parents=True)
        save.write_bytes(b'precious save')
        inode = save.stat().st_ino
        self.prepare()
        self.assertEqual(cloud.check(self.compat), profile)
        self.assertEqual((profile.parent / 'steamuser').resolve(), profile)
        self.assertEqual(save.read_bytes(), b'precious save')
        self.assertEqual(save.stat().st_ino, inode)
        copies = list((self.home / 'backups/cloud').glob('*/profile/Documents/Game/save.dat'))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), b'precious save')

    def test_repeat_preparation_keeps_layout_and_backup(self):
        self.prepare()
        before = list((self.home / 'backups/cloud').iterdir())
        self.assertIn('already prepared', self.prepare()[0])
        self.assertEqual(list((self.home / 'backups/cloud').iterdir()), before)

    def test_separate_proton_prefix_is_never_replaced(self):
        other = self.compat / 'pfx/save.dat'
        other.parent.mkdir(parents=True)
        other.write_bytes(b'other prefix')
        with self.assertRaisesRegex(cloud.CloudError, 'separate pfx'):
            self.prepare()
        self.assertEqual(other.read_bytes(), b'other prefix')
        self.assertFalse(self.prefix.exists())

    def test_external_documents_link_is_refused_without_touching_its_files(self):
        outside = self.root / 'Documents'
        outside.mkdir()
        (outside / 'save').write_bytes(b'host file')
        profile = self.prefix / 'drive_c/users/crossover'
        profile.mkdir(parents=True)
        (profile / 'Documents').symlink_to(outside)
        with self.assertRaisesRegex(cloud.CloudError, 'folder is a link'):
            self.prepare()
        self.assertEqual((outside / 'save').read_bytes(), b'host file')
        self.assertFalse((self.compat / cloud.MARKER).exists())

    def test_multiple_profiles_require_explicit_reconciliation(self):
        for name in ('crossover', 'steamuser'):
            (self.prefix / 'drive_c/users' / name).mkdir(parents=True)
        with self.assertRaisesRegex(cloud.CloudError, 'Multiple'):
            self.prepare()

    def test_external_profile_links_are_refused(self):
        for name in ('steamuser', 'crossover'):
            with self.subTest(name=name):
                users = self.prefix / 'drive_c/users'
                users.mkdir(parents=True, exist_ok=True)
                (users / name).symlink_to(self.root / 'outside')
                with self.assertRaises(cloud.CloudError):
                    self.prepare()
                (users / name).unlink()
        self.assertFalse((self.root / 'outside').exists())

    def test_legacy_real_save_folder_is_not_moved(self):
        old = self.prefix / 'drive_c/users/crossover/My Documents/save'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'legacy save')
        with self.assertRaisesRegex(cloud.CloudError, 'reconciliation'):
            self.prepare()
        self.assertEqual(old.read_bytes(), b'legacy save')

    def test_running_steam_or_wine_blocks_every_change(self):
        self.idle.side_effect = steamplay.SteamPlayError('quit Steam')
        with self.assertRaises(steamplay.SteamPlayError):
            self.prepare()
        self.assertFalse(self.compat.exists())

    def test_tampered_ready_layout_is_rejected(self):
        self.prepare()
        (self.compat / 'pfx').unlink()
        (self.compat / 'pfx').symlink_to(self.root)
        with self.assertRaises(cloud.CloudError):
            cloud.check(self.compat)

    def test_unprepared_game_is_rejected(self):
        with self.assertRaisesRegex(cloud.CloudError, 'automatic preparation'):
            cloud.check(self.compat)

    def test_invalid_appid_cannot_select_other_paths(self):
        for appid in ('../123', '0', '', '-1', '/123'):
            with self.subTest(appid=appid), self.assertRaises(cloud.CloudError):
                cloud.prepare(appid)

    def test_bad_ready_record_is_rejected(self):
        self.prepare()
        (self.compat / cloud.MARKER).write_text(json.dumps({'schema': 2}))
        with self.assertRaisesRegex(cloud.CloudError, 'changed'):
            cloud.check(self.compat)

    def test_insufficient_backup_space_leaves_layout_unchanged(self):
        profile = self.prefix / 'drive_c/users/crossover'
        profile.mkdir(parents=True)
        (profile / 'save').write_bytes(b'large save')
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')
        with patch.object(cloud.shutil, 'disk_usage', return_value=usage(10, 9, 1)):
            with self.assertRaisesRegex(cloud.CloudError, 'free space'):
                self.prepare()
        self.assertFalse((self.compat / cloud.MARKER).exists())
        self.assertFalse((self.compat / 'pfx').exists())

    def test_cloud_shim_with_dot_path_checks_readiness_before_launcher(self):
        root = Path(__file__).resolve().parents[1]
        shim = self.root / 'compatibilitytools.d/gluon-proton/run'
        shim.parent.mkdir(parents=True)
        shim.write_text((root / 'steamplay/run.sh').read_text().replace(
            '/usr/bin/arch -arm64 /usr/bin/python3', shlex.quote(sys.executable)))
        (self.home / 'bin').mkdir(parents=True)
        (self.home / 'lib').mkdir()
        (self.home / 'lib/gluon').symlink_to(root / 'gluon')
        trace = self.root / 'launched'
        launcher = self.home / 'bin/gluon'
        launcher.write_text('#!/bin/sh\nprintf launched > "$CLOUD_FIXTURE_TRACE"\n')
        launcher.chmod(0o755)
        env = dict(os.environ, GLUON_HOME=str(self.home), STEAM_COMPAT_DATA_PATH=str(self.compat),
                   STEAM_ROOT=str(self.library), STEAM_COMPAT_APP_ID='123', CLOUD_FIXTURE_TRACE=str(trace))
        command = ['/bin/sh', str(shim.parent) + '/./run', 'waitforexitandrun', 'fixture.exe']
        failed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('automatic preparation', failed.stderr)
        self.assertFalse(trace.exists())
        self.prepare()
        passed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertTrue(trace.exists())

    def test_automatic_preparation_runs_while_steam_is_active(self):
        self.idle.side_effect = steamplay.SteamPlayError('Steam is active')
        cloud.prepare('123', automatic=True)
        self.assertEqual(cloud.check(self.compat), self.prefix / 'drive_c/users/steamuser')
        self.idle.assert_not_called()
        self.wine_idle.assert_called()

    def test_automatic_preparation_refuses_active_wine_without_changes(self):
        self.wine_idle.side_effect = cloud.CloudError('Wine is active')
        with self.assertRaisesRegex(cloud.CloudError, 'Wine is active'):
            cloud.prepare('123', automatic=True)
        self.assertFalse(self.prefix.exists())

    def test_catalogue_detects_installation_and_layout_conflicts(self):
        data = cloud.index()
        self.assertFalse(data['apps']['123']['ready'])
        self.prepare()
        data = cloud.index()
        self.assertTrue(data['apps']['123']['ready'])
        before = data['apps']['123']['watch']
        (self.compat / 'pfx').unlink()
        (self.compat / 'pfx').mkdir()
        data = cloud.index()
        self.assertFalse(data['apps']['123']['ready'])
        self.assertNotEqual(before, data['apps']['123']['watch'])

    def test_legacy_prefix_in_another_location_is_never_silently_ignored(self):
        legacy = self.home / 'compatdata/123/pfx-wine'
        legacy.mkdir(parents=True)
        (legacy / 'save').write_bytes(b'legacy')
        with self.assertRaisesRegex(cloud.CloudError, 'ambiguous'):
            self.prepare()
        self.assertEqual((legacy / 'save').read_bytes(), b'legacy')
        self.assertFalse(self.prefix.exists())

    def test_compatibility_directory_cannot_alias_another_games_saves(self):
        other = self.root / 'other-game'
        other.mkdir()
        (other / 'save').write_bytes(b'another game')
        self.compat.parent.mkdir(parents=True)
        self.compat.symlink_to(other)
        with self.assertRaisesRegex(cloud.CloudError, 'ambiguous'):
            self.prepare()
        self.assertEqual((other / 'save').read_bytes(), b'another game')
        self.assertEqual(list(other.iterdir()), [other / 'save'])

    def test_shared_mac_documents_location_and_affected_saves_are_preserved(self):
        from test_migration import cache_bytes
        mac_home = self.root / 'MacHome'
        documents = mac_home / 'Documents'
        game = documents / 'FixtureGame'
        game.mkdir(parents=True)
        save = game / 'save.dat'
        save.write_bytes(b'existing shared save')
        (documents / 'unrelated.txt').write_bytes(b'personal')
        profile = self.prefix / 'drive_c/users/crossover'
        profile.mkdir(parents=True)
        (profile / 'Documents').symlink_to(documents)
        cache = self.library / 'appcache/appinfo.vdf'
        cache.parent.mkdir()
        content = cache_bytes({'ufs': {'savefiles': {'0': {
            'root': 'WinMyDocuments', 'path': 'FixtureGame', 'pattern': '*'}}}})
        cache.write_bytes(content)
        with patch.object(Path, 'home', return_value=mac_home), patch.object(steam, 'APPINFO', str(cache)):
            self.prepare()
            self.assertEqual(cloud.check(self.compat), profile)
            self.assertTrue((profile / 'Documents').is_symlink())
            self.assertEqual((profile / 'Documents').resolve(), documents)
        self.assertEqual(save.read_bytes(), b'existing shared save')
        copies = list((self.home / 'backups/cloud').glob('*/documents-*/save.dat'))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), b'existing shared save')
        self.assertFalse(list((self.home / 'backups/cloud').rglob('unrelated.txt')))
        self.assertEqual(cache.read_bytes(), content)

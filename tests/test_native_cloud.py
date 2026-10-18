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
                                    (steam, 'STEAM_ROOT', str(self.library))):
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
                   STEAM_COMPAT_APP_ID='123', CLOUD_FIXTURE_TRACE=str(trace))
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

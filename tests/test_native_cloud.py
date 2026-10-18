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
        self.root = Path(temporary.name)
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
        with self.assertRaisesRegex(cloud.CloudError, 'not prepared'):
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

    def test_profile_backup_limit_leaves_layout_unchanged(self):
        profile = self.prefix / 'drive_c/users/crossover'
        profile.mkdir(parents=True)
        (profile / 'save').write_bytes(b'large save')
        with patch.object(cloud, 'BACKUP_LIMIT', 1):
            with self.assertRaisesRegex(cloud.CloudError, 'backup limit'):
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
        self.assertIn('not prepared', failed.stderr)
        self.assertFalse(trace.exists())
        self.prepare()
        passed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertTrue(trace.exists())

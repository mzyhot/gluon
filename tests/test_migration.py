import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from gluon import appinfo, legacy_cloud, migration, steam, vdf


def cache_bytes(ai, change=1):
    blob = vdf.binary_dumps({'appinfo': ai})
    magic = next(k for k, v in vdf.APPINFO_MAGICS.items() if v == 28)
    head = struct.pack('<IIQ', 0, 0, 0) + b'0' * 20 + struct.pack('<I', change) + b'0' * 20
    return struct.pack('<II', magic, 1) + struct.pack('<II', 123, len(head) + len(blob)) + head + blob + b'\0' * 4


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'Gluon'
        self.root = self.base / 'Steam'
        self.cache = self.root / 'appcache/appinfo.vdf'
        self.cache.parent.mkdir(parents=True)
        (self.home / 'state').mkdir(parents=True)
        self.state = self.home / 'state/apps.json'
        for name, value in [('GLUON_HOME', str(self.home)), ('STEAM_ROOT', str(self.root)), ('APPINFO', str(self.cache))]:
            p = patch.object(steam, name, value)
            p.start(); self.addCleanup(p.stop)
        for name, value in [('is_running', False), ('library_dirs', [str(self.root)]), ('user_config_dirs', [])]:
            p = patch.object(steam, name, return_value=value)
            p.start(); self.addCleanup(p.stop)
        self.original = {'common': {'name': 'Fixture', 'oslist': 'windows'}, 'ufs': {'quota': '123'}}
        self.enabled = {'common': {'name': 'Fixture', 'oslist': 'windows,macos', 'osextended': 'macos64'}}
        self.cache.write_bytes(cache_bytes(self.enabled))
        self.state.write_text(json.dumps({'123': {'change_number': 1, 'original': {
            'common/oslist': 'windows', 'common/osextended': None,
            'ufs': {'binary': base64.b64encode(vdf.binary_dumps(self.original['ufs'])).decode()}}}}))

    def read(self):
        return appinfo.AppInfoFile(str(self.cache)).get(123).kv['appinfo']

    def test_restore_and_repeat_preserve_original_records(self):
        state = self.state.read_bytes()
        migration.migrate()
        self.assertEqual(self.read(), self.original)
        self.assertEqual(self.state.read_bytes(), state)
        self.assertTrue(list((self.home / 'backups/migration').glob('*/journal.json')))
        migration.migrate()
        self.assertEqual(self.read(), self.original)

    def test_refreshed_cache_does_not_receive_stale_values(self):
        new = {'common': {'name': 'Updated', 'oslist': 'windows,linux'}}
        self.cache.write_bytes(cache_bytes(new, change=2))
        migration.migrate()
        self.assertEqual(self.read(), new)

    def test_user_changes_block_restore_without_overwriting(self):
        self.enabled['common']['oslist'] = 'windows,macos,linux'
        before = cache_bytes(self.enabled)
        self.cache.write_bytes(before)
        with self.assertRaisesRegex(migration.MigrationError, 'changed independently'):
            migration.migrate()
        self.assertEqual(self.cache.read_bytes(), before)

    def test_running_steam_never_changes_cache(self):
        before = self.cache.read_bytes()
        with patch.object(steam, 'is_running', return_value=True):
            with self.assertRaises(migration.MigrationError):
                migration.migrate()
        self.assertEqual(self.cache.read_bytes(), before)

    def test_exact_legacy_cloud_mapping_is_restored_without_touching_saves(self):
        original = {'quota': '123', 'savefiles': {'0': {'root': 'WinAppDataLocal', 'path': 'Game'}}}
        state = json.loads(self.state.read_text())
        state['123']['original']['ufs'] = {'binary': base64.b64encode(vdf.binary_dumps(original)).decode()}
        self.state.write_text(json.dumps(state))
        with patch.object(legacy_cloud, '_mac_root', return_value=('MacHome', 'fixture/profile')):
            self.enabled['ufs'] = legacy_cloud.mapped(original, 123)
            self.cache.write_bytes(cache_bytes(self.enabled))
            migration.migrate()
        self.assertEqual(self.read()['ufs'], original)
        self.assertEqual(json.loads(self.state.read_text()), state)

    def test_independently_modified_legacy_cloud_mapping_still_blocks_restore(self):
        original = {'quota': '123', 'savefiles': {'0': {'root': 'WinAppDataLocal', 'path': 'Game'}}}
        state = json.loads(self.state.read_text())
        state['123']['original']['ufs'] = {'binary': base64.b64encode(vdf.binary_dumps(original)).decode()}
        self.state.write_text(json.dumps(state))
        with patch.object(legacy_cloud, '_mac_root', return_value=('MacHome', 'fixture/profile')):
            self.enabled['ufs'] = legacy_cloud.mapped(original, 123)
            self.enabled['ufs']['quota'] = '999'
            before = cache_bytes(self.enabled)
            self.cache.write_bytes(before)
            with self.assertRaises(migration.MigrationError):
                migration.migrate()
        self.assertEqual(self.cache.read_bytes(), before)

    def test_ours_launch_options_removed_user_options_preserved(self):
        config = self.root / 'userdata/1/config'
        config.mkdir(parents=True)
        path = config / 'localconfig.vdf'
        apps = {'123': {'LaunchOptions': '"%s" run %%command%%' % (self.home / 'bin/gluon')},
                '456': {'LaunchOptions': 'user options'}}
        path.write_text(vdf.dumps({'UserLocalConfigStore': {'Software': {'Valve': {'Steam': {'apps': apps}}}}}))
        with patch.object(steam, 'user_config_dirs', return_value=[str(config)]):
            migration.migrate()
        result = vdf.iget(vdf.load(str(path)), 'UserLocalConfigStore', 'Software', 'Valve', 'Steam', 'apps')
        self.assertEqual(result['123'], {})
        self.assertEqual(result['456']['LaunchOptions'], 'user options')

    def test_update_policy_restored_only_when_ours(self):
        (self.root / 'steamapps/common').mkdir(parents=True)
        path = self.root / 'steamapps/appmanifest_123.acf'
        path.write_text(vdf.dumps({'AppState': {'appid': '123', 'AutoUpdateBehavior': '1'}}))
        policies = self.home / 'state/update-policies.json'
        policies.write_text(json.dumps({str(path): {'appid': 123, 'key': 'AutoUpdateBehavior', 'original': '0'}}))
        migration.migrate()
        self.assertEqual(vdf.load(str(path))['AppState']['AutoUpdateBehavior'], '0')
        path.write_text(vdf.dumps({'AppState': {'appid': '123', 'AutoUpdateBehavior': '3'}}))
        migration.migrate()
        self.assertEqual(vdf.load(str(path))['AppState']['AutoUpdateBehavior'], '3')

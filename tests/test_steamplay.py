import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest
from unittest.mock import patch

from gluon import steamplay, steam, migration, vdf


class FixtureSystem:
    def __init__(self):
        self.fail_sign = False
        self.fail_register = False
        self.copies = 0

    def copy(self, source, destination):
        self.copies += 1
        shutil.copytree(source, destination, symlinks=True)

    def sign(self, app):
        if self.fail_sign:
            raise steamplay.SteamPlayError('fixture signing failure')
        (app / 'Contents/signature-fixture').write_text('signed')

    def verify(self, app):
        pass

    def valve(self, app):
        if (app / 'Contents/signature-fixture').exists():
            raise steamplay.SteamPlayError('fixture is no longer Valve signed')

    def register(self, app):
        if self.fail_register:
            self.fail_register = False
            raise steamplay.SteamPlayError('fixture registration failure')


class SteamPlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.home = base / 'Gluon'
        self.root = base / 'Steam'
        self.app = base / 'custom folder/Steam.app'
        (self.app / 'Contents/MacOS').mkdir(parents=True)
        self.plist = self.app / 'Contents/Info.plist'
        self.plist.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.valvesoftware.steam',
            'CFBundleExecutable': 'steam_osx', 'LSEnvironment': {'USER_SETTING': 'kept'}}))
        (self.app / 'Contents/MacOS/steam_osx').write_bytes(b'fixture executable')
        (self.root / 'package').mkdir(parents=True)
        (self.root / 'package/steam_client_macos.installed').write_text('"Steam"\n{\n"version" "123"\n}\n')
        self.system = FixtureSystem()
        self.installation = steamplay.Installation(self.home, self.root, self.system)
        self.payload = self.installation.payload
        self.payload.mkdir(parents=True)
        for name in (steamplay.DYLIB, 'appinfo', 'tools', 'builds', 'run', 'valve-packages.manifest'):
            (self.payload / name).write_text('fixture ' + name)
        self.inventory()
        for target, name, value in ((steam, 'GLUON_HOME', str(self.home)),
                                    (steam, 'STEAM_ROOT', str(self.root)),
                                    (steam, 'APPINFO', str(self.root / 'appcache/appinfo.vdf')),
                                    (steam, 'is_running', False)):
            p = patch.object(target, name, return_value=value) if name == 'is_running' else patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(self.installation, 'idle')
        p.start()
        self.addCleanup(p.stop)
        self.original = steamplay._tree(self.app)

    def inventory(self):
        data = {'schema': 1, 'files': {p.name: steamplay._digest(p) for p in self.payload.iterdir() if p.name != 'payload.json'},
                'builds': {'123': {'profile': 'fixture'}}, 'notproton': 'fixture'}
        (self.payload / 'payload.json').write_text(json.dumps(data))

    def install(self):
        return self.installation.install(str(self.app))

    def test_install_reinstall_uninstall_restores_exact_bundle(self):
        self.install()
        env = plistlib.loads(self.plist.read_bytes())['LSEnvironment']
        self.assertEqual(env['USER_SETTING'], 'kept')
        self.assertEqual(env[steamplay.INSERT], str(self.app / 'Contents/MacOS' / steamplay.DYLIB))
        self.assertEqual(env['GLUON_STEAMPLAY_PAYLOAD'], str(self.payload))
        copies = self.system.copies
        self.install()
        self.assertEqual(copies, self.system.copies)
        self.installation.uninstall()
        self.assertEqual(steamplay._tree(self.app), self.original)
        self.assertEqual(self.installation.receipt()['apps'], {})
        self.assertTrue(list((self.home / 'backups/steamplay').glob('*/Steam.app')))

    def test_update_preserves_original_backup(self):
        self.install()
        receipt = self.installation.receipt()['apps'][str(self.app)]
        (self.payload / steamplay.DYLIB).write_text('new payload')
        self.inventory()
        self.install()
        self.assertEqual(self.installation.receipt()['apps'][str(self.app)]['backup'], receipt['backup'])
        self.installation.uninstall()
        self.assertEqual(steamplay._tree(self.app), self.original)

    def test_missing_steam(self):
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'missing'):
            self.installation.install(str(self.app.parent / 'absent.app'))

    def test_running_steam(self):
        self.installation.idle.side_effect = steamplay.SteamPlayError('quit Steam')
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'quit Steam'):
            self.install()
        self.assertEqual(steamplay._tree(self.app), self.original)

    def test_permission_failure(self):
        with patch('gluon.steamplay.os.access', return_value=False):
            with self.assertRaisesRegex(steamplay.SteamPlayError, 'own'):
                self.install()
        self.assertEqual(steamplay._tree(self.app), self.original)

    def test_foreign_injection_is_preserved(self):
        data = plistlib.loads(self.plist.read_bytes())
        data['LSEnvironment'][steamplay.INSERT] = '/foreign/library.dylib'
        self.plist.write_bytes(plistlib.dumps(data))
        before = steamplay._tree(self.app)
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'foreign'):
            self.install()
        self.assertEqual(steamplay._tree(self.app), before)

    def test_unsupported_build_is_preserved(self):
        (self.root / 'package/steam_client_macos.installed').write_text('"Steam" { "version" "999" }')
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'unsupported Steam build'):
            self.install()
        self.assertEqual(steamplay._tree(self.app), self.original)

    def test_invalid_payload_is_preserved(self):
        (self.payload / steamplay.DYLIB).write_text('tampered')
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'verification'):
            self.install()
        self.assertEqual(steamplay._tree(self.app), self.original)

    def test_signing_failure_does_not_touch_original(self):
        self.system.fail_sign = True
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'signing'):
            self.install()
        self.assertEqual(steamplay._tree(self.app), self.original)

    def test_registration_failure_rolls_back(self):
        self.system.fail_register = True
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'registration'):
            self.install()
        self.assertEqual(steamplay._tree(self.app), self.original)
        self.assertFalse(self.installation.receipt()['apps'])
        self.install()  # recovers the stale transaction then installs

    def test_interrupted_first_rename_is_recovered(self):
        old = self.app.parent / '.gluon-old-fixture.app'
        stage = self.app.parent / '.gluon-stage-fixture.app'
        shutil.copytree(self.app, stage)
        os.rename(self.app, old)
        data = self.installation.receipt()
        data['transaction'] = {'app': str(self.app), 'old': str(old), 'stage': str(stage), 'new_tree': steamplay._tree(stage)}
        steamplay._write_json(self.installation.state, data)
        self.install()
        self.installation.uninstall()
        self.assertEqual(steamplay._tree(self.app), self.original)

    def test_foreign_change_blocks_repair_and_uninstall(self):
        self.install()
        (self.app / 'Contents/MacOS/steam_osx').write_text('changed independently')
        before = steamplay._tree(self.app)
        for operation in (self.install, self.installation.uninstall):
            with self.assertRaises(steamplay.SteamPlayError):
                operation()
            self.assertEqual(steamplay._tree(self.app), before)

    def test_clean_steam_update_can_be_reinstalled(self):
        self.install()
        record = self.installation.receipt()['apps'][str(self.app)]
        shutil.rmtree(self.app)
        shutil.copytree(record['backup'], self.app)
        (self.app / 'Contents/MacOS/steam_osx').write_text('new Valve build fixture')
        updated = steamplay._tree(self.app)
        self.install()
        self.installation.uninstall()
        self.assertEqual(steamplay._tree(self.app), updated)

    def test_corrupt_backup_blocks_uninstall(self):
        self.install()
        record = self.installation.receipt()['apps'][str(self.app)]
        (Path(record['backup']) / 'Contents/MacOS/steam_osx').write_text('corrupt backup')
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'backup'):
            self.installation.uninstall()

    def test_missing_owned_dylib_can_be_repaired(self):
        self.install()
        (self.app / 'Contents/MacOS' / steamplay.DYLIB).unlink()
        self.install()
        self.assertTrue((self.app / 'Contents/MacOS' / steamplay.DYLIB).is_file())
        self.installation.uninstall()
        self.assertEqual(steamplay._tree(self.app), self.original)

    def test_foreign_tool_is_preserved(self):
        tool = self.root / 'compatibilitytools.d/gluon'
        tool.mkdir(parents=True)
        (tool / 'run').write_text('foreign tool')
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'foreign compatibility tool'):
            self.install()
        self.assertEqual((tool / 'run').read_text(), 'foreign tool')

    def test_no_metadata_writes_for_native_install(self):
        appcache = self.root / 'appcache'
        appcache.mkdir()
        path = appcache / 'appinfo.vdf'
        path.write_bytes(b'opaque native metadata')
        with patch.object(migration, 'migrate', return_value=[]):
            self.install()
        self.assertEqual(path.read_bytes(), b'opaque native metadata')

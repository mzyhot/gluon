import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from gluon import steamplay, valve_files


class ValveHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.payload = self.home / 'payload'
        self.payload.mkdir()
        stream = io.BytesIO()
        self.members = {'Steam.dll': b'fixture dll', 'iscriptevaluator.exe': b'fixture evaluator', 'SteamService.exe': b'fixture service'}
        with zipfile.ZipFile(stream, 'w') as archive:
            for name, data in self.members.items():
                archive.writestr('legacycompat/' + name, data)
        self.archive = stream.getvalue()
        manifest = ['base https://client-update.akamai.steamstatic.com',
                    'package fixture fixture.zip ' + hashlib.sha256(self.archive).hexdigest()]
        for name, data in self.members.items():
            manifest.append('file legacycompat/%s fixture legacycompat/%s %s' % (name, name, hashlib.sha256(data).hexdigest()))
        self.manifest = self.payload / 'valve-packages.manifest'
        self.manifest.write_text('\n'.join(manifest))
        (self.payload / 'payload.json').write_text(json.dumps({'files': {'valve-packages.manifest': steamplay._digest(self.manifest)}}))
        p = patch.object(valve_files, 'Installation', return_value=SimpleNamespace(home=self.home, payload=self.payload))
        p.start(); self.addCleanup(p.stop)
        p = patch.dict('os.environ', {}, clear=True)
        p.start(); self.addCleanup(p.stop)

    def test_verified_members_installed_without_steam_modification(self):
        with patch.object(valve_files.urllib.request, 'urlopen', side_effect=lambda *a, **k: io.BytesIO(self.archive)) as download:
            valve_files.ensure()
            self.assertEqual(download.call_count, 1)
            valve_files.ensure()
            self.assertEqual(download.call_count, 1)
        for name, content in self.members.items():
            self.assertEqual((self.home / 'steamplay/legacycompat' / name).read_bytes(), content)

    def test_bad_archive_refused_before_installing(self):
        with patch.object(valve_files.urllib.request, 'urlopen', side_effect=lambda *a, **k: io.BytesIO(b'tampered')):
            with self.assertRaisesRegex(steamplay.SteamPlayError, 'download failed verification'):
                valve_files.ensure()
        self.assertFalse((self.home / 'steamplay/legacycompat').exists())

    def test_tampered_manifest_refused_without_download(self):
        self.manifest.write_text('tampered')
        with patch.object(valve_files.urllib.request, 'urlopen') as download:
            with self.assertRaisesRegex(steamplay.SteamPlayError, 'manifest failed'):
                valve_files.ensure()
            download.assert_not_called()

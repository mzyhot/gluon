import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'Gluon space'
        runtime = self.home / 'runtime/current'
        (runtime / 'bin').mkdir(parents=True)
        (self.home / 'bin').mkdir()
        self.trace = self.base / 'trace.jsonl'
        wine = runtime / 'bin/wine'
        wine.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['WINEPREFIX'])
p.mkdir(parents=True, exist_ok=True)
(p / 'system.reg').touch()
with open(os.environ['GLUON_TEST_TRACE'], 'a') as f:
    f.write(json.dumps({'args': sys.argv[1:], 'prefix': str(p), 'cwd': os.getcwd(),
        'appid': os.environ.get('SteamAppId'), 'compat_id': os.environ.get('STEAM_COMPAT_APP_ID'),
        'insert': os.environ.get('DYLD_INSERT_LIBRARIES')}) + '\\n')
''')
        wine.chmod(0o755)
        server = runtime / 'bin/wineserver'
        server.write_text('#!/bin/sh\nexit 0\n')
        server.chmod(0o755)
        (self.home / 'bin/gluon').symlink_to(ROOT / 'tool/gluon')
        self.game = self.base / "library's space/steamapps/common/Example Game/Game.exe"
        self.game.parent.mkdir(parents=True)
        self.game.touch()
        self.env = dict(os.environ, GLUON_HOME=str(self.home), STEAM_COMPAT_APP_ID='123',
                        GLUON_TEST_TRACE=str(self.trace), GLUON_NO_STEAM_BRIDGE='1')
        for name in ('GLUON_DRY_RUN', 'GLUON_RUNTIME', 'WINEPREFIX', 'GLUON_EXTRA_ARGS'):
            self.env.pop(name, None)

    def run_tool(self, *args, **environment):
        return subprocess.run(['/bin/sh', str(ROOT / 'steamplay/run.sh'), *args],
                              env=dict(self.env, **environment), capture_output=True, text=True, timeout=15)

    def test_waitforexitandrun_preserves_argv_appid_prefix_and_cwd(self):
        result = self.run_tool('waitforexitandrun', str(self.game), 'argument space', 'quote"', 'back\\slash')
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in self.trace.read_text().splitlines()]
        game = rows[-1]
        self.assertEqual(game['args'], [str(self.game), 'argument space', 'quote"', 'back\\slash'])
        self.assertEqual(game['appid'], '123')
        self.assertEqual(game['compat_id'], '123')
        self.assertEqual(game['cwd'], str(self.game.parent))
        self.assertTrue(game['prefix'].endswith('/steamapps/compatdata/123/pfx-wine'))
        self.assertIsNone(game['insert'])

    def test_runinprefix_reuses_native_compat_data(self):
        compat = self.base / 'existing prefix'
        (compat / 'pfx-wine').mkdir(parents=True)
        (compat / 'pfx-wine/system.reg').touch()
        (compat / 'pfx-wine/save.dat').write_text('save')
        result = self.run_tool('runinprefix', str(self.game), STEAM_COMPAT_DATA_PATH=str(compat))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.trace.read_text().splitlines()[-1])['prefix'], str(compat / 'pfx-wine'))
        self.assertEqual((compat / 'pfx-wine/save.dat').read_text(), 'save')
        self.assertIsNone(json.loads(self.trace.read_text().splitlines()[-1])['appid'])

    def test_getcompatpath_never_starts_wine(self):
        result = self.run_tool('getcompatpath', STEAM_COMPAT_DATA_PATH='/fixture compat')
        self.assertEqual(result.stdout, '/fixture compat\n')
        self.assertFalse(self.trace.exists())

    def test_unknown_verb_fails(self):
        self.assertNotEqual(self.run_tool('unknown', str(self.game)).returncode, 0)
        self.assertFalse(self.trace.exists())

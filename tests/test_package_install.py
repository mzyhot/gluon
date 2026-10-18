import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gluon import package_install, steam, steamplay


class PackageDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'Gluon'
        self.source = self.base / 'staged'
        for relative in ('bin', 'lib/gluon', 'runtime/fixture/bin', 'runtime/fixture/licenses',
                         'steamplay/payloads/fixture'):
            (self.source / relative).mkdir(parents=True)
        (self.source / 'bin/gluon-agent').write_text('fixture agent')
        (self.source / 'lib/gluon/steamplay.py').write_text('fixture module')
        (self.source / 'runtime/fixture/bin/wine').write_text('fixture runtime')
        (self.source / 'runtime/pending.json').write_text(json.dumps({'schema': 1, 'name': 'fixture', 'version': '0.4.0'}))
        (self.source / 'steamplay/pending.json').write_text(json.dumps({'name': 'fixture'}))
        for target, name, value in ((steam, 'GLUON_HOME', str(self.home)),
                                    (steamplay.Installation, 'idle', lambda self: None),
                                    (package_install.os, 'geteuid', lambda: 1000)):
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(steamplay.System, 'copy', side_effect=lambda src, dst: shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True))
        p.start(); self.addCleanup(p.stop)
        p = patch.object(package_install.Path, 'home', return_value=self.base)
        p.start(); self.addCleanup(p.stop)
        p = patch.object(package_install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'fixture setup\n', ''))
        self.run = p.start(); self.addCleanup(p.stop)

    def test_stages_versions_and_registers_only_runtime_agent(self):
        package_install.deploy(self.source)
        self.assertTrue((self.home / 'runtime/fixture/bin/wine').is_file())
        self.assertTrue((self.home / 'steamplay/payloads/fixture').is_dir())
        self.assertTrue((self.home / 'logs/install.log').is_file())
        self.assertTrue((self.base / 'Library/LaunchAgents/com.gluon.agent.plist').is_file())
        self.assertTrue(any('install' in call.args[0] for call in self.run.call_args_list))

    def test_staged_versions_are_not_overwritten(self):
        package_install.deploy(self.source)
        (self.home / 'runtime/fixture/bin/wine').write_text('changed existing runtime')
        with self.assertRaisesRegex(steamplay.SteamPlayError, 'existing version differs'):
            package_install.deploy(self.source)
        self.assertEqual((self.home / 'runtime/fixture/bin/wine').read_text(), 'changed existing runtime')

    def test_root_is_refused_before_user_paths_are_created(self):
        with patch.object(package_install.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(steamplay.SteamPlayError, 'must not run as root'):
                package_install.deploy(self.source)
        self.assertFalse(self.home.exists())

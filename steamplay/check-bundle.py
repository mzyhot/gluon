"""Actions-only fixture: compile a fake Steam bundle and test real codesign recovery."""
import os
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gluon import steamplay, migration

assert os.environ.get('GITHUB_ACTIONS') == 'true', 'fixture compilation requires Actions'
repository = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary).resolve()
    app = base / 'Steam.app'
    binary = app / 'Contents/MacOS/steam_osx'
    binary.parent.mkdir(parents=True)
    source = base / 'fixture.c'
    source.write_text('int main(void) { return 0; }\n')
    subprocess.run(['clang', '-arch', 'arm64', str(source), '-o', str(binary)], check=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.valvesoftware.steam',
        'CFBundleExecutable': 'steam_osx', 'CFBundleName': 'Steam fixture', 'CFBundlePackageType': 'APPL'}))
    subprocess.run(['codesign', '-f', '-s', '-', str(app)], check=True)
    with patch.object(steamplay, 'VALVE_REQUIREMENT', 'identifier "org.gluon.wrong-fixture"'):
        try:
            steamplay.System().valve(app)
        except steamplay.SteamPlayError:
            pass
        else:
            raise AssertionError('signature requirement accepted the wrong identity')
    original = steamplay._tree(app)
    class FixtureIdentity(steamplay.System):
        def valve(self, candidate):
            # Fixtures cannot have Valve's Developer ID. Exercise production
            # requirement parsing with this fixture's actual signed identifier.
            with patch.object(steamplay, 'VALVE_REQUIREMENT', 'identifier "com.valvesoftware.steam"'):
                super().valve(candidate)
    install = steamplay.Installation(base / 'Gluon', base / 'Steam', FixtureIdentity())
    shutil.copytree(repository / 'build/steamplay-payload', install.payload)
    build = next(iter(steamplay._json(install.payload / 'payload.json')['builds']))
    package = install.root / 'package'
    package.mkdir(parents=True)
    (package / 'steam_client_macos.installed').write_text('"Steam" { "version" "' + build + '" }')
    with patch.object(install, 'idle'), patch.object(migration, 'migrate', return_value=[]):
        install.install(str(app))
        install.system.verify(app)
        record = install.receipt()['apps'][str(app)]
        backup = Path(record['backup'])
        other = base / 'Other Steam.app'
        shutil.copytree(backup, other)
        # Exercise a real signed upgrade through automatic selection, with the
        # same extra Steam copies Spotlight can return on an installed system.
        tools = install.payload / 'tools'
        tools.write_bytes(tools.read_bytes() + b'\n')
        manifest_path = install.payload / 'payload.json'
        manifest = steamplay._json(manifest_path)
        manifest['files']['tools'] = steamplay._digest(tools)
        manifest_path.write_text(json.dumps(manifest))
        with patch.object(steamplay, '_app_candidates', return_value=[backup, other]) as discovery:
            install.install()
        discovery.assert_not_called()
        install.system.verify(app)
        assert install.receipt()['apps'][str(app)]['backup'] == str(backup)
        assert steamplay._tree(backup) == original, 'upgrade changed the restoration backup'
        assert steamplay._tree(other) == original, 'upgrade changed another Steam installation'
        install.uninstall()
        install.system.verify(app)
    assert steamplay._tree(app) == original, 'original signed fixture did not restore exactly'
print('Native signing, installation, automatic upgrade and exact restoration fixture passed')

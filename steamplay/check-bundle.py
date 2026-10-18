"""Actions-only fixture: compile a fake Steam bundle and test real codesign recovery."""
import os
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
    original = steamplay._tree(app)
    class FixtureIdentity(steamplay.System):
        def valve(self, candidate):
            # Fixtures cannot have Valve's Developer ID. All signature verification,
            # staging and signing still use the real production implementation.
            self.verify(candidate)
    install = steamplay.Installation(base / 'Gluon', base / 'Steam', FixtureIdentity())
    shutil.copytree(repository / 'build/steamplay-payload', install.payload)
    build = next(iter(steamplay._json(install.payload / 'payload.json')['builds']))
    package = install.root / 'package'
    package.mkdir(parents=True)
    (package / 'steam_client_macos.installed').write_text('"Steam" { "version" "' + build + '" }')
    with patch.object(install, 'idle'), patch.object(migration, 'migrate', return_value=[]):
        install.install(str(app))
        install.system.verify(app)
        install.uninstall()
        install.system.verify(app)
    assert steamplay._tree(app) == original, 'original signed fixture did not restore exactly'
print('Native signing, installation and exact restoration fixture passed')

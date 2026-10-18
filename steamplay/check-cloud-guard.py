#!/usr/bin/env python3
"""Run the production native Cloud guard against a disposable Actions library."""
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
    raise SystemExit('Native fixtures run only on GitHub macOS runners')
root = Path(__file__).resolve().parents[1]
source = root / 'build/notproton'
executable = root / 'build/evidence/check-cloud-guard'
subprocess.run(['clang', '-arch', 'arm64', '-std=c17', '-Wall', '-Wextra', '-Werror',
                '-Wno-error=deprecated-declarations',  # pinned upstream cJSON uses sprintf
                '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                '-I' + str(root / 'steamplay'), str(root / 'steamplay/check-cloud-guard.c'),
                str(source / 'dylib/feats/cloud_guard.c'), str(source / 'dylib/util/file.c'),
                str(source / 'dylib/util/log.c'), str(source / 'vendor/cJSON.c'),
                '-o', str(executable)], check=True)
with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary).resolve()
    home, steam = base / 'Gluon', base / 'Steam'
    (home / 'steamplay').mkdir(parents=True)
    (home / 'lib').mkdir()
    (home / 'lib/gluon').symlink_to(root / 'gluon')
    (steam / 'steamapps').mkdir(parents=True)
    (steam / 'steamapps/appmanifest_123.acf').write_text('fixture')
    # API and GameInstall games must keep their original separate pfx untouched.
    sys.path.insert(0, str(root / 'tests'))
    from test_migration import cache_bytes
    entries = []
    for appid, rules in ((789, {'quota': 100}),
                         (790, {'savefiles': {'0': {'root': 'gameinstall', 'path': 'Saves'}}}),
                         (791, {'savefiles': {'0': {'root': 'WinAppDataRoaming', 'path': 'FixtureGame'}}})):
        data = bytearray(cache_bytes({'ufs': rules}))
        struct.pack_into('<I', data, 8, appid)
        entries.append(data[8:-4])
        (steam / ('steamapps/appmanifest_%d.acf' % appid)).write_text('fixture')
        legacy = steam / ('steamapps/compatdata/%d/pfx' % appid)
        legacy.mkdir(parents=True)
        (legacy / 'old-save').write_bytes(b'original prefix retained')
    (steam / 'appcache').mkdir()
    (steam / 'appcache/appinfo.vdf').write_bytes(data[:8] + b''.join(entries) + b'\0' * 4)
    compat = steam / 'steamapps/compatdata/791'
    (compat / 'pfx/drive_c/users/steamuser').mkdir(parents=True)
    (compat / 'pfx/system.reg').write_text('old registry')
    (compat / 'pfx/creation_sync_guard').touch()
    (compat / 'pfx-wine/drive_c/users/crossover').mkdir(parents=True)
    (compat / 'pfx-wine/system.reg').write_text('active registry')
    (compat / 'pfx-wine/drive_c/users/crossover/save').write_bytes(b'active save')
    (compat / '.gluon-steam-bridge').touch()
    (compat / 'tracked_files').write_text('drive_c/users/steamuser/\n')
    (compat / 'version').write_text('legacy-gluon\n')
    current = home / 'runtime/current'
    (current / 'bin').mkdir(parents=True)
    (current / 'bin/wine').write_text('fixture Wine')
    (compat / 'config_info').write_text('\n'.join(('legacy-gluon',
        str(current / 'files/share/fonts') + '/', str(current / 'files/lib') + '/',
        'fixture client', '0', '0', '0', str(current / 'files/share/default_pfx') + '/', '')))
    env = dict(os.environ, GLUON_HOME=str(home), GLUON_STEAM_ROOT=str(steam))
    for name in ('DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'):
        env.pop(name, None)
    subprocess.run([str(executable)], env=env, check=True, timeout=90)
    for appid in (789, 790):
        legacy = steam / ('steamapps/compatdata/%d/pfx' % appid)
        assert not legacy.is_symlink()
        assert (legacy / 'old-save').read_bytes() == b'original prefix retained'
    retired = list(compat.glob('.gluon-retired-pfx-*'))
    assert len(retired) == 1
    assert (retired[0] / 'old-save').read_bytes() == b'original prefix retained'
    assert (compat / 'pfx-wine/drive_c/users/crossover/save').read_bytes() == b'active save'

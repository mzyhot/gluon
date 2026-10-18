#!/usr/bin/env python3
"""Run the production native Cloud guard against a disposable Actions library."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
    raise SystemExit('Native fixtures run only on GitHub macOS runners')
root = Path(__file__).resolve().parents[1]
source = root / 'build/notproton'
executable = root / 'build/evidence/check-cloud-guard'
subprocess.run(['clang', '-arch', 'arm64', '-std=c17', '-Wall', '-Wextra', '-Werror',
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
    env = dict(os.environ, GLUON_HOME=str(home), GLUON_STEAM_ROOT=str(steam))
    for name in ('DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'):
        env.pop(name, None)
    subprocess.run([str(executable)], env=env, check=True, timeout=90)

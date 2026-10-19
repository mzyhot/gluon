#!/usr/bin/env python3
"""Compare released and candidate wrapper/Wine startup on an isolated Actions Mac.

The Windows command fixture has no game, network, GPU, overlay or Steam IPC.
Time to its first file write measures startup overhead, not game load time.
"""
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
    raise SystemExit('Launch profiling requires a GitHub Actions Mac')
root = Path(__file__).resolve().parents[1]
runtime, probes, baseline = (Path(p).resolve() for p in sys.argv[1:])
steam = probes / 'cloud-steam'
compat = steam / 'steamapps/compatdata/123'
prefix = compat / 'pfx-wine'
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'tests'))
from test_migration import cache_bytes
(steam / 'appcache').mkdir(exist_ok=True)
(steam / 'appcache/appinfo.vdf').write_bytes(cache_bytes({'ufs': {'quota': 100}}))
(steam / 'steamapps/appmanifest_123.acf').write_text('"AppState" { "appid" "123" "name" "Launch fixture" }')
marker = probes / 'launch-ready'
batch = probes / 'launch-ready.cmd'
batch.write_text('@echo off\r\necho ready > "Z:' + str(marker).replace('/', '\\') + '"\r\n')
command = [str(runtime / 'lib/wine/x86_64-windows/cmd.exe'), '/c', 'Z:' + str(batch).replace('/', '\\')]
base_env = {k: v for k, v in os.environ.items()
            if not k.startswith(('GLUON_', 'Steam', 'STEAM_', 'WINE', 'DYLD_', 'PROTON_'))}
base_env.update(WINEPREFIX=str(prefix), WINEMSYNC='1', WINEDEBUG='fixme-all,+timestamp',
                GLUON_NO_STEAM_BRIDGE='1', GLUON_CAFFEINATE='0', GLUON_OVERLAY='0',
                STEAM_ROOT=str(steam), STEAM_COMPAT_APP_ID='123', SteamAppId='123',
                STEAM_COMPAT_DATA_PATH=str(compat), PYTHONDONTWRITEBYTECODE='1')
rows = []
for label, source in (('released', baseline), ('candidate', root)):
    home = probes / ('profile-' + label)
    (home / 'bin').mkdir(parents=True)
    (home / 'lib').mkdir()
    shutil.copytree(source / 'gluon', home / 'lib/gluon', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy2(source / 'tool/gluon', home / 'bin/gluon')
    (home / 'bin/gluon').chmod(0o755)
    host = home / 'bin/gluon-launcher-host'
    if label == 'released':
        subprocess.run(['clang', '-arch', 'arm64', '-fobjc-arc', '-framework', 'AppKit',
                        str(source / 'tools/gluon-launcher-host.m'), '-o', str(host)], check=True, timeout=60)
    else:
        shutil.copy2(root / 'build/launcher-host/gluon-launcher-host', host)
    (home / 'runtime').mkdir()
    (home / 'runtime/current').symlink_to(runtime)
    shim = home / 'compatibilitytools.d/gluon-proton/run'
    shim.parent.mkdir(parents=True)
    shutil.copy2(source / 'steamplay/run.sh', shim)
    env = dict(base_env, GLUON_HOME=str(home))
    for attempt in range(4):
        marker.unlink(missing_ok=True)
        started = time.monotonic()
        with (probes / ('launch-%s-%d.log' % (label, attempt))).open('w') as output:
            process = subprocess.Popen(['/bin/sh', str(shim), 'waitforexitandrun', *command],
                                       env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = started + 45
                while not marker.exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('%s launch did not reach the Windows fixture' % label)
                    time.sleep(.005)
                ready = time.monotonic() - started
                if process.wait(timeout=45):
                    raise RuntimeError('%s launcher failed after Windows started' % label)
                print('%s launch %d: Windows ready after %.4f s' % (label, attempt, ready), flush=True)
            finally:
                # -k also returns 1 when the launcher already stopped its server.
                # As in production shutdown, -w confirms that it is actually idle.
                subprocess.run([str(runtime / 'bin/wineserver'), '-k'], env=env, timeout=10)
                subprocess.run([str(runtime / 'bin/wineserver'), '-w'], env=env, timeout=10, check=True)
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        rows.append({'version': label, 'attempt': attempt, 'ready_seconds': round(ready, 4),
                     'warm': attempt != 0})
medians = {label: round(statistics.median(r['ready_seconds'] for r in rows
                                        if r['version'] == label and r['warm']), 4)
           for label in ('released', 'candidate')}
(probes / 'launch-profile.json').write_text(json.dumps({'samples': rows, 'warm_medians': medians}, indent=2))
print('Windows command fixture startup (no game/Steam/GPU):', medians)
with (probes / 'wine-native-leaks.log').open('w') as output:
    scan = subprocess.run(['/usr/bin/leaks', '--atExit', '--', str(runtime / 'bin/wine'),
                           'cmd', '/c', 'exit', '0'], env=dict(base_env, GLUON_HOME=str(home)),
                          stdout=output, stderr=subprocess.STDOUT, timeout=60)
subprocess.run([str(runtime / 'bin/wineserver'), '-k'], env=base_env, timeout=10)
subprocess.run([str(runtime / 'bin/wineserver'), '-w'], env=base_env, check=True, timeout=10)
# Keep a third-party Wine finding visible as evidence, distinct from the
# first-party host/guard checks that gate the build. Never label an error clean.
report = (probes / 'wine-native-leaks.log').read_text(errors='replace')
native_scan = {'exit_code': scan.returncode,
               'result': ('clean' if scan.returncode == 0 and '0 leaks for 0 total leaked bytes' in report
                          else 'findings-or-incomplete-scan'),
               'scope': 'Wine native allocations while running a Windows command, not game heaps'}
(probes / 'wine-native-leaks.json').write_text(json.dumps(native_scan, indent=2))
print('Wine native memory scan:', native_scan)
summary = os.environ.get('GITHUB_STEP_SUMMARY')
if summary:
    with open(summary, 'a') as output:
        output.write('\nLaunch profile: isolated Windows command; excludes game, Steam IPC and GPU loading.\n\n')
        output.write('| Version | Median repeat launch |\n|---|---|\n')
        for label, seconds in medians.items():
            output.write('| %s | %.4f s |\n' % (label, seconds))

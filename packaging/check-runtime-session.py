#!/usr/bin/env python3
"""Validate compiled Wine warm-session ownership in disposable CI prefixes.

Uses public Wine helpers and a source-owned lifecycle fixture. A detached child outlives its launcher;
idle waits must cover that child and registry persistence, while idle-only drain
must refuse it. No Steam, game, account, personal prefix or GPU is involved.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def windows(path):
    return 'Z:' + str(Path(path).resolve()).replace('/', '\\')


def wait_file(path, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('Wine child marker did not appear: ' + str(path))
        time.sleep(0.01)



def check(runtime, output):
    wine = str(runtime / 'bin/wine')
    server = str(runtime / 'bin/wineserver')
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix='gluon-runtime-session-') as directory, \
            (output / 'runtime-session.log').open('w') as log:
        work = Path(directory)
        for arch in ('x86_64', 'i386'):
            prefix = work / ('prefix-' + arch)
            environment = dict(os.environ, WINEPREFIX=str(prefix), WINEDEBUG='fixme-all',
                               WINEMSYNC='1', WINEDLLOVERRIDES='winemenubuilder.exe=d;mscoree,mshtml=')
            for name in list(environment):
                if name.startswith(('Steam', 'STEAM_', 'GLUON_')) or name == 'DYLD_INSERT_LIBRARIES':
                    environment.pop(name)
            owned = []

            def run(args, expected=0, timeout=20):
                result = subprocess.run(args, env=environment, stdout=log, stderr=log, timeout=timeout)
                if result.returncode != expected:
                    raise RuntimeError('command exited %s, expected %s: %r' % (result.returncode, expected, args))
                return result

            def spawn(args):
                child = subprocess.Popen(args, env=environment, stdout=log, stderr=log)
                owned.append(child)
                return child

            try:
                run([wine, 'wineboot', '--init'], timeout=40)
                run([server, '-w'])
                # No initial client must not leave a server alive forever.
                run([server, '--gluon-session=1'])
                run([server, '-w'], timeout=7)
                rows.append({'arch': arch, 'case': 'unused-warm-server-expires', 'passed': True})
                run([server, '--check-running'], expected=3)
                run([server, '--gluon-session=120'])
                run([server, '--check-running'])
                run([server, '--wait-idle'])
                cmd = runtime / 'lib/wine' / (arch + '-windows/cmd.exe')
                ready, release = work / (arch + '-ready'), work / (arch + '-release')
                batch = work / (arch + '-child.cmd')
                token = 'warm-fixture-' + arch
                batch.write_text('@echo off\r\n'
                                 'reg add "HKCU\\Software\\GluonWarmFixture" /v token /t REG_SZ /d "' + token + '" /f >nul\r\n'
                                 'echo ready>"' + windows(ready) + '"\r\n'
                                 ':wait\r\n'
                                 'if exist "' + windows(release) + '" goto done\r\n'
                                 'goto wait\r\n'
                                 ':done\r\n')
                # Native cmd's start returns with its child still alive. Use
                # the requested PE architecture for both parent and descendant.
                parent_batch = work / (arch + '-parent.cmd')
                parent_batch.write_text('@echo off\r\nstart "" /b "' + windows(cmd)
                                        + '" /d /c "' + windows(batch) + '"\r\n')
                parent = spawn([wine, str(cmd), '/d', '/c', windows(parent_batch)])
                wait_file(ready)
                if parent.wait(timeout=10) != 0:
                    raise RuntimeError('detached-child launcher failed')
                idle = spawn([server, '--wait-idle'])
                full = spawn([server, '-w'])
                time.sleep(0.15)
                if idle.poll() is not None or full.poll() is not None:
                    raise RuntimeError('session ended while its detached child was running')
                run([server, '--kill-idle'], expected=2)
                if idle.poll() is not None or full.poll() is not None:
                    raise RuntimeError('idle-only drain terminated a busy session')
                release.touch()
                if idle.wait(timeout=10) != 0:
                    raise RuntimeError('actual user-idle wait failed')
                if token not in (prefix / 'user.reg').read_text(errors='replace'):
                    raise RuntimeError('registry was not persisted before user-idle notification')
                if full.poll() is not None:
                    raise RuntimeError('warm server exited at game completion')
                repeated = work / (arch + '-repeated')
                repeat_batch = work / (arch + '-repeat.cmd')
                repeat_batch.write_text('@echo off\r\necho repeated>"' + windows(repeated) + '"\r\n')
                run([wine, str(cmd), '/d', '/c', windows(repeat_batch)])
                wait_file(repeated)
                run([server, '--wait-idle'])
                if full.poll() is not None:
                    raise RuntimeError('warm server was not retained for the repeat launch')
                run([server, '--kill-idle'])
                run([server, '-w'], timeout=7)
                if full.wait(timeout=2) != 0:
                    raise RuntimeError('full server wait did not confirm idle drain')
                rows.extend({'arch': arch, 'case': name, 'passed': True} for name in (
                    'detached-child-ownership', 'busy-idle-drain-refused', 'registry-flush-before-idle',
                    'warm-repeat-services-retained', 'idle-drain-confirmed'))
                run([server, '--wait-idle'], expected=3)
                run([server, '--kill-idle'], expected=3)
                rows.append({'arch': arch, 'case': 'absent-server-is-unsupported', 'passed': True})

                # Real SteamGameId follows the helper's system-process branch.
                # No SDK/client/account is used: bridge modules are disabled in
                # this fixture only. The helper must cover detached game children
                # yet finish promptly while stock services remain alive.
                fixture = output / ('session-lifecycle-' + ('i686' if arch == 'i386' else arch) + '.exe')
                helper = runtime / 'lib/wine' / (arch + '-windows/steam.exe')
                bootstrap = runtime / 'lib/wine/x86_64-windows/gluon-session-bootstrap.exe'
                run([server, '--gluon-session=120'])
                run([wine, str(bootstrap)])
                run([server, '--wait-idle'])
                run([server, '--seal-session'])
                ready, release = work / (arch + '-bridge-ready'), work / (arch + '-bridge-release')
                steam_directory = prefix / 'drive_c/Program Files (x86)/Steam'
                steam_directory.mkdir(parents=True, exist_ok=True)
                environment.update(SteamGameId='480', SteamAppId='480',
                                   STEAM_COMPAT_CLIENT_INSTALL_PATH=str(work / 'empty-steam'),
                                   WINEDLLOVERRIDES='winemenubuilder.exe=d;mscoree,mshtml,lsteamclient,vrclient,vrclient_x64=')
                bridge = spawn([wine, str(helper), str(fixture), '--detach', windows(ready), windows(release)])
                wait_file(ready)
                idle = spawn([server, '--wait-idle'])
                time.sleep(0.15)
                if bridge.poll() is not None or idle.poll() is not None:
                    raise RuntimeError('Steam helper or idle barrier ended before its detached game child')
                run([server, '--kill-idle'], expected=2)
                released_at = time.monotonic()
                release.touch()
                if bridge.wait(timeout=12) != 0 or idle.wait(timeout=12) != 0:
                    raise RuntimeError('Steam helper did not finish after all game descendants')
                wait_file(Path(str(ready) + '.done'))
                run([server, '--check-running'])
                rows.append({'arch': arch, 'case': 'normal-steam-id-bridge-reaped-before-idle',
                             'completion_seconds': round(time.monotonic() - released_at, 4), 'passed': True})
                run([server, '--kill-idle'])
                run([server, '-w'], timeout=10)
                for name in list(environment):
                    if name.startswith(('Steam', 'STEAM_')):
                        environment.pop(name)
                environment['WINEDLLOVERRIDES'] = 'winemenubuilder.exe=d;mscoree,mshtml='

                # A real Windows service uses StartServiceCtrlDispatcher and
                # becomes a Wine system process. It must revoke warm mode before
                # executing, and the game completion barrier must stay held.
                run([server, '--gluon-session=120'])
                run([wine, str(bootstrap)])
                run([server, '--wait-idle'])
                run([server, '--seal-session'])
                ready, release = work / (arch + '-service-ready'), work / (arch + '-service-release')
                run([wine, str(fixture), '--start-service', windows(ready), windows(release)])
                wait_file(ready)
                idle = spawn([server, '--wait-idle'])
                time.sleep(0.15)
                if idle.poll() is not None:
                    raise RuntimeError('Custom service was treated as an idle stock service')
                run([server, '--kill-idle'], expected=2)
                release.touch()
                wait_file(Path(str(ready) + '.done'))
                if idle.wait(timeout=15) != 0:
                    raise RuntimeError('Custom service cold completion failed')
                run([server, '-w'], timeout=15)
                run([server, '--check-running'], expected=3)
                rows.append({'arch': arch, 'case': 'custom-service-forces-full-cold-completion', 'passed': True})
            finally:
                with __import__('contextlib').suppress(Exception):
                    subprocess.run([server, '-k'], env=environment, stdout=log, stderr=log, timeout=7)
                    subprocess.run([server, '-w'], env=environment, stdout=log, stderr=log, timeout=7)
                for child in owned:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=2)
    (output / 'runtime-session.json').write_text(json.dumps(rows, indent=2) + '\n')
    print('Compiled Wine warm sessions: %d cases passed across both PE architectures' % len(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
        raise SystemExit('Compiled Wine session probes require GitHub Actions macOS')
    check(args.runtime.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()

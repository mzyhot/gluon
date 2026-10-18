"""Copy the system package's staged payload into the logged-in user's Gluon home.

The privileged package script drops privileges before invoking this module.
No user-owned path or script is processed as root. Runtime versions are immutable;
installation requires Steam/Wine idle, and existing prefixes are never copied.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

from . import runtime, steam, steamplay


def deploy(source):
    if os.geteuid() == 0:
        raise steamplay.SteamPlayError('Gluon user setup must not run as root')
    with steamplay.Installation().lock('package.lock'):
        return _deploy(source)


def _deploy(source):
    source = Path(source).resolve(strict=True)
    home = Path(steam.GLUON_HOME)
    installation = steamplay.Installation()
    installation.idle()
    request = runtime._request(source / 'runtime/pending.json')
    pending = steamplay._json(source / 'steamplay/pending.json')
    name = pending.get('name')
    if (request is None or not isinstance(name, str) or name in ('', '.', '..')
            or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-' for c in name)):
        raise steamplay.SteamPlayError('invalid staged package request')
    subprocess.run(['/bin/launchctl', 'bootout', 'gui/%d/com.gluon.agent' % os.getuid()], capture_output=True)
    home.mkdir(parents=True, exist_ok=True)
    for relative in ('runtime/' + request['name'], 'steamplay/payloads/' + name):
        src, dst = source / relative, home / relative
        if not src.is_dir() or src.is_symlink():
            raise steamplay.SteamPlayError('missing staged package directory: ' + relative)
        if dst.exists():
            if steamplay._tree(src) != steamplay._tree(dst):
                raise steamplay.SteamPlayError('existing version differs from package; use a newer package: ' + str(dst))
            continue
        temporary = dst.with_name('.staging-' + dst.name)
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        try:
            steamplay.System().copy(src, temporary)
            if steamplay._tree(src) != steamplay._tree(temporary):
                raise steamplay.SteamPlayError('staged copy failed verification: ' + relative)
            os.rename(temporary, dst)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    # Scripts and Python modules are small; copy while all native sessions are idle.
    for directory in ('bin', 'lib'):
        steamplay.System().copy(source / directory, home / directory)
    for retired in ('enable.py', 'metadata.py'):
        (home / 'lib/gluon' / retired).unlink(missing_ok=True)
    for relative in ('runtime/pending.json', 'steamplay/pending.json'):
        steamplay._atomic(home / relative, (source / relative).read_bytes())
    # Use the just-installed code. The environment is set by the privileged wrapper
    # to this account's HOME; no privileged interpreter imports from the home folder.
    result = subprocess.run(['/usr/bin/arch', '-arm64', '/usr/bin/python3', '-m', 'gluon', 'install'],
                            env=dict(os.environ, PYTHONPATH=str(home / 'lib')), capture_output=True, text=True)
    logs = home / 'logs'
    logs.mkdir(exist_ok=True)
    (logs / 'install.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise steamplay.SteamPlayError('Steam Play setup failed: ' + result.stderr.strip() + '\nSee ' + str(logs / 'install.log'))
    print(result.stdout, end='')
    agents = Path.home() / 'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / 'com.gluon.agent.plist'
    steamplay._atomic(plist, plistlib.dumps({'Label': 'com.gluon.agent',
        'ProgramArguments': [str(home / 'bin/gluon-agent')], 'RunAtLoad': True,
        'StartInterval': 60, 'ProcessType': 'Background', 'StandardErrorPath': str(logs / 'agent-stderr.log')}))
    subprocess.run(['/bin/launchctl', 'bootstrap', 'gui/%d' % os.getuid(), str(plist)], check=True)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise steamplay.SteamPlayError('expected a staged package directory')
        deploy(sys.argv[1])
    except (steamplay.SteamPlayError, runtime.RuntimeSelectionError, steam.SteamError,
            OSError, ValueError, subprocess.SubprocessError) as error:
        print('gluon: ' + str(error), file=sys.stderr)
        sys.exit(1)

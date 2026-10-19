#!/bin/bash
# Native compilation and NSApplication fixtures belong on a cloud runner only.
set -euo pipefail
if [[ ${GITHUB_ACTIONS:-} != true || $(uname -s) != Darwin ]]; then
    echo 'Launcher host checks require a GitHub Actions macOS runner.' >&2
    exit 1
fi
repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
mkdir -p build/launcher-host build/launcher-host-fixtures
python3 - <<'PY'
import os
import signal
import subprocess
import sys
architectures = ['-arch', 'arm64', '-arch', 'x86_64', '-mmacosx-version-min=11.0',
                 '-Wall', '-Wextra', '-Werror']
common = [*architectures, '-fobjc-arc', '-framework', 'AppKit']
fixtures = 'build/launcher-host-fixtures/'
for source, output in [('tools/gluon-launcher-host.m', 'build/launcher-host/gluon-launcher-host'),
                       ('packaging/launcher-host-controller.m', fixtures + 'launcher-host-controller'),
                       ('packaging/launcher-steam-owner.m', fixtures + 'launcher-steam-owner')]:
    subprocess.run(['clang', *common, source, '-o', output], check=True, timeout=60)
# Controlled stand-ins for Steam's client library; never packaged.
for defines, output in [([], 'steamclient.dylib'),
                        (['-DGLUON_FIXTURE_MISSING_CALLBACK'], 'steamclient-missing.dylib')]:
    subprocess.run(['clang', *architectures, '-dynamiclib', *defines,
                    'packaging/launcher-steam-client.c', '-o', fixtures + output],
                   check=True, timeout=60)
subprocess.run(['clang', *common, '-dynamiclib', 'packaging/launcher-appkit-startup.m',
                '-o', fixtures + 'appkit-startup.dylib'], check=True, timeout=60)
command = [sys.executable, 'packaging/test-launcher-host.py',
           'build/launcher-host/gluon-launcher-host',
           fixtures + 'launcher-host-controller', fixtures + 'launcher-steam-owner',
           fixtures + 'steamclient.dylib', fixtures + 'steamclient-missing.dylib',
           fixtures + 'appkit-startup.dylib']
process = subprocess.Popen(command, start_new_session=True)
try:
    code = process.wait(timeout=60)
except subprocess.TimeoutExpired:
    # Own fixture process group only: includes its native hosts, shell stubs
    # and the fake Steam applications.
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    raise SystemExit('Launcher host fixtures exceeded the 60-second hard deadline.')
raise SystemExit(code)
PY
python3 packaging/check-native-memory.py

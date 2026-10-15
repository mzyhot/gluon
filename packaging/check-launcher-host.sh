#!/bin/bash
# Native compilation and NSApplication fixtures belong on a cloud runner only.
set -euo pipefail
if [[ ${GITHUB_ACTIONS:-} != true || $(uname -s) != Darwin ]]; then
    echo 'Launcher host checks require a GitHub Actions macOS runner.' >&2
    exit 1
fi
repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
mkdir -p build/launcher-host
python3 - <<'PY'
import os
import signal
import subprocess
import sys
common = ['clang', '-arch', 'arm64', '-arch', 'x86_64', '-mmacosx-version-min=11.0',
          '-fobjc-arc', '-Wall', '-Wextra', '-Werror', '-framework', 'AppKit']
for source, output in [('tools/gluon-launcher-host.m', 'gluon-launcher-host'),
                       ('packaging/launcher-host-controller.m', 'launcher-host-controller')]:
    subprocess.run([*common, source, '-o', 'build/launcher-host/' + output],
                   check=True, timeout=60)
command = [sys.executable, 'packaging/test-launcher-host.py',
           'build/launcher-host/gluon-launcher-host',
           'build/launcher-host/launcher-host-controller']
process = subprocess.Popen(command, start_new_session=True)
try:
    code = process.wait(timeout=30)
except subprocess.TimeoutExpired:
    # Own fixture process group only: includes its native hosts and shell stubs.
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    raise SystemExit('Launcher host fixtures exceeded the 30-second hard deadline.')
raise SystemExit(code)
PY

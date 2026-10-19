#!/usr/bin/env python3
"""Scan only a disposable launcher host on a GitHub Actions Mac."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
    raise SystemExit('Memory scans require a GitHub Actions Mac')
root = Path(__file__).resolve().parents[1]
evidence = root / 'build/evidence'
evidence.mkdir(parents=True, exist_ok=True)
with (evidence / 'launcher-static-analysis.log').open('w') as output:
    subprocess.run(['clang', '--analyze', '-fobjc-arc', '-mmacosx-version-min=11.0',
                    '-Wall', '-Wextra', '-Werror', '-framework', 'AppKit',
                    '-Xanalyzer', '-analyzer-output=text', 'tools/gluon-launcher-host.m',
                    '-o', '/dev/null'], cwd=root, stdout=output, stderr=subprocess.STDOUT,
                   check=True, timeout=60)
with tempfile.TemporaryDirectory(prefix='gluon-memory-') as temporary:
    base = Path(temporary)
    ready = base / 'ready'
    script = base / 'launcher.sh'
    script.write_text('#!/bin/bash\ntouch "$GLUON_MEMORY_READY"\nexec /bin/sleep 90\n')
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('GLUON_', 'Steam', 'STEAM_', 'DYLD_', 'Malloc'))}
    env.update(GLUON_MEMORY_READY=str(ready), MallocStackLogging='1')
    with (evidence / 'launcher-memory-host.log').open('w') as log:
        process = subprocess.Popen([str(root / 'build/launcher-host/gluon-launcher-host'), str(script)],
                                   env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Disposable memory fixture failed to start')
                time.sleep(.025)
            # Include many timer polls and a live Cocoa run loop. No real Steam
            # client, prefix, game or personal application is loaded.
            time.sleep(.5)
            with (evidence / 'launcher-leaks.log').open('w') as output:
                subprocess.run(['/usr/bin/leaks', '-quiet', str(process.pid)], stdout=output,
                               stderr=subprocess.STDOUT, check=True, timeout=30)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
print('launcher memory: static analysis and live leaks scan passed')

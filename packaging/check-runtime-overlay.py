#!/usr/bin/env python3
"""Exercise compiled Wine child-overlay routing on a disposable Actions Mac.

The inserted library records only its own initialization. No Steam client,
game, account, graphics backend or personal prefix is involved.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
        raise SystemExit('Compiled overlay checks require a GitHub Actions Mac')
    if len(sys.argv) != 3:
        raise SystemExit('Usage: check-runtime-overlay.py RUNTIME PROBE_DIRECTORY')
    runtime, probes = (Path(p).resolve() for p in sys.argv[1:])
    work = probes / 'overlay-scope-runtime'
    work.mkdir()
    prefix = work / 'prefix'
    source = work / 'insertion.c'
    library = work / 'insertion.dylib'
    source.write_text('''#include <fcntl.h>
#include <stdlib.h>
#include <unistd.h>
__attribute__((constructor)) static void inserted(void)
{
    const char *path = getenv("GLUON_OVERLAY_FIXTURE_RECORD");
    if (!path) return;
    int fd = open(path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC, 0600);
    if (fd >= 0) { (void)write(fd, "inserted\\n", 9); close(fd); }
}
''')
    subprocess.run(['clang', '-arch', 'x86_64', '-dynamiclib', '-Wall', '-Wextra', '-Werror',
                    str(source), '-o', str(library)], check=True, timeout=30)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('GLUON_', 'Steam', 'STEAM_', 'WINE', 'DYLD_', 'PROTON_'))}
    env.update(WINEPREFIX=str(prefix), WINEMSYNC='1', WINEDEBUG='fixme-all',
               WINEDLLOVERRIDES='mscoree,mshtml,winemenubuilder.exe=d')
    wine, server = str(runtime / 'bin/wine'), str(runtime / 'bin/wineserver')
    rows = []
    windows = lambda p: 'Z:' + str(p).replace('/', '\\')
    with (probes / 'overlay-scope-runtime.log').open('w') as log:
        def run(command, environment=env, timeout=30):
            subprocess.run(command, env=environment, cwd=work, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=timeout)
        try:
            run([wine, 'wineboot', '--init'], timeout=60)
            run([server, '-w'])
            for arch in ('x86_64', 'i386'):
                directory = work / arch
                directory.mkdir()
                image = directory / 'cmd.exe'
                other = directory / 'other.exe'
                shutil.copy2(runtime / ('lib/wine/' + arch + '-windows/cmd.exe'), image)
                shutil.copy2(image, other)
                cases = (
                    ('browser', [], True, True, False),
                    ('gpu', ['--type=gpu-process'], True, True, False),
                    ('renderer', ['--type=renderer'], False, True, False),
                    ('utility', ['--type=utility'], False, True, False),
                    ('unknown', ['--type=future-role'], True, True, False),
                    ('ambiguous', ['--type=renderer', '--type=gpu-process'], True, True, False),
                    ('unmarked-renderer', ['--type=renderer'], True, False, False),
                    ('other-renderer', ['--type=renderer'], True, True, True),
                )
                for name, arguments, expected, marked, different in cases:
                    label = arch + '-' + name
                    ready, inserted = work / (label + '.ready'), work / (label + '.inserted')
                    child = work / (label + '-child.cmd')
                    parent = work / (label + '-parent.cmd')
                    child.write_text('@echo off\r\necho GLUON_OVERLAY_CHILD_READY > "' + windows(ready) + '"\r\n')
                    # A real Windows child creation supplies the actual PE image
                    # path; the initial Unix Wine wrapper has no such path yet.
                    parent.write_text('@echo off\r\n"' + windows(other if different else image) +
                                      '" /c "' + windows(child) + '" ' + ' '.join(arguments) + '\r\n')
                    candidate = dict(env, GLUON_OVERLAY_INSERT=str(library),
                                     GLUON_OVERLAY_FIXTURE_RECORD=str(inserted))
                    if marked:
                        candidate.update(GLUON_CHROMIUM_OVERLAY_SCOPE='in-process-gpu-v1',
                                         GLUON_CHROMIUM_OVERLAY_IMAGE=str(image))
                    run([wine, 'cmd', '/c', windows(parent)], candidate)
                    if not ready.is_file() or ready.read_text().strip() != 'GLUON_OVERLAY_CHILD_READY':
                        raise RuntimeError('Windows child did not execute: ' + label)
                    count = len(inserted.read_text().splitlines()) if inserted.exists() else 0
                    rows.append(dict(case=label, insertions=count, expected=int(expected)))
                    if count != int(expected):
                        raise RuntimeError('Unexpected native overlay initialization: ' + label + ' ' + str(count))
                    print('compiled overlay scope: ' + label + ' passed', flush=True)
        finally:
            if prefix.exists():
                subprocess.run([server, '-k'], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=10)
                run([server, '-w'], timeout=10)
            (probes / 'overlay-scope-runtime.json').write_text(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    main()

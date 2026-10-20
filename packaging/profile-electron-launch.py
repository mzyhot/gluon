#!/usr/bin/env python3
"""Actions-only Windows Electron startup evidence, using a generated offline app.

Compare v0.4.2 and candidate wrappers with the SAME newly built Wine runtime.
Cold means a stopped Wine server, not a flushed macOS disk/shader cache. Warm
means the same verified native Wine server survives between completed launches.
The native timestamp is the first observation that this fixture's WineWindow is
on screen in WindowServer. It does not assert a displayed compositor frame, game
readiness, Steam IPC/overlay performance, or Happy Wheels launch time.
"""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile

from warm_process_evidence import inspect_retained_services


VERSION = '44.4.1'
ARCHIVE = 'electron-v44.4.1-win32-x64.zip'
# Official electron/electron v44.4.1 assets, retrieved 2026-10-19. Pin both the
# published manifest and its selected entry; a replaced manifest is not trusted.
MANIFEST_SHA256 = '54dba5be57cb6b14e701e012ce1967d946799b152871d8528a50767087066cc3'
ARCHIVE_SHA256 = '34bc07977d6c43b6514b956a5f2e3292255daa3838a49e6110f3fe19ffafb83f'
RELEASE = 'https://github.com/electron/electron/releases/download/v' + VERSION + '/'
BASELINE_REF = 'v0.4.2'
LOG_LIMIT = 512 * 1024

MAIN_JS = r'''const { app, BrowserWindow, ipcMain, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const directory = process.env.GLUON_ELECTRON_FIXTURE_DIR;
const title = process.env.GLUON_ELECTRON_FIXTURE_TITLE;
if (!directory || !title) process.exit(90);
const record = (name, value = {}) => fs.writeFileSync(path.join(directory, name + '.json'),
  JSON.stringify({ event: name, pid: process.pid, ...value }));
app.setPath('userData', process.env.GLUON_ELECTRON_FIXTURE_USER_DATA);
app.commandLine.appendSwitch('disable-background-networking');
app.commandLine.appendSwitch('disable-component-update');
app.commandLine.appendSwitch('no-first-run');
let window;
let framed = false;
let shown = false;
const fail = message => { record('error', { message }); app.exit(91); };
process.on('uncaughtException', error => fail(String(error.stack || error)));
app.on('child-process-gone', (_event, details) => {
  if (details.reason !== 'clean-exit') fail('Child process: ' + JSON.stringify(details));
});
app.on('before-quit', () => record('quit'));
app.whenReady().then(() => {
  record('app-ready', { versions: process.versions, platform: process.platform });
  session.defaultSession.webRequest.onBeforeRequest({ urls: ['http://*/*', 'https://*/*'] },
    (_details, callback) => callback({ cancel: true }));
  ipcMain.on('fixture-frame', (_event, result) => {
    if (framed) return;
    framed = true;
    if (!result || !['webgl', 'canvas2d-fallback'].includes(result.mode) ||
        JSON.stringify(result.pixel) !== JSON.stringify([51, 102, 153, 255]))
      return fail('Renderer did not draw the expected offline canvas: ' + JSON.stringify(result));
    record('renderer-frame', result);
  });
  window = new BrowserWindow({ width: 640, height: 480, title, show: false,
    backgroundColor: '#336699', webPreferences: { contextIsolation: true,
      nodeIntegration: false, sandbox: false, preload: path.join(__dirname, 'preload.js') } });
  window.on('page-title-updated', event => event.preventDefault());
  window.webContents.on('render-process-gone', (_event, details) => fail(JSON.stringify(details)));
  window.webContents.on('did-fail-load', (_event, code, message) => fail(code + ': ' + message));
  window.once('ready-to-show', () => {
    record('ready-to-show');
    window.show();
    shown = true;
    record('show-called');
  });
  window.loadFile(path.join(__dirname, 'index.html'));
  setInterval(() => {
    if (shown && framed && fs.existsSync(path.join(directory, 'release'))) app.quit();
  }, 20);
});
setTimeout(() => fail('Offline fixture exceeded its 45-second deadline'), 45000).unref();
'''

PRELOAD_JS = r'''const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('fixture', { frame: value => ipcRenderer.send('fixture-frame', value) });
'''

RENDERER_JS = r'''const canvas = document.querySelector('canvas');
let mode = 'webgl';
let pixel;
const gl = canvas.getContext('webgl', { preserveDrawingBuffer: true });
if (gl) {
  gl.clearColor(0.2, 0.4, 0.6, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  const bytes = new Uint8Array(4);
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, bytes);
  pixel = Array.from(bytes);
} else {
  mode = 'canvas2d-fallback';
  const drawing = canvas.getContext('2d');
  drawing.fillStyle = '#336699';
  drawing.fillRect(0, 0, canvas.width, canvas.height);
  pixel = Array.from(drawing.getImageData(0, 0, 1, 1).data);
}
requestAnimationFrame(() => requestAnimationFrame(() => window.fixture.frame({ mode, pixel })));
'''


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(data)
    return value.hexdigest()


def download(name: str, destination: Path, expected: str, limit: int) -> None:
    started = time.monotonic()
    request = urllib.request.Request(RELEASE + name, headers={'User-Agent': 'Gluon-Actions-Electron-Fixture'})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open('wb') as output:
        total = 0
        while data := response.read(1024 * 1024):
            total += len(data)
            if total > limit or time.monotonic() - started > 180:
                raise RuntimeError('Official Electron fixture download exceeded its bound')
            output.write(data)
    if digest(destination) != expected:
        raise RuntimeError('Official Electron checksum mismatch: ' + name)


def extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        total = 0
        entries = source.infolist()
        if len(entries) > 4096:
            raise RuntimeError('Unexpected Electron archive entry count')
        for entry in entries:
            name = Path(entry.filename)
            total += entry.file_size
            if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                    or stat.S_ISLNK(entry.external_attr >> 16) or total > 1024 * 1024 * 1024):
                raise RuntimeError('Unsafe or oversized official Electron archive')
        source.extractall(destination)
    if not all((destination / name).is_file() for name in ('electron.exe', 'chrome_100_percent.pak', 'version')):
        raise RuntimeError('Unexpected official Electron archive layout')
    if (destination / 'version').read_text().strip() not in (VERSION, 'v' + VERSION):
        raise RuntimeError('Unexpected Electron fixture version')


def baseline_commit(root: Path, baseline: Path) -> str:
    commit = subprocess.check_output(['git', 'rev-parse', BASELINE_REF + '^{commit}'], cwd=root, text=True).strip()
    paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', commit, 'gluon',
        'tool/gluon', 'tools/gluon-launcher-host.m', 'tools/gluon-steam-client.h', 'steamplay/run.sh'],
        cwd=root, text=True).splitlines()
    if len(paths) < 5:
        raise RuntimeError('Missing v0.4.2 baseline source inventory')
    for name in paths:
        expected = subprocess.check_output(['git', 'show', commit + ':' + name], cwd=root)
        if not (baseline / name).is_file() or (baseline / name).read_bytes() != expected:
            raise RuntimeError('Baseline source differs from v0.4.2: ' + name)
    return commit


class Flock(ctypes.Structure):
    # Darwin <sys/fcntl.h>, arm64 and x86_64. F_GETLK queries the existing lock;
    # this fixture neither takes that lock nor sends an ad-hoc server request.
    _fields_ = [('start', ctypes.c_int64), ('length', ctypes.c_int64),
                ('pid', ctypes.c_int), ('type', ctypes.c_short), ('whence', ctypes.c_short)]


def server_identity(prefix: Path):
    info = prefix.stat()
    lock = Path('/tmp/.wine-%d/server-%x-%x/lock' % (os.getuid(), info.st_dev, info.st_ino))
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        query = Flock(0, 1, 0, fcntl.F_WRLCK, os.SEEK_SET)
        result = Flock.from_buffer_copy(fcntl.fcntl(fd, fcntl.F_GETLK, bytes(query)))
    finally:
        os.close(fd)
    if result.type == fcntl.F_UNLCK:
        return None
    if result.type != fcntl.F_WRLCK or result.pid <= 1:
        raise RuntimeError('Unexpected disposable Wine server lock')
    process = subprocess.check_output(['/bin/ps', '-p', str(result.pid), '-o', 'lstart=', '-o', 'comm='], text=True).strip()
    if 'wineserver' not in process:
        raise RuntimeError('Fixture lock owner is not wineserver')
    return {'pid': result.pid, 'started_and_command': process}


def trim(path: Path) -> None:
    if path.is_file() and path.stat().st_size > LOG_LIMIT:
        with path.open('rb') as source:
            source.seek(-LOG_LIMIT, os.SEEK_END)
            tail = source.read()
        path.write_bytes(b'[Earlier fixture output omitted; retained tail follows.]\n' + tail)


def main() -> None:
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'darwin':
        raise SystemExit('Electron launch profiling requires a GitHub Actions Mac')
    if len(sys.argv) != 4:
        raise SystemExit('Usage: profile-electron-launch.py RUNTIME PROBE_DIRECTORY V0.4.2_BASELINE')
    root = Path(__file__).resolve().parents[1]
    runtime, probes, baseline = (Path(arg).resolve() for arg in sys.argv[1:])
    probes.mkdir(parents=True, exist_ok=True)
    result_path = probes / 'electron-launch-profile.json'
    result = {'fixture': 'official Electron ' + VERSION + ' Windows x64; generated offline canvas',
              'baseline_ref': BASELINE_REF, 'baseline_commit': baseline_commit(root, baseline),
              'archive_sha256': ARCHIVE_SHA256, 'manifest_sha256': MANIFEST_SHA256,
              'runtime_patch_hash': (runtime / '.gluon-wine-patches').read_text().strip(),
              'samples': [], 'stop': {'status': 'not-run'}, 'status': 'running',
              'limits': [
                  'No Happy Wheels assets, custom addon, Steam account, Steam IPC or real Steam overlay.',
                  'Pure Electron excludes steam.exe helper setup; no Steam API is required by the generated app.',
                  'Cold means Wine server stopped; OS, Electron profile and shader caches stay warm.',
                  'Warm repeats must retain the same locked native Wine server process.',
                  'An external observer polls WindowServer metadata every 20 ms; it does not capture pixels.',
                  'Window identity requires the exact generated bundle, its Wine executable and expected normal-window bounds.',
                  'The unique title is checked when available; missing privacy-filtered names do not weaken bundle/PID checks.',
                  'A generated sandbox-disabled offline fixture does not represent sandboxed workloads.',
                  'Renderer WebGL failure uses and explicitly records a canvas2d fallback.',
                  'No latency threshold or Happy Wheels speed claim is inferred from these samples.']}
    expected = subprocess.check_output([str(root / 'packaging/apply-wine-patches.sh'), '--hash'], text=True).strip()
    if result['runtime_patch_hash'] != expected:
        raise RuntimeError('Electron profile runtime does not match the compiled Wine patches')
    applied = set((runtime / 'licenses/gluon-wine-patches/APPLIED.txt').read_text().splitlines())
    if not {'wine-macos-app-bundle', 'wine-macos-overlay-scope', 'wine-session-idle'} <= applied:
        raise RuntimeError('Electron profile requires compiled app-bundle, overlay-scope and session-idle patches')
    template = probes / 'cloud-steam/steamapps/compatdata/123/pfx-wine'
    if not all((template / name).is_file() for name in ('system.reg', 'user.reg')):
        raise RuntimeError('Expected the validated complete-runtime probe prefix')
    base = {key: value for key, value in os.environ.items()
            if not key.startswith(('GLUON_', 'Steam', 'STEAM_', 'WINE', 'DYLD_', 'PROTON_', 'ELECTRON_',
                                   'DXMT_', 'DXVK_', 'MVK_', 'MTL_'))}
    base.update(WINEMSYNC='1', WINEDEBUG='fixme-all,+timestamp',
                WINEDLLOVERRIDES='mscoree,mshtml,winemenubuilder.exe=d',
                GLUON_NO_STEAM_BRIDGE='1', GLUON_CAFFEINATE='0', GLUON_OVERLAY='0',
                GLUON_FOCUS='0', PYTHONDONTWRITEBYTECODE='1',
                GST_PLUGIN_SYSTEM_PATH_1_0=str(runtime / 'lib/gstreamer-1.0'),
                GST_PLUGIN_SCANNER_1_0=str(runtime / 'libexec/gstreamer-1.0/gst-plugin-scanner'))
    wine_server = str(runtime / 'bin/wineserver')
    subprocess.run([wine_server, '-w'], env=dict(base, WINEPREFIX=str(template)), check=True, timeout=15)
    contexts = []
    active = None
    active_observer = None
    temporary = None
    try:
        # Keep the download, extracted public binaries and disposable prefix
        # clones outside the runtime and uploaded graphics-probes directory.
        temporary = tempfile.TemporaryDirectory(prefix='electron-profile-', dir=probes.parent)
        work = Path(temporary.name)
        manifest = work / 'SHASUMS256.txt'
        archive = work / ARCHIVE
        download('SHASUMS256.txt', manifest, MANIFEST_SHA256, 128 * 1024)
        entries = [line.split() for line in manifest.read_text().splitlines()]
        if [entry[0] for entry in entries if len(entry) == 2 and entry[1].lstrip('*') == ARCHIVE] != [ARCHIVE_SHA256]:
            raise RuntimeError('Pinned Electron archive does not match its published checksum entry')
        download(ARCHIVE, archive, ARCHIVE_SHA256, 350 * 1024 * 1024)
        fixture_library = work / 'fixture-library'
        electron = fixture_library / 'steamapps/common/Gluon Electron CI'
        electron.mkdir(parents=True)
        (fixture_library / 'steamapps/appmanifest_123.acf').write_text(
            '"AppState" { "appid" "123" "name" "Gluon Electron CI" }')
        extract(archive, electron)
        application = electron / 'resources/app'
        application.mkdir()
        (application / 'package.json').write_text(json.dumps({'name': 'gluon-electron-ci', 'version': '1.0.0', 'main': 'main.js'}))
        for name, source in (('main.js', MAIN_JS), ('preload.js', PRELOAD_JS), ('renderer.js', RENDERER_JS)):
            (application / name).write_text(source)
        (application / 'index.html').write_text('<!doctype html><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'self\'; connect-src \'none\'">'
            '<canvas width="320" height="240"></canvas><script src="renderer.js"></script>')
        observer = work / 'window-observer'
        subprocess.run(['clang', '-arch', 'arm64', '-fobjc-arc',
            '-Wall', '-Wextra', '-Werror', '-framework', 'AppKit', '-framework', 'CoreGraphics',
            str(root / 'packaging/profile-electron-window.m'), '-o', str(observer)], check=True, timeout=60)
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / 'tests'))
        from test_migration import cache_bytes

        def command(context, *arguments, check=True, timeout=15):
            with (probes / ('electron-' + context['label'] + '-server.log')).open('a') as log:
                return subprocess.run([wine_server, *arguments], env=context['env'],
                    stdout=log, stderr=subprocess.STDOUT, check=check, timeout=timeout)

        def cold(context):
            command(context, '-k', check=False)
            command(context, '-w')
            if server_identity(context['prefix']) is not None:
                raise RuntimeError('Fixture server still running after confirmed stop')

        for label, source, warm in (('released-cold', baseline, False),
                                     ('candidate-cold', root, False), ('candidate-warm', root, True)):
            home = work / label / 'gluon'
            (home / 'bin').mkdir(parents=True)
            (home / 'lib').mkdir()
            shutil.copytree(source / 'gluon', home / 'lib/gluon', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy2(source / 'tool/gluon', home / 'bin/gluon')
            (home / 'bin/gluon').chmod(0o755)
            host = home / 'bin/gluon-launcher-host'
            if source == baseline:
                subprocess.run(['clang', '-arch', 'arm64', '-fobjc-arc', '-framework', 'AppKit',
                    str(source / 'tools/gluon-launcher-host.m'), '-o', str(host)], check=True, timeout=60)
            else:
                shutil.copy2(root / 'build/launcher-host/gluon-launcher-host', host)
            (home / 'runtime').mkdir()
            (home / 'runtime/current').symlink_to(runtime)
            shim = home / 'compatibilitytools.d/gluon-proton/run'
            shim.parent.mkdir(parents=True)
            shutil.copy2(source / 'steamplay/run.sh', shim)
            steam = work / label / 'steam'
            compat = steam / 'steamapps/compatdata/123'
            compat.mkdir(parents=True)
            prefix = compat / 'pfx-wine'
            shutil.copytree(template, prefix, symlinks=True)
            (steam / 'appcache').mkdir()
            (steam / 'appcache/appinfo.vdf').write_bytes(cache_bytes({'ufs': {'quota': 0}}))
            (steam / 'steamapps/appmanifest_123.acf').write_text(
                '"AppState" { "appid" "123" "name" "Gluon Electron CI" }')
            user_data = work / label / 'electron-user-data'
            user_data.mkdir()
            env = dict(base, WINEPREFIX=str(prefix), GLUON_HOME=str(home), STEAM_ROOT=str(steam),
                GLUON_STEAM_ROOT=str(steam), STEAM_COMPAT_APP_ID='123', SteamAppId='123',
                STEAM_COMPAT_DATA_PATH=str(compat), GLUON_WARM_SERVER='1' if warm else '0',
                GLUON_ELECTRON_FIXTURE_USER_DATA='Z:' + str(user_data).replace('/', '\\'),
                GST_REGISTRY_1_0=str(work / label / 'gstreamer-registry.bin'),
                DXMT_SHADER_CACHE_PATH=str(work / label / 'shader-cache'), DXMT_LOG_PATH='none',
                DXVK_STATE_CACHE_PATH=str(work / label), DXVK_LOG_PATH=str(work / label))
            context = {'label': label, 'prefix': prefix, 'env': env, 'home': home, 'shim': shim,
                       'warm': warm, 'server': None}
            contexts.append(context)
            cold(context)

            def launch(attempt, stop=False):
                nonlocal active, active_observer
                if not warm:
                    cold(context)
                prior = server_identity(prefix)
                if warm and attempt != 'warmup' and prior != context['server']:
                    raise RuntimeError('True warm sample lost its previous native server identity')
                record = work / label / str(attempt)
                record.mkdir()
                title = 'Gluon Electron CI ' + uuid.uuid4().hex
                environment = dict(env, GLUON_ELECTRON_FIXTURE_TITLE=title,
                    GLUON_ELECTRON_FIXTURE_DIR='Z:' + str(record).replace('/', '\\'))
                log_path = probes / ('electron-%s-%s.log' % (label, attempt))
                times = {}
                native = None
                retained_ownership = None
                with log_path.open('w') as output:
                    bundle = home / 'apps.noindex/123/Gluon Electron CI.app'
                    active_observer = subprocess.Popen([str(observer), str(bundle), title,
                        str(record / 'native.json'), str(record / 'observer-ready')], env=base,
                        stdout=output, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 5
                    while not (record / 'observer-ready').is_file():
                        if active_observer.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('Native CI observer did not become ready')
                        time.sleep(.005)
                    started = time.monotonic_ns()
                    active = subprocess.Popen(['/bin/sh', str(shim), 'waitforexitandrun',
                        str(electron / 'electron.exe'), '--no-sandbox'], env=environment, cwd=electron,
                        stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                    deadline = time.monotonic() + 55
                    while len(times) < 3:
                        error = record / 'error.json'
                        if error.is_file():
                            raise RuntimeError('Electron fixture error: ' + error.read_text())
                        for name in ('ready-to-show', 'renderer-frame', 'native'):
                            path = record / (name + '.json')
                            if name not in times and path.is_file():
                                try:
                                    value = json.loads(path.read_text())
                                except (ValueError, OSError):
                                    continue
                                now = time.monotonic_ns()
                                if name == 'native':
                                    native = value
                                    stamp = value.get('monotonic_ns', 0)
                                    if not started <= stamp <= now or min(value.get('width', 0), value.get('height', 0)) < 100:
                                        raise RuntimeError('Invalid first native window observation')
                                    times[name] = (stamp - started) / 1e9
                                else:
                                    times[name] = (now - started) / 1e9
                        if active.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('Electron fixture did not reach a native window and renderer frame')
                        if active_observer.poll() is not None and 'native' not in times:
                            raise RuntimeError('Native CI observer exited without this fixture window')
                        time.sleep(.005)
                    if active_observer.wait(timeout=3) != 0:
                        raise RuntimeError('Native CI observer failed after writing its result')
                    active_observer = None
                    identity = server_identity(prefix)
                    if identity is None or (prior is not None and identity != prior):
                        raise RuntimeError('Electron sample did not use the expected native Wine server')
                    if stop:
                        active.send_signal(signal.SIGTERM)
                        active.wait(timeout=15)
                        command(context, '-w')
                        if server_identity(prefix) is not None:
                            raise RuntimeError('Stop left the fixture Wine server running')
                        result['stop'] = {'status': 'passed', 'wrapper_exit_code': active.returncode,
                                          'window_observed': True, 'server_drained': True}
                    else:
                        (record / 'release').write_text('quit\n')
                        code = active.wait(timeout=25)
                        if code != 0 or not (record / 'quit.json').is_file():
                            raise RuntimeError('Electron did not complete a clean fixture-owned quit')
                        command(context, '--wait-idle' if warm else '-w')
                        retained = server_identity(prefix)
                        if warm and retained != identity:
                            raise RuntimeError('Candidate failed to retain its native Wine server after clean quit')
                        if not warm and retained is not None:
                            raise RuntimeError('Cold fixture did not stop its native Wine server')
                        if warm:
                            command(context, '--check-running')
                            retained_ownership = inspect_retained_services(prefix, runtime, bundle, active.pid)
                        context['server'] = retained
                    active = None
                frame = json.loads((record / 'renderer-frame.json').read_text())
                ready = json.loads((record / 'app-ready.json').read_text())
                if ready.get('platform') != 'win32' or ready.get('versions', {}).get('electron') != VERSION:
                    raise RuntimeError('The pinned Windows Electron fixture did not execute')
                row = {'wrapper': label, 'attempt': attempt, 'excluded_warmup': attempt == 'warmup',
                       'server_reused': prior is not None,
                       'native_window_onscreen_seconds': round(times['native'], 6),
                       'ready_to_show_observed_seconds': round(times['ready-to-show'], 6),
                       'renderer_frame_observed_seconds': round(times['renderer-frame'], 6),
                       'canvas_mode': frame['mode'], 'electron_versions': ready['versions'],
                       'server_identity': identity, 'native_window': native,
                       'retained_ownership': retained_ownership,
                       'clean_exit': not stop, 'stop_case': stop}
                result['samples'].append(row)
                trim(log_path)
                print('Electron %s %s: native window %.4f s, %s, server reused=%s' %
                      (label, attempt, times['native'], frame['mode'], prior is not None), flush=True)

            launch('warmup')
            for attempt in range(3):
                launch(attempt)
            if warm:
                launch('stop', stop=True)
            cold(context)
            # Retain only bounded text evidence, never binaries or prefix data.
            for source_log in (home / 'logs').glob('*.log'):
                target = probes / ('electron-%s-%s' % (label, source_log.name))
                shutil.copy2(source_log, target)
                trim(target)
        result['medians'] = {label: round(statistics.median(row['native_window_onscreen_seconds']
            for row in result['samples'] if row['wrapper'] == label and not row['excluded_warmup']
            and not row['stop_case']), 6) for label in ('released-cold', 'candidate-cold', 'candidate-warm')}
        result['status'] = 'passed'
    except BaseException as error:
        result['status'] = 'failed'
        result['error'] = str(error)
        raise
    finally:
        # All control commands are scoped to a disposable fixture prefix.
        for context in contexts:
            with (probes / ('electron-' + context['label'] + '-cleanup.log')).open('w') as log:
                for option in ('-k', '-w'):
                    try:
                        subprocess.run([wine_server, option], env=context['env'], stdout=log,
                                       stderr=subprocess.STDOUT, check=option == '-w', timeout=15)
                    except (OSError, subprocess.SubprocessError) as error:
                        result.setdefault('cleanup_errors', []).append(str(error))
        if active is not None and active.poll() is None:
            os.killpg(active.pid, signal.SIGKILL)
            active.wait(timeout=5)
        if active_observer is not None and active_observer.poll() is None:
            active_observer.terminate()
            active_observer.wait(timeout=5)
        for context in contexts:
            for source_log in (context['home'] / 'logs').glob('*.log'):
                target = probes / ('electron-%s-%s' % (context['label'], source_log.name))
                shutil.copy2(source_log, target)
                trim(target)
        if temporary is not None:
            temporary.cleanup()
        for path in probes.glob('electron-*.log'):
            trim(path)
        if result.get('cleanup_errors'):
            result['status'] = 'failed'
        result_path.write_text(json.dumps(result, indent=2) + '\n')
    if result.get('cleanup_errors'):
        raise RuntimeError('Electron fixture cleanup did not finish')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as output:
            output.write('\nOffline public Electron %s native-window startup; excludes games and Steam IPC.\n\n' % VERSION)
            output.write('| Wrapper / Wine server | Median native window |\n|---|---|\n')
            for label, seconds in result['medians'].items():
                output.write('| %s | %.4f s |\n' % (label, seconds))
            output.write('\nOwn-window WindowServer metadata, not pixels. Cold retains OS/shader caches; '
                         'warm retains the same Wine server. No end-to-end game speed claim.\n')


if __name__ == '__main__':
    main()

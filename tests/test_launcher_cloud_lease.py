"""The real shell serializes every plain-Wine game with Cloud preparation.

All installed modules and runtime commands below are isolated fixture helpers;
no personal Steam data, Wine process, game or native app is touched.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherCloudLeaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='gluon-launch-cloud-lease-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / 'Gluon'
        self.compat = self.root / 'compat'
        self.prefix = self.compat / 'pfx-wine'
        self.prefix.mkdir(parents=True)
        (self.prefix / 'system.reg').write_text('fixture')
        self.lease = self.compat / '.gluon-session.lock'
        self.lease.touch(mode=0o600)
        self.state = self.root / 'cloud-mode'
        self.state.write_text('prefix')
        package = self.home / 'lib/gluon'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        (package / 'session.py').write_text((ROOT / 'gluon/session.py').read_text())
        (package / 'runtime.py').write_text('')
        (package / 'steamplay.py').write_text('class SteamPlayError(RuntimeError): pass\n')
        (package / 'launch.py').write_text('''import os
from pathlib import Path
root = Path(os.environ['LEASE_FIXTURE_ROOT'])
(root / 'early-checked').touch()
print('cloud=' + os.environ.get('LEASE_EARLY_MODE', 'prefix'))
print('overlay=')
print('bundle=')
''')
        (package / 'cloud.py').write_text('''import fcntl, os
from pathlib import Path
class CloudError(RuntimeError): pass
def check_app(appid, compat):
    root = Path(os.environ['LEASE_FIXTURE_ROOT'])
    with (Path(compat) / '.gluon-session.lock').open('a') as check:
        try:
            fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise CloudError('post-wait check did not hold the prefix lease')
    (root / 'late-checked').touch()
    mode = (root / 'cloud-mode').read_text()
    if mode == 'refuse':
        raise CloudError('Cloud save rules changed while this launch waited')
    return mode
def prepare(*args, **kwargs):
    raise AssertionError('automatic Cloud preparation must not run under the lease')
''')
        runtime = self.home / 'runtime/current/bin'
        runtime.mkdir(parents=True)
        stub = '#!%s\n' % sys.executable + '''import fcntl, json, os, pathlib, sys
root = pathlib.Path(os.environ['LEASE_FIXTURE_ROOT'])
try:
    os.fstat(9)
    inherited = True
except OSError:
    inherited = False
held = False
with (pathlib.Path(os.environ['WINEPREFIX']).parent / '.gluon-session.lock').open('a') as check:
    try:
        fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        held = True
with (root / 'wine-events').open('a') as stream:
    stream.write(json.dumps({'role': pathlib.Path(sys.argv[0]).name, 'args': sys.argv[1:],
                             'lease_held': held, 'lease_inherited': inherited,
                             'appid': os.environ.get('SteamAppId'), 'host': os.environ.get('GLUON_HOST_PID'),
                             'overlay': os.environ.get('GLUON_OVERLAY_INSERT')}) + '\\n')
if sys.argv[1:2] == ['winepath']:
    print(sys.argv[-1])
sys.exit(int(os.environ.get('LEASE_WINE_STATUS', '0')))
'''
        for name in ('wine', 'wineserver'):
            command = runtime / name
            command.write_text(stub)
            command.chmod(0o755)
        self.launcher = self.home / 'bin/gluon'
        self.launcher.parent.mkdir()
        self.launcher.write_text((ROOT / 'tool/gluon').read_text().replace(
            '/usr/bin/arch -arm64 /usr/bin/python3', shlex.quote(sys.executable)).replace(
            '/usr/bin/osascript', '/usr/bin/true'))
        self.launcher.chmod(0o755)
        self.game = self.root / 'game.exe'
        self.game.write_text('fixture, never executed')
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(('GLUON_', 'STEAM_', 'Steam', 'WINE', 'PROTON_', 'DYLD_'))}
        self.environment.update(GLUON_HOME=str(self.home), GLUON_NATIVE_CLOUD='1',
                                GLUON_CAFFEINATE='0', STEAM_COMPAT_DATA_PATH=str(self.compat),
                                SteamAppId='123', LEASE_FIXTURE_ROOT=str(self.root))
        self.process = None
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.communicate(timeout=3)

    def start(self, verb='waitforexitandrun', operand=None):
        self.process = subprocess.Popen([str(self.launcher), verb, str(self.game) if operand is None else operand],
                                        env=self.environment, cwd=self.root,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def early_check_finished(self):
        deadline = time.monotonic() + 4
        while not (self.root / 'early-checked').exists():
            if self.process.poll() is not None:
                log = self.home / 'logs/launch-123.log'
                self.fail('fixture exited before preparation: ' + (log.read_text() if log.exists() else repr(self.process.communicate())))
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)

    def test_unpatched_wine_is_leased_and_rechecked_before_any_runtime_command(self):
        self.start()
        output = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 0,
                         (output, (self.home / 'logs/launch-123.log').read_text()))
        self.assertTrue((self.root / 'late-checked').exists())
        events = [json.loads(line) for line in (self.root / 'wine-events').read_text().splitlines()]
        self.assertTrue(all(event['lease_held'] and not event['lease_inherited'] for event in events), events)
        self.assertEqual([event['args'] for event in events if event['role'] == 'wineserver'], [['-w'], ['-w']])

    def test_cloud_rules_changing_while_waiting_cannot_start_wine(self):
        import fcntl
        with self.lease.open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            self.start()
            self.early_check_finished()
            self.state.write_text('refuse')
        output = self.process.communicate(timeout=5)
        self.assertNotEqual(self.process.returncode, 0, output)
        self.assertTrue((self.root / 'late-checked').exists())
        self.assertFalse((self.root / 'wine-events').exists())
        self.assertIn('Cloud save rules changed', (self.home / 'logs/wine-123.log').read_text())

    def test_cloud_path_mode_changing_while_waiting_requires_fresh_steam_preparation(self):
        import fcntl
        with self.lease.open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            self.start()
            self.early_check_finished()
            self.state.write_text('native')
        output = self.process.communicate(timeout=5)
        self.assertNotEqual(self.process.returncode, 0, output)
        self.assertFalse((self.root / 'wine-events').exists())
        self.assertIn('paths changed while', (self.home / 'logs/wine-123.log').read_text())

    def test_initial_native_cloud_mode_is_rechecked_even_after_launch_helper_clears_flag(self):
        self.environment['LEASE_EARLY_MODE'] = 'native'
        self.state.write_text('native')
        self.start()
        output = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 0,
                         (output, (self.home / 'logs/launch-123.log').read_text()))
        self.assertTrue((self.root / 'late-checked').exists())


    def test_install_helper_uses_cloud_lease_without_game_identity_or_warming(self):
        marker = self.home / 'runtime/current/licenses/gluon-wine-patches/APPLIED.txt'
        marker.parent.mkdir(parents=True)
        marker.write_text('wine-session-idle\n')
        self.start('runinprefix')
        output = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 0, output)
        events = [json.loads(line) for line in (self.root / 'wine-events').read_text().splitlines()]
        waits = [event for event in events if event['role'] == 'wineserver']
        self.assertEqual([event['args'] for event in waits], [['--wait-idle']])
        self.assertTrue(all(event['lease_held'] and not event['lease_inherited'] for event in waits))
        events = [event for event in events if event['role'] == 'wine']
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0]['role'], 'wine')
        self.assertTrue(events[0]['lease_held'])
        self.assertFalse(events[0]['lease_inherited'])
        self.assertIsNone(events[0]['appid'])
        self.assertIsNone(events[0]['host'])
        self.assertIsNone(events[0]['overlay'])
        self.assertFalse((self.home / 'state/warm-sessions').exists())

    def test_path_conversion_is_leased_and_preserves_only_converted_path_stdout(self):
        target = 'C:\\windows\\path with spaces'
        self.start('getnativepath', target)
        output = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 0, output)
        self.assertEqual(output[0], (target + '\n').encode())
        events = [json.loads(line) for line in (self.root / 'wine-events').read_text().splitlines()]
        self.assertEqual(events[-1]['args'], ['-w'])
        self.assertTrue(events[-1]['lease_held'])
        events = [event for event in events if event['role'] == 'wine']
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0]['args'], ['winepath', '-u', target])
        self.assertTrue(events[0]['lease_held'])
        self.assertFalse(events[0]['lease_inherited'])
        self.assertIsNone(events[0]['appid'])
        self.assertIsNone(events[0]['host'])

    def test_path_converter_cannot_write_while_cloud_preparation_owns_prefix(self):
        import fcntl
        with self.lease.open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            self.start('getnativepath', 'C:\\Windows')
            self.early_check_finished()
            log = self.home / 'logs/launch-123.log'
            deadline = time.monotonic() + 4
            while not log.exists() or 'waiting for exclusive prefix ownership' not in log.read_text():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertFalse((self.root / 'wine-events').exists())
        output = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 0, output)
        self.assertTrue((self.root / 'late-checked').exists())


    def test_path_conversion_keeps_the_owned_runtime_exit_status(self):
        self.environment['LEASE_WINE_STATUS'] = '42'
        self.start('getnativepath', 'C:\\Windows')
        output = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 42, output)
        self.assertEqual(output[0], b'C:\\Windows\n')


if __name__ == '__main__':
    unittest.main()

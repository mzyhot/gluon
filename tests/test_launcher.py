"""Offline process fixtures: every runtime executable is a small Python stub."""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest


STUB = r'''import json, os, pathlib, signal, sys
root = pathlib.Path(os.environ.get('GLUON_LAUNCHER_FIXTURE') or os.environ['LAUNCHER_FIXTURE_ROOT'])
role = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
phase = os.environ.get('GLUON_LAUNCHER_PHASE') or os.environ.get('LAUNCHER_FIXTURE_PHASE', '')

def record(event, **extra):
    item = dict(event=event, pid=os.getpid(), role=role, args=args,
                prefix=os.environ.get('WINEPREFIX'), **extra)
    with (root / 'trace.jsonl').open('a') as stream:
        stream.write(json.dumps(item) + '\n')

try:
    os.fstat(9)
    inherited_lease = True
except OSError:
    inherited_lease = False
record('started', inherited_lease=inherited_lease)

def block():
    def finish(number, frame):
        if phase in ('proton-ignores-term', 'shutdown-timeout') and number == signal.SIGTERM:
            record('ignored', signal=number)
            return
        record('exited', signal=number)
        sys.exit(128 + number)
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, finish)
    (root / 'active.json').write_text(json.dumps(dict(pid=os.getpid(), role=role)))
    record('blocked')
    while True:
        signal.pause()

if role == 'notify':
    sys.exit(0)
elif role == 'wineserver':
    if args == ['--gluon-session=120']:
        if (root / 'warm').exists():
            sys.exit(2)
        (root / 'warm').touch()
    elif args == ['--wait-idle']:
        if not (root / 'warm').exists():
            sys.exit(3)
        if phase == 'warm-final' and (root / 'warm-game').exists() and not (root / 'stopped').exists():
            block()
    elif args == ['--kill-idle']:
        (root / 'warm').unlink(missing_ok=True)
    elif args == ['-k']:
        if phase == 'shutdown-timeout':
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            record('shutdown-blocked')
            while True:
                signal.pause()
        (root / 'stopped').touch()
        try:
            active = json.loads((root / 'active.json').read_text())
            os.kill(active['pid'], signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError):
            pass
    elif args == ['-w'] and not (root / 'stopped').exists():
        if phase == 'final-wait-failure':
            print('err:fixture: final server wait failed', file=sys.stderr)
            sys.exit(42)
        # shutdown-timeout: the server never releases its lock after -k hangs.
        if phase in ('registry-wait', 'prelaunch-wait', 'final-wait', 'shutdown-timeout'):
            block()
elif role == 'proton':
    if phase in ('proton', 'proton-ignores-term', 'shutdown-timeout'):
        block()
elif args[:2] == ['wineboot', '--init']:
    if phase == 'wineboot':
        block()
    prefix = pathlib.Path(os.environ['WINEPREFIX'])
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / 'system.reg').write_text('fixture')
elif args == ['cmd.exe', '/d', '/c', 'exit', '0']:
    record('bootstrap', appid=os.environ.get('SteamAppId'), host=os.environ.get('GLUON_HOST_PID'))
elif args[:1] == ['reg']:
    if phase == 'registry':
        block()
    if phase == 'registry-failure':
        sys.exit(42)
else:
    record('game')
    (root / 'warm-game').touch()
    if phase == 'game':
        block()
'''


class LauncherLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gluon-launcher-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "Gluon Data"
        self.runtime = self.root / "Runtime"
        self.compat = self.root / "Game Prefix"
        self.game = self.root / "Game Folder/game.exe"
        self.game.parent.mkdir()
        self.game.write_bytes(b"fixture; never executed")
        self.process = None
        self.addCleanup(self.stop_fixture_processes)

    def prepare(self, phase, bridge=False, proton=False, warm=False):
        self.prefix = self.compat / ("pfx" if proton else "pfx-wine")
        if phase != "wineboot":
            self.prefix.mkdir(parents=True)
            (self.prefix / "system.reg").write_text("fixture")
        if bridge:
            dlls = self.runtime / "lib/wine/x86_64-windows"
            dlls.mkdir(parents=True)
            for name in ("steam.exe", "lsteamclient.dll"):
                (dlls / name).write_bytes(b"fixture")
            for name in ("system32", "syswow64"):
                (self.prefix / "drive_c/windows" / name).mkdir(parents=True)
        bin_dir = self.runtime / ("files/bin" if proton else "bin")
        bin_dir.mkdir(parents=True)
        for path in (bin_dir / "wine", bin_dir / "wineserver"):
            path.write_text("#!%s\n%s" % (sys.executable, STUB))
            path.chmod(0o755)
        if proton:
            path = self.runtime / "proton"
            path.write_text("#!%s\n%s" % (sys.executable, STUB))
            path.chmod(0o755)
        if warm:
            marker = self.runtime / "licenses/gluon-wine-patches/APPLIED.txt"
            marker.parent.mkdir(parents=True)
            marker.write_text("wine-session-idle\n")
            for name in (".gluon-wine-patches", ".gluon-runtime-build-key"):
                (self.runtime / name).write_text("a" * 64)
        environment = dict(os.environ)
        for name in list(environment):
            if name.startswith(("GLUON_", "STEAM_", "Steam", "WINE", "PROTON_")):
                environment.pop(name)
        environment.update(GLUON_HOME=str(self.home), GLUON_RUNTIME=str(self.runtime),
                           GLUON_LAUNCHER_FIXTURE=str(self.root), GLUON_LAUNCHER_PHASE=phase,
                           LAUNCHER_FIXTURE_ROOT=str(self.root), LAUNCHER_FIXTURE_PHASE=phase,
                           STEAM_COMPAT_DATA_PATH=str(self.compat), SteamAppId="12345")
        self.environment = environment

    def events(self):
        path = self.root / "trace.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def start(self, verb="run"):
        source = Path(__file__).resolve().parents[1] / "tool/gluon"
        notifier = self.root / "notify"
        notifier.write_text("#!%s\n%s" % (sys.executable, STUB))
        notifier.chmod(0o755)
        package = self.home / "lib/gluon"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("")
        (package / "session.py").write_text((source.parents[1] / "gluon/session.py").read_text())
        launcher = self.root / "launcher"
        # Preserve launcher behavior while recording notifications without using macOS UI.
        launcher_source = source.read_text()
        if getattr(self, 'inject_early_stop', False):
            launcher_source = launcher_source.replace('trap request_stop TERM INT HUP',
                                                      'trap request_stop TERM INT HUP\nkill -TERM $$')
        launcher.write_text(launcher_source.replace(
            "/usr/bin/arch -arm64 /usr/bin/python3", shlex.quote(sys.executable)
        ).replace("/usr/bin/osascript", shlex.quote(str(notifier))))
        launcher.chmod(0o755)
        self.process = subprocess.Popen([str(launcher), verb, str(self.game)],
                                        env=self.environment, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)

    def wait_for_blocked(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            blocked = [event for event in self.events() if event["event"] == "blocked"]
            if blocked:
                return blocked[-1]
            if self.process.poll() is not None:
                self.fail("launcher exited before fixture blocked: %r" % (self.process.communicate(),))
            time.sleep(0.01)
        self.fail("launcher did not reach the requested phase: %r" % self.events())

    def stop_fixture_processes(self):
        # Only recorded stub children belong to this fixture; never inspect or stop Wine.
        events = self.events()
        exited = {event["pid"] for event in events if event["event"] == "exited"}
        for event in events:
            if event["event"] == "blocked" and event["pid"] not in exited:
                try:
                    os.kill(event["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.communicate(timeout=2)

    def assert_cancelled(self, phase, bridge=False, proton=False, verb="run"):
        self.prepare(phase, bridge=bridge, proton=proton)
        self.start(verb)
        blocked = self.wait_for_blocked()
        self.process.send_signal(signal.SIGTERM)
        try:
            output = self.process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            self.fail("Stop did not interrupt %s" % phase)
        self.assertEqual(self.process.returncode, 143, output)
        events = self.events()
        shutdown = [event for event in events if event["role"] == "wineserver"
                    and event["event"] == "started" and event["args"] == ["-k"]]
        self.assertEqual(len(shutdown), 1, events)
        self.assertEqual(shutdown[0]["prefix"], str(self.prefix))
        after_kill = events[events.index(shutdown[0]) + 1:]
        self.assertTrue(any(event["role"] == "wineserver" and event["args"] == ["-w"]
                            and event["prefix"] == str(self.prefix) for event in after_kill), events)
        if phase not in ("game", "final-wait"):
            self.assertFalse(any(event["event"] == "game" for event in events), events)
        if phase != "proton-ignores-term":
            self.assertTrue(any(event["event"] == "exited" and event["pid"] == blocked["pid"]
                                for event in events), events)
        with self.assertRaises(ProcessLookupError, msg="launcher left its current helper alive"):
            os.kill(blocked["pid"], 0)

    def test_term_interrupts_wineboot(self):
        self.assert_cancelled("wineboot")

    def test_term_interrupts_registry_server_wait(self):
        self.assert_cancelled("registry-wait", bridge=True)
        self.assertFalse((self.compat / ".gluon-steam-bridge").exists())

    def test_term_interrupts_registry_command(self):
        self.assert_cancelled("registry", bridge=True)
        self.assertFalse((self.compat / ".gluon-steam-bridge").exists())

    def test_term_interrupts_previous_session_wait(self):
        self.assert_cancelled("prelaunch-wait", verb="waitforexitandrun")

    def test_term_stops_active_game(self):
        self.assert_cancelled("game")

    def test_term_interrupts_final_server_wait(self):
        self.assert_cancelled("final-wait")

    def test_term_stops_proton_wrapper(self):
        self.assert_cancelled("proton", proton=True)

    def test_stop_reaps_owned_proton_wrapper_that_ignores_term(self):
        self.assert_cancelled("proton-ignores-term", proton=True)

    def test_failed_final_server_wait_reports_launch_failure(self):
        self.prepare("final-wait-failure")
        self.start()
        output = self.process.communicate(timeout=4)
        self.assertEqual(self.process.returncode, 42, output)
        events = self.events()
        self.assertTrue(any(event["event"] == "game" for event in events), events)
        notices = [event for event in events if event["role"] == "notify"]
        self.assertEqual(len(notices), 1, events)
        self.assertIn("final server wait failed", notices[0]["args"][2])
        self.assertIn(str(self.home / "logs/wine-12345.log"), notices[0]["args"][2])

    def test_shutdown_timeout_reports_failure_and_reaps_owned_wrapper(self):
        self.prepare("shutdown-timeout", proton=True)
        self.start()
        blocked = self.wait_for_blocked()
        self.process.send_signal(signal.SIGTERM)
        output = self.process.communicate(timeout=14)
        self.assertEqual(self.process.returncode, 1, output)
        with self.assertRaises(ProcessLookupError):
            os.kill(blocked["pid"], 0)
        shutdown = [event for event in self.events() if event["role"] == "wineserver"
                    and event["event"] == "started"]
        self.assertEqual([event["args"] for event in shutdown], [["-k"], ["-w"]])
        self.assertTrue(all(event["prefix"] == str(self.prefix) for event in shutdown))
        self.assertIn("wineserver -k timed out after 5s", (self.home / "logs/wine-12345.log").read_text())
        self.assertIn("wineserver -w timed out after 5s", (self.home / "logs/wine-12345.log").read_text())
        self.assertIn("prefix shutdown could not be confirmed", (self.home / "logs/launch-12345.log").read_text())

    def test_registry_failure_does_not_mark_bridge_initialized_or_start_game(self):
        self.prepare("registry-failure", bridge=True)
        self.start()
        output = self.process.communicate(timeout=4)
        self.assertNotEqual(self.process.returncode, 0, output)
        self.assertFalse((self.compat / ".gluon-steam-bridge").exists())
        self.assertFalse(any(event["event"] == "game" for event in self.events()))


    def test_warm_repeat_retains_server_without_inheriting_shell_lease(self):
        self.prepare("warm-success", warm=True)
        self.start("waitforexitandrun")
        first = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 0, first)
        first_count = len(self.events())
        self.start("waitforexitandrun")
        second = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 0, second)
        events = self.events()
        self.assertFalse(any(event.get("inherited_lease") for event in events), events)
        bootstraps = [event for event in events if event["event"] == "bootstrap"]
        self.assertEqual(len(bootstraps), 1, events)
        self.assertTrue(all(event["appid"] is None and event["host"] is None for event in bootstraps))
        repeat = events[first_count:]
        self.assertFalse(any(event["args"] == ["-w"] for event in repeat), repeat)
        self.assertTrue(any(event["args"] == ["--wait-idle"] for event in repeat), repeat)
        self.assertTrue(any(event["event"] == "game" for event in repeat), repeat)

    def test_stop_interrupts_warm_user_idle_wait_and_confirms_full_shutdown(self):
        self.prepare("warm-final", warm=True)
        self.start()
        blocked = self.wait_for_blocked()
        self.assertEqual(blocked["args"], ["--wait-idle"])
        self.process.terminate()
        output = self.process.communicate(timeout=5)
        self.assertEqual(self.process.returncode, 143, output)
        events = self.events()
        self.assertEqual(len([event for event in events if event["args"] == ["-k"]]), 1)
        self.assertTrue(any(event["args"] == ["-w"] for event in events[events.index(blocked):]))
        self.assertFalse(any(event.get("inherited_lease") for event in events), events)

    def test_stop_during_lease_wait_does_not_terminate_another_game(self):
        import fcntl
        self.prepare("warm-success", warm=True)
        lease_path = self.compat / ".gluon-session.lock"
        with lease_path.open("a") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            self.start()
            deadline = time.monotonic() + 4
            launch_log = self.home / "logs/launch-12345.log"
            while not launch_log.exists() or "waiting for exclusive prefix ownership" not in launch_log.read_text():
                self.assertLess(time.monotonic(), deadline)
                self.assertIsNone(self.process.poll(), self.process.communicate() if self.process.poll() is not None else "")
                time.sleep(0.01)
            self.process.terminate()
            output = self.process.communicate(timeout=3)
        self.assertEqual(self.process.returncode, 143, output)
        self.assertFalse(any(event["role"] == "wineserver" for event in self.events()), self.events())
        self.assertFalse(any(event["event"] == "game" for event in self.events()), self.events())


    def test_stop_before_lease_open_does_not_kill_another_prefix_owner(self):
        self.prepare("warm-success", warm=True)
        self.inject_early_stop = True
        self.start()
        output = self.process.communicate(timeout=3)
        self.assertEqual(self.process.returncode, 143, output)
        self.assertFalse(any(event["role"] == "wineserver" for event in self.events()), self.events())
        self.assertFalse(any(event["event"] == "game" for event in self.events()), self.events())


if __name__ == "__main__":
    unittest.main()

"""Offline launcher fixtures; every runtime executable is a Python recorder."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gluon-launcher-startup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "Gluon"
        self.home.mkdir()
        self.trace = self.root / "trace.jsonl"
        self.game = self.root / "Game With Spaces/game.exe"
        self.game.parent.mkdir()
        self.game.write_bytes(b"fixture, never executed")
        prefix = self.home / "compatdata/123/pfx-wine"
        prefix.mkdir(parents=True)
        (prefix / "system.reg").write_text("fixture")

        self.stub = "#!%s\n" % sys.executable + """import json, os, pathlib, sys
event = {'role': pathlib.Path(sys.argv[0]).name,
         'args': sys.argv[1:], 'dllpath': os.environ.get('WINEDLLPATH'),
         'focus': os.environ.get('GLUON_FOCUS'), 'gl': os.environ.get('GLUON_GL_COMPAT_AS_CORE'),
         'dyld': os.environ.get('DYLD_INSERT_LIBRARIES'), 'game_cwd': os.environ.get('GLUON_GAME_CWD'),
         'prefix': os.environ.get('WINEPREFIX'), 'app': os.environ.get('SteamAppId'),
         'host': os.environ.get('GLUON_HOST_PID')}
print('fixture output from', event['role'])
if event['role'] == 'notify':
    event['script'] = sys.stdin.read()
with open(os.environ['LAUNCHER_STARTUP_TRACE'], 'a') as out:
    out.write(json.dumps(event) + '\\n')
"""
        self.notifier = self.root / "notify"
        self.executable(self.notifier)
        # LaunchServices stand-in: it starts the bundle's executable without the
        # caller's environment and waits, like open -n -W -a.
        self.opener = self.root / "open"
        self.opener.write_text("#!%s\n" % sys.executable + """import json, os, plistlib, subprocess, sys
args = sys.argv[1:]
with open(os.environ['LAUNCHER_STARTUP_TRACE'], 'a') as out:
    out.write(json.dumps({'role': 'open', 'args': args, 'dllpath': None}) + '\\n')
if os.environ.get('FIXTURE_OPEN_FAILS') or args[:3] != ['-n', '-W', '-a']:
    sys.exit(1)
bundle, rest, env, output = args[3], args[4:], {'PATH': '/usr/bin:/bin', 'HOME': os.environ.get('HOME', '')}, None
while rest and rest[0] != '--args':
    option, value, rest = rest[0], rest[1], rest[2:]
    if option == '--env':
        key, _, item = value.partition('=')
        env[key] = item
    elif option == '--stdout':
        output = value
    elif option == '--stderr':
        errors = value
# The real environment variables reach the game only through --env.
env['LAUNCHER_STARTUP_TRACE'] = os.environ['LAUNCHER_STARTUP_TRACE']
executable = plistlib.load(open(os.path.join(bundle, 'Contents/Info.plist'), 'rb'))['CFBundleExecutable']
with open(output, 'w') as out, open(errors, 'w') as err:
    sys.exit(subprocess.call([os.path.join(bundle, 'Contents/MacOS', executable), *rest[1:]],
                             env=env, cwd='/', stdout=out, stderr=err))
""")
        self.opener.chmod(0o755)
        self.launcher = self.root / "bin/gluon"
        self.launcher.parent.mkdir()
        source = (ROOT / "tool/gluon").read_text()
        self.launcher.write_text(source.replace(
            "/usr/bin/arch -arm64 /usr/bin/python3", shlex.quote(sys.executable)
        ).replace("/usr/bin/osascript", shlex.quote(str(self.notifier))
        ).replace("/usr/bin/open", shlex.quote(str(self.opener))))
        self.launcher.chmod(0o755)

        # Run the real bootstrap module from an isolated installation. It only
        # creates a missing default and does not load any runtime executable.
        package = self.home / "lib/gluon"
        package.mkdir(parents=True)
        for name in ("__init__.py", "runtime.py", "steam.py"):
            shutil.copy2(ROOT / "gluon" / name, package / name)

        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("GLUON_", "STEAM_", "Steam", "WINE", "PROTON_", "DYLD_"))
        }
        self.environment.update(HOME=str(self.root), GLUON_HOME=str(self.home),
                                SteamAppId="123", LAUNCHER_STARTUP_TRACE=str(self.trace),
                                PYTHONDONTWRITEBYTECODE="1")

    def executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.stub)
        path.chmod(0o755)

    def runtime(self, modern=True, proton=False, name="fixture-runtime"):
        runtime = self.home / "runtime" / name
        bin_dir = runtime / ("files/bin" if proton else "bin")
        for name in ("wine", "wineserver"):
            self.executable(bin_dir / name)
        if proton:
            self.executable(runtime / "proton")
        if modern:
            native = runtime / "lib/wine/x86_64-unix"
            self.executable(native / "wine")
            (native / "ntdll.so").write_bytes(b"fixture, never loaded")
        (runtime / "licenses").mkdir()
        return runtime

    def run_launcher(self, *arguments, **environment):
        result = subprocess.run([str(self.launcher), "run", str(self.game), *arguments],
                                env=dict(self.environment, **environment),
                                capture_output=True, text=True, timeout=5)
        events = ([json.loads(line) for line in self.trace.read_text().splitlines()]
                  if self.trace.exists() else [])
        return result, events

    def assert_launch(self, events, dllpath, role="wine"):
        games = [event for event in events if event["role"] == role]
        self.assertEqual(len(games), 1, events)
        self.assertEqual(games[0]["dllpath"], dllpath)
        self.assertIn(str(self.game), games[0]["args"])
        self.assertFalse(any(event["role"] == "notify" for event in events), events)

    def pending(self, runtime):
        (runtime.parent / "pending.json").write_text(json.dumps(
            {"schema": 1, "name": runtime.name, "version": "0.1.0"}))

    def test_modern_wine_enables_runtime_dll_path_for_game_naming(self):
        runtime = self.runtime()
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_launch(events, str(runtime / "lib/wine"))

    def test_existing_custom_dll_paths_are_preserved(self):
        runtime = self.runtime()
        custom = "/custom/one:/custom/two"
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime), WINEDLLPATH=custom)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_launch(events, custom)

    def test_older_wine_layout_leaves_dll_path_unset(self):
        runtime = self.runtime(modern=False)
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_launch(events, None)

    def test_proton_keeps_its_own_dll_path_configuration(self):
        runtime = self.runtime(proton=True)
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_launch(events, None, role="proton")

    def test_first_launch_bootstraps_valid_pending_runtime(self):
        runtime = self.runtime()
        self.pending(runtime)
        current = runtime.parent / "current"
        result, events = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(current), runtime.name)
        self.assert_launch(events, str(current / "lib/wine"))
        self.assertIn("selected packaged runtime " + runtime.name,
                      (self.home / "logs/launch-123.log").read_text())
        backup = next((self.home / "backups").glob("runtime-*/selection.json"))
        self.assertIsNone(json.loads(backup.read_text())["previous"])

    def install_bundle_helpers(self, runtime):
        for name in ("appbundle.py", "vdf.py"):
            shutil.copy2(ROOT / "gluon" / name, self.home / "lib/gluon" / name)
        notices = runtime / "licenses/gluon-wine-patches"
        notices.mkdir(parents=True)
        (notices / "APPLIED.txt").write_text("wine-macos-app-bundle\nwine-macos-overlay-scope\n")

    def test_game_starts_from_its_app_bundle_through_launchservices(self):
        runtime = self.runtime()
        self.install_bundle_helpers(runtime)
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        opens = [event for event in events if event["role"] == "open"]
        self.assertEqual(len(opens), 1, events)
        bundle = Path(opens[0]["args"][3])
        self.assertEqual(bundle.parent, self.home / "apps.noindex/123")
        self.assertEqual(opens[0]["args"][opens[0]["args"].index("--args") + 1:], [str(self.game)])
        # The bundle's loader ran with this session's settings, passed through open.
        self.assert_launch(events, str(runtime / "lib/wine"))
        game = [event for event in events if event["role"] == "wine"][-1]
        self.assertEqual(game["game_cwd"], str(self.game.parent))
        self.assertEqual(game["app"], "123")
        self.assertTrue(game["prefix"].endswith("pfx-wine"))
        self.assertIsNone(game["host"], "a launch without the native host must not name one")
        wine_log = (self.home / "logs/wine-123.log").read_text()
        self.assertIn("fixture output from wine", wine_log)
        self.assertEqual(sorted(p.name for p in (self.home / "logs").glob("wine-123-session.*")), [])

    def test_runtime_without_bundle_support_starts_wine_directly(self):
        runtime = self.runtime()
        self.install_bundle_helpers(runtime)
        (runtime / "licenses/gluon-wine-patches/APPLIED.txt").write_text("wine-macos-display-name\n")
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(event["role"] == "open" for event in events), events)
        self.assert_launch(events, str(runtime / "lib/wine"))

    def test_bundle_that_never_starts_falls_back_to_wine(self):
        runtime = self.runtime()
        self.install_bundle_helpers(runtime)
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime), FIXTURE_OPEN_FAILS="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([event for event in events if event["role"] == "open"]), 1, events)
        self.assert_launch(events, str(runtime / "lib/wine"))
        self.assertIn("the app bundle did not start", (self.home / "logs/launch-123.log").read_text())

    def test_app_bundle_can_be_turned_off(self):
        runtime = self.runtime()
        self.install_bundle_helpers(runtime)
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime), GLUON_APP_BUNDLE="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(event["role"] == "open" for event in events), events)
        self.assert_launch(events, str(runtime / "lib/wine"))

    def test_steam_launch_options_set_switches_and_leave_game_arguments(self):
        runtime = self.runtime()
        self.install_bundle_helpers(runtime)
        result, events = self.run_launcher(
            "--windowed", "%command%", "GLUON_APP_BUNDLE=0", "GLUON_FOCUS=0",
            "DYLD_INSERT_LIBRARIES=/not/a/setting.dylib", GLUON_RUNTIME=str(runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(event["role"] == "open" for event in events), events)
        game = [event for event in events if event["role"] == "wine"][-1]
        self.assertEqual(game["args"], [str(self.game), "--windowed", "DYLD_INSERT_LIBRARIES=/not/a/setting.dylib"])
        self.assertEqual(game["focus"], "0")
        self.assertIsNone(game["dyld"])
        log = (self.home / "logs/launch-123.log").read_text()
        self.assertIn("launch option GLUON_APP_BUNDLE=0", log)
        self.assertNotIn("launch option DYLD_", log)

    def test_host_pid_comes_only_from_the_launcher_host(self):
        runtime = self.runtime()
        self.install_bundle_helpers(runtime)
        result, events = self.run_launcher("GLUON_HOST_PID=1", GLUON_RUNTIME=str(runtime),
                                           GLUON_LAUNCHER_HOST="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        game = [event for event in events if event["role"] == "wine"][-1]
        # The fixture runs the launcher directly, so its parent is this test.
        self.assertEqual(game["host"], str(os.getpid()))

    def test_settings_for_every_game_and_compatibility_mapping_default(self):
        runtime = self.runtime()
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime))
        self.assertEqual([event["gl"] for event in events if event["role"] == "wine"][-1], "0")
        (self.home / "settings.env").write_text("# every game\nGLUON_GL_COMPAT_AS_CORE=0\nGLUON_FOCUS=0\n")
        (self.home / "games").mkdir()
        (self.home / "games/123.env").write_text("GLUON_FOCUS=1\n")
        self.trace.unlink()
        result, events = self.run_launcher(GLUON_RUNTIME=str(runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        game = [event for event in events if event["role"] == "wine"][-1]
        self.assertEqual(game["gl"], "0")
        self.assertEqual(game["focus"], "1", "a game's own settings must win over settings for every game")

    def test_dry_run_does_not_bootstrap_or_start_pending_runtime(self):
        runtime = self.runtime()
        self.pending(runtime)
        result, events = self.run_launcher(GLUON_DRY_RUN="1")
        self.assertEqual(result.returncode, 1)
        self.assertFalse((runtime.parent / "current").is_symlink())
        self.assertFalse((self.home / "backups").exists())
        self.assertTrue(events)
        self.assertTrue(all(event["role"] == "notify" for event in events), events)

    def test_missing_runtime_notifies_with_error_and_log_path(self):
        result, events = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no runtime found", result.stderr)
        self.assertEqual(len(events), 1, events)
        notice = events[0]
        self.assertEqual(notice["role"], "notify")
        self.assertEqual(notice["args"][1], "Could not start the game")
        self.assertIn("no runtime found", notice["args"][2])
        self.assertIn(str(self.home / "logs/launch-123.log"), notice["args"][2])
        self.assertIn("display notification", notice["script"])


if __name__ == "__main__":
    unittest.main()

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
         'args': sys.argv[1:], 'dllpath': os.environ.get('WINEDLLPATH')}
if event['role'] == 'notify':
    event['script'] = sys.stdin.read()
with open(os.environ['LAUNCHER_STARTUP_TRACE'], 'a') as out:
    out.write(json.dumps(event) + '\\n')
"""
        self.notifier = self.root / "notify"
        self.executable(self.notifier)
        self.launcher = self.root / "bin/gluon"
        self.launcher.parent.mkdir()
        source = (ROOT / "tool/gluon").read_text()
        self.launcher.write_text(source.replace(
            "/usr/bin/arch -arm64 /usr/bin/python3", shlex.quote(sys.executable)
        ).replace("/usr/bin/osascript", shlex.quote(str(self.notifier))))
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

    def run_launcher(self, **environment):
        result = subprocess.run([str(self.launcher), "run", str(self.game)],
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

"""Steam Play plus a saved Launch Option must produce exactly one game session."""
import os
import shlex
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherWrappingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / "Gluon Home"
        self.launcher = self.home / "bin/gluon"
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text((ROOT / "tool/gluon").read_text().replace(
            "/usr/bin/osascript", "/usr/bin/true").replace(
            "/usr/bin/arch -arm64 /usr/bin/python3", shlex.quote(sys.executable)))
        self.launcher.chmod(0o755)
        package = self.home / "lib/gluon"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "session.py").write_text((ROOT / "gluon/session.py").read_text())
        runtime = self.home / "runtime/current/bin"
        runtime.mkdir(parents=True)
        for name, source in {
            "wine": 'printf "%s\\0" "$@" >> "$FIXTURE_ROOT/argv"\n',
            "wineserver": 'printf "%s\\n" "$*" >> "$FIXTURE_ROOT/waits"\n',
        }.items():
            path = runtime / name
            path.write_text("#!/bin/sh\n" + source)
            path.chmod(0o755)
        host = self.home / "bin/gluon-launcher-host"
        host.write_text('#!/bin/sh\nprintf "host\\n" >> "$FIXTURE_ROOT/hosts"\n'
                        'export GLUON_LAUNCHER_HOST=1\nexec "$@"\n')
        host.chmod(0o755)
        prefix = self.home / "compatdata/123/pfx-wine"
        prefix.mkdir(parents=True)
        (prefix / "system.reg").touch()
        self.game = self.root / "Game's Folder/game.exe"
        self.game.parent.mkdir()
        self.game.touch()
        self.steam_root = self.root / "Steam"
        self.shim = (self.steam_root /
                     "Steam.AppBundle/Steam/Contents/MacOS/compatibilitytools.d/gluon/gluon")
        self.shim.parent.mkdir(parents=True)
        self.shim.touch()
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(("GLUON_", "STEAM_", "Steam", "WINE", "PROTON_", "DYLD_"))}
        self.environment.update(GLUON_HOME=str(self.home), STEAM_ROOT=str(self.steam_root),
                                SteamAppId="123", FIXTURE_ROOT=str(self.root),
                                GLUON_APP_BUNDLE="0", GLUON_FOCUS="0", GLUON_CAFFEINATE="0")

    def run_command(self, *arguments):
        return subprocess.run([str(self.launcher), *map(str, arguments)], env=self.environment,
                              capture_output=True, text=True, timeout=3)

    def assert_session(self, result, expected, waits):
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / "argv").read_bytes().split(b"\0")[:-1]
        self.assertEqual(args, [str(arg).encode() for arg in expected])
        self.assertEqual((self.root / "hosts").read_text(), "host\n")
        self.assertEqual((self.root / "waits").read_text().splitlines(), ["-w"] * waits)

    def test_tool_wraps_saved_launch_option(self):
        result = self.run_command("waitforexitandrun", self.launcher, "run", self.game,
                                  "with spaces", "", "literal;$value", r"C:\game\settings")
        self.assert_session(result, [self.game, "with spaces", "", "literal;$value", r"C:\game\settings"], 2)

    def test_saved_option_wraps_tool_and_keeps_inner_wait(self):
        result = self.run_command("run", str(self.shim.parent) + "/./gluon", "waitforexitandrun", self.game,
                                  "GLUON_FOCUS=0", "--windowed")
        self.assert_session(result, [self.game, "--windowed"], 2)

    def test_other_program_named_gluon_and_later_arguments_are_untouched(self):
        other = self.game.parent / "gluon"
        other.touch()
        result = self.run_command("run", other, "run", self.launcher, "run")
        self.assert_session(result, [other, "run", self.launcher, "run"], 1)

    def test_incomplete_wrapper_fails_before_starting_runtime(self):
        result = self.run_command("run", self.launcher, "run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete nested Gluon", (self.home / "logs/launch-123.log").read_text())
        self.assertFalse((self.root / "argv").exists())

    def test_native_wrapper_exposes_one_game_host_after_peeling(self):
        self.environment['STEAM_COMPAT_APP_ID'] = '123'
        native = self.steam_root / 'compatibilitytools.d/gluon/run'
        native.parent.mkdir(parents=True)
        native.touch()
        result = self.run_command('run', str(native.parent) + '/./run', 'waitforexitandrun', self.game)
        self.assert_session(result, [self.game], 2)

    def test_unknown_nested_verb_fails_before_starting_runtime(self):
        result = self.run_command("run", self.launcher, "unsupported", self.game)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported nested Gluon verb", (self.home / "logs/launch-123.log").read_text())
        self.assertFalse((self.root / "argv").exists())


if __name__ == "__main__":
    unittest.main()

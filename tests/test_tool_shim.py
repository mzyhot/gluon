import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "tool/gluon-shim"


class ToolShimTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gluon-tool-shim-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "Gluon with spaces"
        self.launcher = self.home / "bin/gluon"
        self.env = dict(os.environ, HOME=str(self.root), GLUON_HOME=str(self.home))

    def write_launcher(self, mode=0o755):
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text(
            "#!%s\n" % sys.executable +
            "import json, os, sys\n"
            "print(json.dumps({'args': sys.argv, 'env': dict(os.environ), 'pid': os.getpid()}))\n"
            "sys.exit(23)\n"
        )
        self.launcher.chmod(mode)

    def test_exec_preserves_arguments_environment_exit_status_and_pid(self):
        self.write_launcher()
        args = ["waitforexitandrun", "game folder/game.exe", "", "--name=a b", "quote'\"", "$literal"]
        forwarded = {
            "SteamAppId": "123",
            "STEAM_COMPAT_DATA_PATH": str(self.root / "library/compatdata/123"),
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(self.root / "Steam"),
            "STEAM_COMPAT_TOOL_PATHS": "tool:runtime",
            "STEAM_DYLD_INSERT_LIBRARIES": "overlay:test",
            "GLUON_OVERLAY": "0",
            "CUSTOM_EMPTY": "",
            "CUSTOM_VALUE": "value with spaces\nand a newline",
            "gluon_home": "inherited lowercase variable",
            "launcher": "inherited lowercase variable",
        }
        self.env.update(forwarded)
        proc = subprocess.Popen([str(SHIM), *args], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 23, stderr)
        observed = json.loads(stdout)
        self.assertEqual(observed["args"], [str(self.launcher), *args])
        self.assertEqual(observed["pid"], proc.pid)
        for name, value in dict(forwarded, GLUON_HOME=str(self.home)).items():
            self.assertEqual(observed["env"].get(name), value, name)
        self.assertEqual(stderr, "")

    def test_default_home_is_used_without_adding_an_environment_variable(self):
        self.env.pop("GLUON_HOME")
        self.home = self.root / "Library/Application Support/Gluon"
        self.launcher = self.home / "bin/gluon"
        self.write_launcher()
        result = subprocess.run([str(SHIM), "run", "game.exe"], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 23, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["args"][0], str(self.launcher))
        self.assertNotIn("GLUON_HOME", observed["env"])

    def assert_unavailable(self):
        result = subprocess.run([str(SHIM), "run", "game.exe"], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("installed launcher is missing or not executable", result.stderr)
        self.assertIn(str(self.launcher), result.stderr)
        self.assertIn(str(self.launcher), (self.home / "logs/tool-shim.log").read_text())

    def test_missing_installed_launcher_logs_and_fails(self):
        self.assert_unavailable()

    def test_non_executable_installed_launcher_logs_and_fails(self):
        self.write_launcher(mode=0o644)
        self.assert_unavailable()

    def test_directory_is_not_accepted_as_the_installed_launcher(self):
        self.launcher.mkdir(parents=True)
        self.assert_unavailable()

    def test_log_failure_still_reports_to_stderr_and_fails(self):
        self.home.mkdir()
        (self.home / "logs").write_text("not a directory")
        result = subprocess.run([str(SHIM), "run", "game.exe"], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed launcher is missing or not executable", result.stderr)
        self.assertIn("could not write the tool shim log", result.stderr)

    def test_registration_installs_the_shim_and_manifests_in_a_fixture(self):
        steam = self.root / "Steam fixture"
        (steam / "Steam.AppBundle").mkdir(parents=True)
        result = subprocess.run([str(ROOT / "scripts/install-tool.sh")],
                                env=dict(self.env, STEAM_ROOT=str(steam)),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        tool = steam / "Steam.AppBundle/Steam/Contents/MacOS/compatibilitytools.d/gluon"
        self.assertEqual((tool / "gluon").read_bytes(), SHIM.read_bytes())
        self.assertTrue(os.access(tool / "gluon", os.X_OK))
        for name in ("compatibilitytool.vdf", "toolmanifest.vdf"):
            self.assertEqual((tool / name).read_bytes(), (ROOT / "tool" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()

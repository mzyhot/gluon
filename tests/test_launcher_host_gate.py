"""Check the shell-to-host PID handoff using a harmless Python recorder."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LauncherHostGateTests(unittest.TestCase):
    def test_host_replaces_tracked_shell_and_receives_original_arguments(self):
        with tempfile.TemporaryDirectory(prefix="gluon-host-gate-") as directory:
            root = Path(directory)
            launch_dir = root / "Installed Bin With Spaces"
            launch_dir.mkdir()
            launcher = launch_dir / "gluon"
            source = Path(__file__).resolve().parents[1] / "tool/gluon"
            launcher.write_bytes(source.read_bytes())
            launcher.chmod(0o755)
            host = launch_dir / "gluon-launcher-host"
            trace = root / "handoff.json"
            host.write_text("#!%s\n" % sys.executable +
                            "import json, os, pathlib, sys\n"
                            "pathlib.Path(os.environ['GLUON_GATE_TRACE']).write_text(json.dumps(\n"
                            "    dict(pid=os.getpid(), arguments=sys.argv[1:],\n"
                            "         insertion=os.environ.get('DYLD_INSERT_LIBRARIES'))))\n"
                            "sys.exit(23)\n")
            host.chmod(0o755)
            environment = dict(os.environ)
            for key in list(environment):
                if key.startswith(("GLUON_", "Steam", "STEAM_")):
                    environment.pop(key)
            environment.update(GLUON_HOME=str(root / "Gluon Data"),
                               GLUON_GATE_TRACE=str(trace), SteamAppId="12345")
            arguments = ["run", str(root / "Game Name.exe"), "--space=value with spaces", ""]
            process = subprocess.Popen([str(launcher)] + arguments, env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output = process.communicate(timeout=4)
            self.assertEqual(process.returncode, 23, output)
            recorded = json.loads(trace.read_text())
            self.assertEqual(recorded["pid"], process.pid)
            self.assertEqual(recorded["arguments"], [str(launcher)] + arguments)
            self.assertIsNone(recorded["insertion"])


if __name__ == "__main__":
    unittest.main()

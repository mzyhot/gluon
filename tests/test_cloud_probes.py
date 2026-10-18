"""Exercise CI probe scheduling without starting Wine or native programs."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "cloud_probes", Path(__file__).resolve().parents[1] / "packaging/run-cloud-probes.py")
cloud_probes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloud_probes)


class CloudProbeTests(unittest.TestCase):
    def exercise(self, idle_code=0, timeout_vulkan=False):
        with tempfile.TemporaryDirectory(prefix="gluon-cloud-probe-fixture-") as directory:
            root = Path(directory)
            runtime, probes = root / "runtime", root / "probes"
            runtime.mkdir()
            (runtime / ".gluon-wine-patches").write_text("fixture")
            commands = []

            def start(command, **kwargs):
                commands.append(command)
                output = kwargs["stdout"]
                name = Path(output.name).stem
                code = 0
                if name in ("native-opengl", "native-vulkan-device"):
                    output.write("GLUON_PROBE_UNAVAILABLE: fixture has no GPU\n")
                    output.flush()
                    code = 77
                documents = probes / 'cloud-steam/steamapps/compatdata/123/pfx/drive_c/users/steamuser/Documents'
                if name == 'native-cloud-read':
                    output.write('Steam-written-save\n')
                    output.flush()
                if name == 'native-cloud-write':
                    (documents / 'cloud-fixture.txt').write_text('Wine-written-save\n')
                if name == "wine-prefix-idle":
                    code = idle_code
                process = mock.Mock(pid=123456)
                if name == "vulkan-loader-x86_64" and timeout_vulkan:
                    process.wait.side_effect = [subprocess.TimeoutExpired(command, 30), 0]
                else:
                    process.wait.return_value = code
                return process

            def support(command, **kwargs):
                if 'gluon.cloud' in command and '--automatic' in command:
                    compat = probes / 'cloud-steam/steamapps/compatdata/123'
                    (compat / 'pfx-wine/drive_c/users/steamuser/Documents').mkdir(parents=True)
                    (compat / 'pfx').symlink_to('pfx-wine')

            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True), \
                    mock.patch.object(cloud_probes.sys, "platform", "darwin"), \
                    mock.patch.object(cloud_probes.sys, "argv", ["probes", str(runtime), str(probes)]), \
                    mock.patch.object(cloud_probes.subprocess, "Popen", side_effect=start), \
                    mock.patch.object(cloud_probes.subprocess, "run", side_effect=support) as sample, \
                    mock.patch.object(cloud_probes.os, "killpg") as kill:
                code = cloud_probes.main()
            results = json.loads((probes / "results.json").read_text())
            return code, commands, results, sample, kill

    def test_prefix_finishes_before_any_dll_probe(self):
        code, commands, results, _, _ = self.exercise()
        self.assertEqual(code, 0)
        names = [item["name"] for item in results]
        self.assertLess(names.index("wine-prefix-idle"), names.index("runtime-dependencies-x86_64"))
        self.assertEqual(Path(commands[names.index("wine-prefix-idle")][0]).name, "wineserver")
        self.assertEqual(commands[names.index("wine-prefix-idle")][1:], ["-w"])
        self.assertEqual(names[-2:], ["wine-stop", "wine-wait"])

    def test_unfinished_prefix_blocks_dll_probes_and_still_cleans_up(self):
        code, _, results, _, _ = self.exercise(idle_code=1)
        self.assertEqual(code, 1)
        self.assertEqual([item["name"] for item in results], [
            "native-opengl", "native-vulkan-device", "wine-prefix", "wine-prefix-idle",
            "wine-stop", "wine-wait"])

    def test_timeout_captures_diagnostics_and_remains_a_failure(self):
        code, _, results, sample, kill = self.exercise(timeout_vulkan=True)
        self.assertEqual(code, 1)
        failed = next(item for item in results if item["name"] == "vulkan-loader-x86_64")
        self.assertEqual(failed["status"], "timed_out")
        diagnostics = [call.args[0] for call in sample.call_args_list if call.args[0][0] == '/usr/bin/sample']
        self.assertEqual(diagnostics, [["/usr/bin/sample", "123456", "3"]])
        kill.assert_called_once_with(123456, cloud_probes.signal.SIGKILL)
        self.assertEqual([item["name"] for item in results][-2:], ["wine-stop", "wine-wait"])

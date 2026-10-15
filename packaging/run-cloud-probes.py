#!/usr/bin/env python3
"""Bounded probes of the complete CI-built runtime on GitHub's macOS runner.

These probes never run on the owner's Mac. Missing hosted-runner GPU capability
is recorded as a skip; a failing assertion or timeout fails the job. Every Wine
check uses one disposable CI prefix and is followed by a bounded server stop.
"""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


class ProbeFailed(Exception):
    pass


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "darwin":
        raise SystemExit("Runtime probes require a GitHub Actions macOS runner.")
    if len(sys.argv) != 3:
        raise SystemExit("Usage: run-cloud-probes.py RUNTIME PROBE_DIRECTORY")
    runtime, probes = (Path(arg).resolve() for arg in sys.argv[1:])
    if not (runtime / ".gluon-wine-patches").is_file():
        raise SystemExit("Expected the complete source-built Gluon runtime.")
    probes.mkdir(parents=True, exist_ok=True)
    prefix = probes / "prefix"
    if prefix.exists():
        raise SystemExit("Probe prefix already exists; use a fresh probe directory.")
    env = os.environ.copy()
    for key in ("DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        env.pop(key, None)
    env.update(WINEPREFIX=str(prefix), WINEDEBUG="fixme-all",
               WINEDLLOVERRIDES="mscoree,mshtml,winemenubuilder.exe=d",
               DXVK_LOG_PATH=str(probes), DXVK_STATE_CACHE_PATH=str(probes))
    env.update(GST_PLUGIN_SYSTEM_PATH_1_0=str(runtime / "lib/gstreamer-1.0"),
               GST_PLUGIN_SCANNER_1_0=str(runtime / "libexec/gstreamer-1.0/gst-plugin-scanner"),
               GST_REGISTRY_1_0=str(probes / "gstreamer-registry.bin"))
    results = []

    def run(name, command, seconds=30, allow_unavailable=False, cleanup=False, overrides=None):
        path = probes / (name + ".log")
        started = time.monotonic()
        with path.open("w") as output:
            process = subprocess.Popen(command, env=dict(env, **(overrides or {})), cwd=probes,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                code = process.wait(timeout=seconds)
                status = "passed" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                code, status = None, "timed_out"
                output.write(f"\nStopped the probe after {seconds} seconds.\n")
        item = dict(name=name, status=status, exit_code=code,
                    elapsed_seconds=round(time.monotonic() - started, 1), log=path.name)
        contents = path.read_text(errors="replace")
        if allow_unavailable and code == 77 and "GLUON_PROBE_UNAVAILABLE:" in contents:
            item["status"] = "skipped"
            item["reason"] = "Hosted runner lacks the required native graphics capability."
        results.append(item)
        print(f"{name}: {item['status']}", flush=True)
        if item["status"] in ("failed", "timed_out") and not cleanup:
            raise ProbeFailed(name)
        return item, contents

    def require_native_portability(item, contents):
        # Check the enabled-device block rather than MoltenVK's earlier list
        # of every supported extension, which also contains this name.
        blocks = re.findall(r"\[mvk-info\] Created VkDevice[^\n]*extensions enabled:\n((?:\t[^\n]*\n)+)", contents)
        if not blocks or any("\tVK_KHR_portability_subset " not in block for block in blocks):
            item["status"] = "failed"
            item["reason"] = "Native MoltenVK device did not enable its portability subset."
            raise ProbeFailed(item["name"])

    wine = str(runtime / "bin/wine")
    server = str(runtime / "bin/wineserver")
    exit_code = 0
    try:
        native_gl, _ = run("native-opengl", [str(probes / "opengl-native")],
                           allow_unavailable=True)
        native_vk, _ = run("native-vulkan-device",
                               [str(probes / "vulkan-native"),
                                str(runtime / "lib/libMoltenVK.dylib"), "--device"],
                           allow_unavailable=True)
        run("wine-prefix", [wine, "wineboot", "--init"], seconds=60)
        # wineboot can return while setup/service children are still running.
        # Match the launcher's first-run barrier before probing other DLLs.
        run("wine-prefix-idle", [server, "-w"], seconds=30)
        for arch in ("x86_64", "i686"):
            run(f"runtime-dependencies-{arch}", [wine, str(probes / f"dependencies-{arch}.exe")])
            run(f"opengl-module-{arch}", [wine, str(probes / f"wgl-{arch}.exe"), "--load-only"])
            run(f"vulkan-loader-{arch}", [wine, str(probes / f"vulkan-{arch}.exe")])
            if native_gl["status"] == "passed":
                # Without the opt-in, a 3.3 compatibility request must still be rejected.
                run(f"wgl-alias-{arch}", [wine, str(probes / f"wgl-{arch}.exe"), "--require-alias"])
                run(f"wgl-compat-as-core-{arch}",
                    [wine, str(probes / f"wgl-{arch}.exe"), "--require-alias", "--compat-as-core"],
                    overrides={"GLUON_GL_COMPAT_AS_CORE": "1"})
            else:
                for name in (f"wgl-alias-{arch}", f"wgl-compat-as-core-{arch}"):
                    results.append(dict(name=name, status="skipped",
                                        reason="Native CGL prerequisite did not pass."))
            if native_vk["status"] == "passed":
                device, contents = run(f"vulkan-device-1.0-{arch}",
                                       [wine, str(probes / f"vulkan-{arch}.exe"), "--device-1.0"],
                                       overrides={"WINEDEBUG": "fixme-all,+vulkan", "MVK_CONFIG_LOG_LEVEL": "3"})
                require_native_portability(device, contents)
                rendered, contents = run(f"d3d9-render-{arch}", [wine, str(probes / f"d3d9-{arch}.exe")],
                                         overrides={"MVK_CONFIG_LOG_LEVEL": "3"})
                require_native_portability(rendered, contents)
            else:
                results.append(dict(name=f"vulkan-device-1.0-{arch}", status="skipped",
                                    reason="Native Metal device prerequisite did not pass."))
                results.append(dict(name=f"d3d9-render-{arch}", status="skipped",
                                    reason="Native Metal device prerequisite did not pass."))
    except (ProbeFailed, OSError) as error:
        print(f"Stopped runtime checks: {error}", flush=True)
        exit_code = 1
    finally:
        # The server may daemonize outside the probe's process group. Stop only
        # this CI prefix, including on failed/timed-out Wine initialization.
        if prefix.exists():
            try:
                stopped, _ = run("wine-stop", [server, "-k"], seconds=10, cleanup=True)
                if stopped["status"] != "passed":
                    exit_code = 1
                waited, _ = run("wine-wait", [server, "-w"], seconds=10, cleanup=True)
                if waited["status"] != "passed":
                    exit_code = 1
            except OSError as error:
                results.append(dict(name="wine-cleanup", status="failed", error=str(error)))
                exit_code = 1
        (probes / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as output:
                output.write("\n| Runtime check | Result |\n|---|---|\n")
                for item in results:
                    output.write(f"| {item['name']} | {item['status']} |\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

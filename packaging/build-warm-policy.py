#!/usr/bin/env python3
"""Capture a runtime-bound stock service policy from a stopped disposable CI prefix."""
import argparse
import os
from pathlib import Path
import sys
import shutil
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gluon.service_policy import build_stock_policy
from gluon.session import _system_environment, _bootstrap_services


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "darwin":
        raise SystemExit("Stock service policy generation requires GitHub Actions macOS")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    runtime, prefix = args.runtime.resolve(strict=True), args.prefix.resolve()
    if os.path.lexists(prefix):
        raise SystemExit('Stock policy requires a new isolated prefix')
    prefix.parent.mkdir(parents=True, exist_ok=True)
    environment = _system_environment(runtime, prefix)
    environment.update(WINEDEBUG='fixme-all', WINEMSYNC='1')
    wine, server = runtime / 'bin/wine', runtime / 'bin/wineserver'
    prefix.mkdir()
    try:
        subprocess.run([str(wine), 'wineboot', '--init'], env=environment, check=True, timeout=60)
        subprocess.run([str(server), '-w'], env=environment, check=True, timeout=30)
        _bootstrap_services(runtime, prefix)
        subprocess.run([str(server), '-w'], env=environment, check=True, timeout=30)
        result = build_stock_policy(runtime, prefix)
        print("Stock service policy: %d registry keys, %d required PE files, %d optional bridge files" %
              (len(result["registry"]), len(result["required"]), len(result["optional"])))
    finally:
        subprocess.run([str(server), '-k'], env=environment, timeout=10, check=False)
        subprocess.run([str(server), '-w'], env=environment, timeout=15, check=True)
        shutil.rmtree(prefix)


if __name__ == "__main__":
    main()

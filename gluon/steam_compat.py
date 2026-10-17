"""Explicit developer launch probe; no Steam files or compatibility state are edited.

The native component is a read-only resolver, not a working enabler. Starting
steam_osx directly tests whether dyld can load it without the app bootstrapper.
Nothing launches unless the caller supplies --launch.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys
import tempfile

from . import steam


def probe_paths(library=None):
    client = Path(steam.CLIENT_DIR).resolve()
    dylib = Path(library or Path(steam.GLUON_HOME) / "lib/libgluon-steam-compat.dylib").resolve()
    if ":" in str(dylib) or "\n" in str(dylib):
        raise steam.SteamError("the probe library path must not contain a colon or newline")
    return client / "steam_osx", dylib


def probe_environment(library, log):
    # Supply the probe to the executable itself, after Python has started. Do
    # not involve /usr/bin/open, a shell, or Steam.app's hardened bootstrapper.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DYLD_", "GLUON_STEAM_COMPAT_"))}
    environment.update(DYLD_INSERT_LIBRARIES=str(library), GLUON_STEAM_COMPAT_PROBE="1",
                       GLUON_STEAM_COMPAT_LOG=str(log))
    return environment


def cmd_probe(args):
    executable, library = probe_paths(args.library)
    print("Read-only Steam compatibility probe; hooks remain disabled.")
    print("Executable:", executable)
    print("Library   :", library)
    print("Working directory:", executable.parent)
    if not args.launch:
        print("Preview only. An approved test uses --launch after Steam has quit.")
        print("Command:", shlex.quote(str(executable)))
        return 0
    if sys.platform != "darwin":
        raise steam.SteamError("the Steam launch probe requires macOS")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise steam.SteamError("Steam's client executable is missing or not executable")
    if not library.is_file():
        raise steam.SteamError("probe library missing; download the GitHub Actions steam-compat artifact")
    if steam.is_running():
        raise steam.SteamError("quit Steam before starting the launch probe")
    logs = Path(steam.GLUON_HOME) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    descriptor, log = tempfile.mkstemp(prefix="steam-compat-", suffix=".log", dir=logs)
    os.close(descriptor)
    print("Probe log :", log, flush=True)
    environment = probe_environment(library, log)
    os.chdir(executable.parent)
    os.execve(str(executable), [str(executable)], environment)
    return 0

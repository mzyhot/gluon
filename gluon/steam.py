"""Where the macOS Steam client keeps things, and whether it is running."""
from __future__ import annotations

import os
import subprocess
from typing import List

STEAM_ROOT = os.environ.get("STEAM_ROOT") or os.path.expanduser("~/Library/Application Support/Steam")
GLUON_HOME = os.environ.get("GLUON_HOME") or os.path.expanduser("~/Library/Application Support/Gluon")

CLIENT_DIR = os.path.join(STEAM_ROOT, "Steam.AppBundle", "Steam", "Contents", "MacOS")
APPINFO = os.path.join(STEAM_ROOT, "appcache", "appinfo.vdf")
CONFIG_VDF = os.path.join(STEAM_ROOT, "config", "config.vdf")
TOOL_DIR = os.path.join(CLIENT_DIR, "compatibilitytools.d", "gluon")
# The package installs the launcher into Gluon's own folder; scripts/install-tool.sh puts a
# development copy into Steam's tool folder. Launch options written for either are ours.
INSTALLED_LAUNCHER = os.path.join(GLUON_HOME, "bin", "gluon")
DEV_LAUNCHER = os.path.join(TOOL_DIR, "gluon")
LAUNCHERS = (INSTALLED_LAUNCHER, DEV_LAUNCHER)
LAUNCHER = INSTALLED_LAUNCHER if os.access(INSTALLED_LAUNCHER, os.X_OK) else DEV_LAUNCHER


class SteamError(RuntimeError):
    pass


def is_running() -> bool:
    try:
        result = subprocess.run(["pgrep", "-x", "steam_osx"], capture_output=True)
    except OSError as exc:
        raise SteamError("cannot check whether Steam is running; its files were left alone") from exc
    if result.returncode not in (0, 1):
        raise SteamError("cannot check whether Steam is running; its files were left alone")
    return result.returncode == 0


def user_config_dirs() -> List[str]:
    base = os.path.join(STEAM_ROOT, "userdata")
    out = []
    for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        d = os.path.join(base, name, "config")
        if name.isdigit() and os.path.isdir(d):
            out.append(d)
    return out


def library_dirs() -> List[str]:
    from . import vdf
    path = os.path.join(STEAM_ROOT, "steamapps", "libraryfolders.vdf")
    try:
        folders = vdf.load(path).get("libraryfolders", {})
    except OSError:
        return [STEAM_ROOT]
    return [f["path"] for f in folders.values() if isinstance(f, dict) and "path" in f] or [STEAM_ROOT]

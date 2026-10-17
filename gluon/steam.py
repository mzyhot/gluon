"""Where the macOS Steam client keeps things, and whether it is running."""
from __future__ import annotations

import os
import select
import subprocess
import time
from typing import List

STEAM_ROOT = os.environ.get("STEAM_ROOT") or os.path.expanduser("~/Library/Application Support/Steam")
GLUON_HOME = os.environ.get("GLUON_HOME") or os.path.expanduser("~/Library/Application Support/Gluon")

CLIENT_DIR = os.path.join(STEAM_ROOT, "Steam.AppBundle", "Steam", "Contents", "MacOS")
APPINFO = os.path.join(STEAM_ROOT, "appcache", "appinfo.vdf")
CONFIG_VDF = os.path.join(STEAM_ROOT, "config", "config.vdf")
TOOL_DIR = os.path.join(CLIENT_DIR, "compatibilitytools.d", "gluon")
# The registered tool is only a shim to the installed launcher. Keep recognizing
# old launch options that named the tool copy, but never write new ones to it.
INSTALLED_LAUNCHER = os.path.join(GLUON_HOME, "bin", "gluon")
DEV_LAUNCHER = os.path.join(TOOL_DIR, "gluon")
LAUNCHERS = (INSTALLED_LAUNCHER, DEV_LAUNCHER)
LAUNCHER = INSTALLED_LAUNCHER


class SteamError(RuntimeError):
    pass


def tool_copy_state() -> str:
    """Check the registered entry point against the canonical packaged shim.

    An old full launcher is stale even when it matches the installed launcher.
    A valid shim also needs both it and its installed target to be executable.
    This only reads files; it never registers or updates the tool in Steam.
    """
    try:
        with open(DEV_LAUNCHER, "rb") as f:
            theirs = f.read()
    except OSError:
        return "absent"
    canonical = os.path.join(os.path.dirname(__file__), "tool-shim")
    if not os.path.isfile(canonical):
        canonical = os.path.join(os.path.dirname(__file__), "..", "tool", "gluon-shim")
    try:
        with open(canonical, "rb") as f:
            ours = f.read()
    except OSError:
        return "unknown"
    if ours != theirs:
        return "differs"
    if not os.access(DEV_LAUNCHER, os.X_OK):
        return "unusable"
    if not os.path.isfile(INSTALLED_LAUNCHER) or not os.access(INSTALLED_LAUNCHER, os.X_OK):
        return "unavailable"
    return "shim"


def _pgrep() -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(["pgrep", "-x", "steam_osx"], capture_output=True, text=True)
    except OSError as exc:
        raise SteamError("cannot check whether Steam is running; its files were left alone") from exc
    if result.returncode not in (0, 1):
        raise SteamError("cannot check whether Steam is running; its files were left alone")
    return result


def is_running() -> bool:
    return _pgrep().returncode == 0


def pids() -> List[int]:
    result = _pgrep()
    return [int(p) for p in (result.stdout or "").split()] if result.returncode == 0 else []


def wait_for_exit(timeout: float) -> bool:
    """Wait up to `timeout` seconds for the Steam client to quit; True once it has.

    Sleeps on kqueue process-exit events, so a long wait costs no CPU.
    """
    deadline = time.monotonic() + timeout
    while True:
        running = pids()
        if not running:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        _wait_for_any_exit(running, remaining)


def _wait_for_any_exit(running: List[int], timeout: float) -> None:
    """Return once one of the processes exits, or after `timeout` seconds."""
    if not hasattr(select, "kqueue"):
        time.sleep(min(timeout, 2.0))
        return
    kq = select.kqueue()
    try:
        for pid in running:
            try:
                kq.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                          flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                          fflags=select.KQ_NOTE_EXIT)], 0)
            except ProcessLookupError:
                return
            except OSError:
                time.sleep(min(timeout, 2.0))
                return
        kq.control(None, 1, timeout)
    finally:
        kq.close()


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

"""Limit damage from a Steam metadata refresh without modifying a running client.

Steam owns appinfo.vdf and may replace its entries while running. Gluon can only
repair that cache after Steam exits. Installed managed games use Steam's ordinary
update-on-launch policy to defer background depot reconciliation in that window.
This cannot block manual verification or an update performed before game launch.
"""
from __future__ import annotations

import json
import os
import stat
import struct
import subprocess
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List

from . import appinfo, install, steam, vdf

POLICIES = os.path.join(steam.GLUON_HOME, "state", "update-policies.json")
OBSERVED = os.path.join(steam.GLUON_HOME, "state", "metadata-observed.json")
UPDATE_ON_LAUNCH = "1"


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + "." + uuid.uuid4().hex + ".tmp"
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _manifest(path: str, appid: int):
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
        raise ValueError("unsafe manifest")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8", errors="surrogateescape") as handle:
        opened = os.fstat(handle.fileno())
        if install._identity(opened) != install._identity(before):
            raise ValueError("manifest changed")
        text = handle.read()
    data = install._strict_manifest(text)
    app = vdf.iget(data, "AppState")
    if len(data) != 1 or not isinstance(app, dict) or vdf.iget(app, "appid") != str(appid):
        raise ValueError("wrong manifest identity")
    # The formatter must preserve everything before we change one scalar.
    if vdf.dumps(data) != text:
        raise ValueError("manifest does not round-trip exactly")
    return text, data, app, opened


def _replace_manifest(path: str, data: Dict[str, Any], opened: os.stat_result) -> bool:
    temporary = path + ".gluon-" + uuid.uuid4().hex + ".tmp"
    try:
        with open(temporary, "x", encoding="utf-8", errors="surrogateescape") as handle:
            handle.write(vdf.dumps(data))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(opened.st_mode))
        if steam.is_running() or install._identity(os.lstat(path)) != install._identity(opened):
            return False
        os.replace(temporary, path)
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def guard_updates(appids: Iterable[int], libraries: Iterable[str] | None = None) -> List[str]:
    """Set update-on-launch for fully installed managed games, Steam closed only."""
    if steam.is_running():
        return []
    records = _load(POLICIES)
    try:
        roots = list(steam.library_dirs() if libraries is None else libraries)
    except (OSError, ValueError, TypeError):
        return []
    managed = sorted(set(appids))
    notes: List[str] = []
    for library in roots:
        try:
            if install._ready_common(library) is None:
                continue
        except (OSError, ValueError, TypeError):
            continue
        for appid in managed:
            path = os.path.join(library, "steamapps", "appmanifest_%d.acf" % appid)
            try:
                text, data, app, opened = _manifest(path, appid)
                leaf = install._install_leaf(app, appid)
                if leaf is None or not os.path.isdir(os.path.join(library, "steamapps", "common", leaf)):
                    continue
                key = next((k for k in app if k.lower() == "autoupdatebehavior"), None)
                current = app.get(key) if key is not None else None
                if current == UPDATE_ON_LAUNCH or current not in (None, "0", "2", "3"):
                    continue
                if steam.is_running():
                    return notes
                backup = os.path.join(steam.GLUON_HOME, "backups", "update-policy",
                                      "appmanifest_%d.%s.acf" % (appid, uuid.uuid4().hex))
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                with open(backup, "x", encoding="utf-8", errors="surrogateescape") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                # Record rollback before modifying Steam's copy. If the user has
                # since selected another policy, preserve that latest choice.
                records[path] = {"appid": appid, "key": key, "original": current,
                                 "backup": backup}
                _save(POLICIES, records)
                app[key or "AutoUpdateBehavior"] = UPDATE_ON_LAUNCH
                if _replace_manifest(path, data, opened):
                    notes.append("%d: deferred background updates until launch (manifest backup: %s)" % (appid, backup))
            except (OSError, ValueError, TypeError):
                continue
    return notes


def restore_updates(appids: Iterable[int]) -> List[str]:
    """Undo our policy only when its current value still equals our setting."""
    if steam.is_running():
        return []
    selected = set(appids)
    records = _load(POLICIES)
    notes: List[str] = []
    for path, rec in list(records.items()):
        if not isinstance(rec, dict) or rec.get("appid") not in selected:
            continue
        library = os.path.dirname(os.path.dirname(path))
        try:
            if install._ready_common(library) is None:
                continue
        except (OSError, ValueError, TypeError):
            # An absent/unmounted library is not a deleted manifest. Keep its
            # original preference so it can be restored when the volume returns.
            continue
        try:
            appid = rec["appid"]
            _, data, app, opened = _manifest(path, appid)
            key = next((k for k in app if k.lower() == "autoupdatebehavior"), None)
            if key is not None and app.get(key) == UPDATE_ON_LAUNCH:
                if rec.get("key") == key:
                    app[key] = rec.get("original")
                elif rec.get("key") is None:
                    del app[key]
                else:
                    del app[key]
                    app[rec["key"]] = rec.get("original")
                if not _replace_manifest(path, data, opened):
                    continue
                notes.append("%d: restored automatic update preference" % appid)
            del records[path]
        except FileNotFoundError:
            del records[path]
        except (OSError, ValueError, TypeError, KeyError):
            continue
    _save(POLICIES, records)
    return notes


def _game_running() -> bool:
    """A Gluon session or any Wine server is running."""
    for pattern in (["pgrep", "-x", "wineserver"], ["pgrep", "-f", "/gluon-launcher-host "]):
        try:
            if subprocess.run(pattern, capture_output=True, timeout=5).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            return True
    return False


def _ask_restart(message: str) -> bool:
    """Ask once; no answer within five minutes counts as Later."""
    script = ("on run argv\n"
              " set reply to display dialog (item 1 of argv) with title \"Gluon\" "
              "buttons {\"Later\", \"Restart Steam\"} default button \"Restart Steam\" giving up after 300\n"
              " return button returned of reply\n"
              "end run")
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script, message],
                                capture_output=True, text=True, timeout=330)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "Restart Steam"


def _notify(message: str) -> None:
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        "on run argv\n display notification (item 1 of argv) with title \"Gluon\"\nend run",
                        message], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _restart_steam(repair: Callable[[], List[str]], wait: float = 120.0,
                   sleep: Callable[[float], None] = time.sleep) -> List[str]:
    """Quit Steam through its own exit URL, repair while it is closed, reopen it."""
    subprocess.run(["/usr/bin/open", "steam://exit"], capture_output=True, timeout=10)
    waited = 0.0
    while steam.is_running():
        if waited >= wait:
            _notify("Steam did not quit, so Gluon changed nothing. Quit and reopen Steam yourself.")
            return ["Steam did not quit within %ds; nothing was repaired" % wait]
        sleep(1.0)
        waited += 1.0
    lines = repair()
    subprocess.run(["/usr/bin/open", "-b", "com.valvesoftware.steam"], capture_output=True, timeout=10)
    return ["restarted Steam to restore refreshed Gluon games"] + lines


def watch_refresh(state: Dict[str, Any], planned_changes: Callable,
                  repair: Callable[[], List[str]] | None = None) -> List[str]:
    """Read changed cache files and offer, once per refreshed set, to restart Steam.

    The helper may run while Steam is open: it writes Gluon's own observation
    state and never Steam's cache or manifests. Restarting is offered only when
    no game is running, and only Steam's own exit closes it; the repair runs
    after Steam has quit.
    """
    if not state:
        return []
    try:
        before = os.stat(steam.APPINFO)
        signature = list(install._identity(before))
        observed = _load(OBSERVED)
        managed = sorted(state)
        if observed.get("cache") == signature and observed.get("managed") == managed:
            drift = observed.get("drift", [])
        else:
            cache = appinfo.AppInfoFile(steam.APPINFO)
            if install._identity(os.stat(steam.APPINFO)) != install._identity(before):
                return []
            drift = []
            for key in managed:
                entry = cache.get(int(key))
                if entry is None or planned_changes(entry.kv["appinfo"], int(key), entry.change_number):
                    drift.append([key, entry.change_number if entry is not None else None])
            observed.update(cache=signature, managed=managed, drift=drift)
            if not drift:
                observed.pop("offered", None)
            _save(OBSERVED, observed)
    except (OSError, ValueError, TypeError, KeyError, IndexError, struct.error):
        return []
    if not drift or drift == observed.get("offered") or _game_running():
        return []
    observed["offered"] = drift
    _save(OBSERVED, observed)
    games = ", ".join(item[0] for item in drift)
    message = ("Steam's details for %d Gluon game(s) need updating, for example after Steam refreshed "
               "them, so it may not install, update, start or cloud-sync them correctly. Restart Steam "
               "now so Gluon can update them?" % len(drift))
    if repair is None or not _ask_restart(message):
        return ["metadata refresh detected for apps %s; restart offered and postponed" % games]
    if _game_running():
        # A game started while the question was open. Quitting Steam would end
        # it, so ask again once no game is running.
        observed.pop("offered", None)
        _save(OBSERVED, observed)
        return ["metadata refresh detected for apps %s; a game started, restart postponed" % games]
    return ["metadata refresh detected for apps %s" % games] + _restart_steam(repair)

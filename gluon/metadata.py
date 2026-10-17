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


def watch_refresh(state: Dict[str, Any], planned_changes: Callable) -> List[str]:
    """Note, once per refreshed set, which managed entries Steam refreshed while running.

    Refreshed games keep launching. The helper re-applies Gluon's details at the next
    Steam quit and then clears the update flag the refresh left behind
    (settle_update_flags). While Steam runs it writes only Gluon's own state.
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
            observed.pop("offered", None)
            if not drift:
                observed.pop("reported", None)
            _save(OBSERVED, observed)
    except (OSError, ValueError, TypeError, KeyError, IndexError, struct.error):
        return []
    if not drift or drift == observed.get("reported"):
        return []
    observed["reported"] = drift
    _save(OBSERVED, observed)
    return ["metadata refresh detected for apps %s; Gluon re-applies its details when Steam quits"
            % ", ".join(item[0] for item in drift)]


# StateFlags bits in an app manifest: 4 fully installed, 2 update required.
FULLY_INSTALLED = 4
UPDATE_REQUIRED = 2


def _key(app: Dict[str, Any], name: str) -> str | None:
    return next((k for k in app if k.lower() == name.lower()), None)


def _matches_details(app: Dict[str, Any], details: Dict[str, Any]) -> bool:
    """The installed build and every installed depot manifest equal Steam's details."""
    config = vdf.iget(app, "UserConfig")
    branch = vdf.iget(config, "BetaKey") if isinstance(config, dict) else None
    if branch not in (None, "", "public"):
        return False
    depots = details.get("depots") or {}
    public = (depots.get("branches") or {}).get("public") or {}
    if str(public.get("buildid")) != str(vdf.iget(app, "buildid")):
        return False
    installed = vdf.iget(app, "InstalledDepots")
    if not isinstance(installed, dict) or not installed:
        return False
    for depot, record in installed.items():
        target = ((depots.get(depot) or {}).get("manifests") or {}).get("public")
        if isinstance(target, dict):
            target = target.get("gid")
        if not isinstance(record, dict) or target is None or str(target) != str(vdf.iget(record, "manifest")):
            return False
    return True


def settle_update_flags(appids: Iterable[int], libraries: Iterable[str] | None = None) -> List[str]:
    """Clear the Update Required flag a metadata refresh left on an installed game.

    While Steam runs, a refresh makes it flag the game for an update, and the flag
    is saved in the game's manifest; after Gluon re-applies its details Steam then
    runs an update that finds no changes. Steam closed only, and only when the
    installed build and depot manifests already match Steam's details: a real update
    (another build or manifest) keeps its flag and runs normally.
    """
    if steam.is_running():
        return []
    try:
        roots = list(steam.library_dirs() if libraries is None else libraries)
        cache = appinfo.AppInfoFile(steam.APPINFO)
    except (OSError, ValueError, TypeError, struct.error):
        return []
    notes: List[str] = []
    for library in roots:
        for appid in sorted(set(appids)):
            path = os.path.join(library, "steamapps", "appmanifest_%d.acf" % appid)
            if not os.path.exists(path):
                continue
            try:
                text, data, app, opened = _manifest(path, appid)
                flags_key = _key(app, "StateFlags")
                # Only the plain "installed, update required" state; never one
                # with a download running, paused or started.
                if flags_key is None or app[flags_key] != str(FULLY_INSTALLED | UPDATE_REQUIRED):
                    continue
                entry = cache.get(appid)
                if entry is None or not _matches_details(app, entry.kv["appinfo"]):
                    continue
                if steam.is_running():
                    return notes
                backup = os.path.join(steam.GLUON_HOME, "backups", "update-flags",
                                      "appmanifest_%d.%s.acf" % (appid, uuid.uuid4().hex))
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                with open(backup, "x", encoding="utf-8", errors="surrogateescape") as handle:
                    handle.write(text)
                app[flags_key] = str(FULLY_INSTALLED)
                scheduled = _key(app, "ScheduledAutoUpdate")
                if scheduled is not None:
                    app[scheduled] = "0"
                if _replace_manifest(path, data, opened):
                    notes.append("%d: cleared the update flag a metadata refresh left (no changes; "
                                 "manifest backup: %s)" % (appid, backup))
            except (OSError, ValueError, TypeError, AttributeError):
                continue
    return notes

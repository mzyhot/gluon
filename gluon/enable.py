"""Make a Windows-only app installable in the macOS Steam client.

The client decides whether an app can be installed, which depots it needs and which
launch entry applies from its local metadata cache (appcache/appinfo.vdf). Adding
"macos" next to "windows" in an app's entry is enough for the stock client to download
the Windows depots and offer Play - no platform forcing, nothing running inside Steam.

Every value we change is recorded in $GLUON_HOME/state/apps.json first so `disable`
can put the entry back exactly. Steam replaces an entry on its own whenever the app's
change number moves on the server; `status` shows when that has happened.

The cache is read at startup and written back on exit, so Steam must not be running.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from . import appinfo, cloud, steam, vdf

STATE = os.path.join(steam.GLUON_HOME, "state", "apps.json")

# Steam Cloud: as a macOS client Steam evaluates an app's cloud rules for macOS, where
# Windows save locations do not resolve, and files that match no rule get removed from
# the user's cloud after a session (seen with People Playground, 2026-10-13). gluon.cloud
# maps each cross-platform rule's Windows location into the game's Wine prefix with a
# macOS root override; an app it cannot map keeps its cloud section out of the cached
# entry, which turns Steam Cloud off for it on this Mac only.

# Steam downloads an app's details again whenever its change number on Steam's servers
# moves past the cached one, which happens several times a day for some games. A
# running client then treats the game as Windows-only until it restarts: Play can fail,
# and an update or verification can remove the installed files (seen on 2026-10-14).
# Installed managed games therefore keep a cached change number above any real one, so
# Steam keeps Gluon's details. Real game updates arrive in an update window: once per
# GLUON_GAME_UPDATES interval the real numbers are restored for one Steam session.
FROZEN_CHANGE_NUMBER = 2_000_000_000
UPDATE_INTERVALS = {"daily": 86400, "weekly": 7 * 86400, "never": None}
UPDATES = os.path.join(steam.GLUON_HOME, "state", "game-updates.json")

# App types offered for install. Left out: tools (SDKs, dedicated servers), DLC (its files
# come through the base game), music, config.
PLAYABLE_TYPES = ("game", "demo", "application", "beta", "mod")
Path = Tuple[str, ...]


class EnableError(RuntimeError):
    pass


def _items(csv: Any) -> List[str]:
    # Some entries are written "windows, macos"
    return [p.strip() for p in str(csv or "").split(",") if p.strip()]


def _with(csv: Any, item: str) -> str:
    parts = _items(csv)
    if item not in parts:
        parts.append(item)
    return ",".join(parts)


def _has(csv: Any, item: str) -> bool:
    return item in _items(csv)


def has_64bit_mac_build(common: Dict[str, Any]) -> bool:
    """Steam marks a Mac build it can still run with macos64 or macosapplesilicon."""
    extended = _items(common.get("osextended"))
    return _has(common.get("oslist"), "macos") and ("macos64" in extended or "macosapplesilicon" in extended)


def runs_through_gluon(ai: Dict[str, Any]) -> bool:
    """Windows games without a Mac build, and games whose only Mac build is 32-bit
    (Steam's "Invalid OS Type" on current macOS) but that have a Windows build."""
    common = ai.get("common") or {}
    oslist = common.get("oslist")
    if not _has(oslist, "macos"):
        return True
    return _has(oslist, "windows") and not has_64bit_mac_build(common)


def _get(d: Dict[str, Any], path: Path) -> Any:
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _set(d: Dict[str, Any], path: Path, value: Any) -> None:
    for k in path[:-1]:
        d = d.setdefault(k, {})
    if value is None:
        d.pop(path[-1], None)
    else:
        d[path[-1]] = value


def installed_appids(appids: Any) -> set:
    """Managed games with installed files in any Steam library."""
    wanted = {int(a) for a in appids}
    found = set()
    try:
        libraries = steam.library_dirs()
    except (OSError, ValueError, TypeError):
        return found
    for library in libraries:
        for appid in wanted - found:
            try:
                app = vdf.load(os.path.join(library, "steamapps", "appmanifest_%d.acf" % appid)).get("AppState", {})
                flags = int(vdf.iget(app, "StateFlags") or 0)
                leaf = vdf.iget(app, "installdir")
            except (OSError, ValueError, TypeError, AttributeError):
                continue
            # Fully installed, including while Steam wants an update.
            if flags & 4 and isinstance(leaf, str) and leaf and "/" not in leaf and \
                    os.path.isdir(os.path.join(library, "steamapps", "common", leaf)):
                found.add(appid)
    return found


def _server_change(entry: Any, rec: Optional[Dict[str, Any]]) -> int:
    """The app's real change number: the cache's, unless Gluon froze it."""
    if entry.change_number != FROZEN_CHANGE_NUMBER:
        return entry.change_number
    return int((rec or {}).get("change_number") or 0)


def _load_updates() -> Dict[str, Any]:
    try:
        with open(UPDATES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_updates(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(UPDATES), exist_ok=True)
    with open(UPDATES + ".tmp", "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(UPDATES + ".tmp", UPDATES)


def update_window_open() -> bool:
    return bool(_load_updates().get("open"))


def note_steam_running() -> None:
    """An open update window ends at the first Steam quit after Steam has run."""
    data = _load_updates()
    if data.get("open") and not data.get("steam_ran"):
        data["steam_ran"] = True
        _save_updates(data)


def _update_interval() -> Optional[int]:
    value = cloud._settings(0).get("GLUON_GAME_UPDATES", "weekly").strip().lower()
    return UPDATE_INTERVALS.get(value, UPDATE_INTERVALS["weekly"])


def _cloud_source(ai: Dict[str, Any], appid: int, change_number: Optional[int],
                  state: Optional[Dict[str, Any]]) -> Any:
    """Steam's own cloud section for the app."""
    if "ufs" in ai:
        return ai["ufs"]
    # An earlier Gluon left the section out entirely; Steam's copy is in the state
    # until Steam refreshes the entry, which brings back its current section.
    rec = (state if state is not None else load_state()).get(str(appid)) or {}
    if change_number is None or (change_number != FROZEN_CHANGE_NUMBER and
                                 rec.get("change_number") != change_number):
        return None
    return _unpack(rec.get("original", {}).get("ufs"))


def planned_changes(ai: Dict[str, Any], appid: Optional[int] = None, change_number: Optional[int] = None,
                    state: Optional[Dict[str, Any]] = None) -> List[Tuple[Path, Any, Any]]:
    """(path, current value, new value) for everything that has to mention macOS and for
    the app's cloud section on this Mac. Without an app id the cloud section is left out."""
    out: List[Tuple[Path, Any, Any]] = []

    def want(path: Path, new: str) -> None:
        cur = _get(ai, path)
        if cur != new:
            out.append((path, cur, new))

    # A game whose Mac build is 32-bit only runs its Windows build instead: Steam
    # installs and starts the Windows depots and launch entries on this Mac.
    common = _get(ai, ("common",)) or {}
    mac32 = _has(common.get("oslist"), "macos") and _has(common.get("oslist"), "windows") \
        and not has_64bit_mac_build(common)
    # An app with no platform list at all is a Windows app (older titles omit it).
    want(("common", "oslist"), _with(_get(ai, ("common", "oslist")) or "windows", "macos"))
    # without this the client flags the app "Invalid OS Type" (its 32-bit-only-Mac-app marker)
    want(("common", "osextended"), _with(_get(ai, ("common", "osextended")), "macos64"))
    for section in (("depots",), ("config", "launch")):
        for key, item in (_get(ai, section) or {}).items():
            if not isinstance(item, dict):
                continue
            oslist = _get(item, ("config", "oslist"))
            if _has(oslist, "windows"):
                want(section + (key, "config", "oslist"), _with(oslist, "macos"))
            elif mac32 and _has(oslist, "macos"):
                out.append((section + (key,), item, None))
    if appid is None:
        if "ufs" in ai:
            out.append((("ufs",), ai["ufs"], None))
        return out
    source = _cloud_source(ai, appid, change_number, state)
    target = cloud.mapped(source, appid) if source is not None else None
    if target != ai.get("ufs"):
        out.append((("ufs",), ai.get("ufs"), target))
    return out


def _pack(section: Dict[str, Any]) -> Dict[str, str]:
    """A whole section in a form that survives the JSON state file with its value types."""
    return {"binary": base64.b64encode(vdf.binary_dumps(section)).decode("ascii")}


def _unpack(value: Any) -> Any:
    if isinstance(value, dict) and "binary" in value:
        return vdf.binary_loads(base64.b64decode(value["binary"]))
    return value


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def _open_cache() -> appinfo.AppInfoFile:
    if steam.is_running():
        raise EnableError("Steam is running; quit it first (it rewrites its metadata cache on exit)")
    return appinfo.AppInfoFile(steam.APPINFO)


def _backup_cache() -> str:
    d = os.path.join(steam.GLUON_HOME, "backups")
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, "appinfo.vdf." + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(steam.APPINFO, dest)
    return dest


def launch_option() -> str:
    """The Steam launch option that routes an app's Play button through Gluon."""
    return '"%s" run %%command%%' % steam.LAUNCHER


def _edit_launch_options(appids: List[int], install: bool) -> List[str]:
    """Add (or remove) Gluon's launch option in each local user's localconfig.vdf.

    A launch option the user wrote themselves is never replaced or removed.
    """
    notes: List[str] = []
    ours = launch_option()
    for cfg_dir in steam.user_config_dirs():
        path = os.path.join(cfg_dir, "localconfig.vdf")
        try:
            with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
                text = f.read()
        except OSError:
            continue
        data = vdf.loads(text)
        if vdf.dumps(data) != text:
            notes.append("%s: not rewritten (would not round-trip exactly); set the launch option in Steam instead" % path)
            continue
        apps = data
        for key in ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps"):
            apps = vdf.isetdefault(apps, key)
        changed = False
        for appid in appids:
            app = vdf.isetdefault(apps, str(appid)) if install else vdf.iget(apps, str(appid))
            if not isinstance(app, dict):
                continue
            key = next((k for k in app if k.lower() == "launchoptions"), "LaunchOptions")
            current = app.get(key, "")
            is_ours = any(current == '"%s" run %%command%%' % l for l in steam.LAUNCHERS)
            if install and (not current or (is_ours and current != ours)):
                app[key] = ours
                changed = True
            elif install and not is_ours:
                notes.append("%d: has its own launch option, left alone: %s" % (appid, current))
            elif not install and is_ours:
                del app[key]
                changed = True
        if changed:
            backup = os.path.join(steam.GLUON_HOME, "backups", "localconfig.vdf.%s.%s" % (
                os.path.basename(os.path.dirname(cfg_dir)), time.strftime("%Y%m%d-%H%M%S")))
            os.makedirs(os.path.dirname(backup), exist_ok=True)
            shutil.copy2(path, backup)
            with open(path + ".gluon-tmp", "w", encoding="utf-8", errors="surrogateescape") as f:
                f.write(vdf.dumps(data))
            os.replace(path + ".gluon-tmp", path)
            notes.append("launch options updated in %s (backup: %s)" % (path, backup))
    return notes


def enable(appids: List[int], freeze: bool = True) -> List[str]:
    cache = _open_cache()
    state = load_state()
    notes: List[str] = []
    dirty = False
    installed = installed_appids(appids) if freeze else set()
    for appid in appids:
        entry = cache.get(appid)
        if entry is None:
            notes.append("%d: not in Steam's metadata cache (open its library page once, then retry)" % appid)
            continue
        kv = entry.kv
        ai = kv["appinfo"]
        name = _get(ai, ("common", "name"))
        rec = state.get(str(appid))
        if rec is None and not runs_through_gluon(ai):
            notes.append("%d (%s): already has a macOS build, left alone" % (appid, name))
            continue
        changes = planned_changes(ai, appid, entry.change_number, state)
        server_change = _server_change(entry, rec)
        frozen = appid in installed
        refreeze = (entry.change_number != FROZEN_CHANGE_NUMBER) if frozen else \
            (entry.change_number == FROZEN_CHANGE_NUMBER and server_change > 0)
        if not changes and not refreeze:
            notes.append("%d (%s): already enabled" % (appid, name))
            continue
        if refreeze:
            entry.change_number = FROZEN_CHANGE_NUMBER if frozen else server_change
            dirty = True
            if not changes:
                notes.append("%d (%s): %s Steam's details" % (appid, name, "kept" if frozen else "released"))
                if rec:
                    rec["change_number"] = server_change
                continue
        original = dict((rec or {}).get("original", {}))
        record = {"name": name, "change_number": server_change, "original": original}
        if rec and "cloud_backup" in rec:
            record["cloud_backup"] = rec["cloud_backup"]
        for path, cur, new in changes:
            if path == ("ufs",) and isinstance(new, dict) and new.get("savefiles") \
                    and "cloud_backup" not in record:
                # Before Steam first syncs these saves on this Mac.
                try:
                    lines = cloud.backup(new, appid)
                except OSError as exc:
                    notes.append("%d (%s): Steam Cloud left off; backup failed: %s" % (appid, name, exc))
                    new = lines = None  # cloud off, as before; retried on the next sync
                if lines is not None:
                    notes += lines
                    record["cloud_backup"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if path == ("ufs",) and cloud.is_gluon_mapping(cur):
                pass  # Gluon's own mapping; the original stays Steam's section
            elif isinstance(cur, dict):
                original["/".join(path)] = _pack(cur)  # always the latest copy of a removed section
            else:
                original.setdefault("/".join(path), cur)
            _set(ai, path, new)
        entry.set_kv(kv)
        state[str(appid)] = record
        dirty = True
        notes.append("%d (%s): enabled (%d values)" % (appid, name, len(changes)))
    if dirty:
        notes.append("cache backup: " + _backup_cache())
        save_state(state)  # before the cache, so a recorded original always exists
        cache.save()
    notes += _edit_launch_options([a for a in appids if str(a) in state], install=True)
    from . import metadata
    notes += metadata.guard_updates([a for a in appids if str(a) in state])
    return notes


def disable(appids: List[int]) -> List[str]:
    cache = _open_cache()
    state = load_state()
    notes: List[str] = []
    dirty = False
    for appid in appids:
        rec = state.get(str(appid))
        if rec is None:
            notes.append("%d: not enabled by Gluon" % appid)
            continue
        entry = cache.get(appid)
        if entry is not None:
            kv = entry.kv
            for key, value in rec["original"].items():
                _set(kv["appinfo"], tuple(key.split("/")), _unpack(value))
            entry.set_kv(kv)
            # Let Steam refresh the entry from its servers again.
            if entry.change_number == FROZEN_CHANGE_NUMBER:
                entry.change_number = int(rec.get("change_number") or 0)
            dirty = True
        del state[str(appid)]
        notes.append("%d (%s): restored" % (appid, rec.get("name")))
    if dirty:
        notes.append("cache backup: " + _backup_cache())
        cache.save()
    notes += _edit_launch_options([a for a in appids if str(a) not in state], install=False)
    from . import metadata
    notes += metadata.restore_updates(appids)
    save_state(state)
    return notes


def status() -> List[Dict[str, Any]]:
    """One row per enabled app; `in_effect` is False once Steam has refreshed the entry."""
    cache = appinfo.AppInfoFile(steam.APPINFO)
    rows = []
    state = load_state()
    installed = installed_appids(state)
    window = update_window_open()
    for key, rec in sorted(state.items(), key=lambda kv: int(kv[0])):
        entry = cache.get(int(key))
        pending: Optional[int] = None
        change = None
        if entry is not None:
            pending = len(planned_changes(entry.kv["appinfo"], int(key), entry.change_number))
            change = entry.change_number
            if pending == 0 and int(key) in installed and not window and \
                    entry.change_number != FROZEN_CHANGE_NUMBER:
                pending = 1
        rows.append({"appid": int(key), "name": rec.get("name"), "in_effect": pending == 0,
                     "enabled_at_change": rec.get("change_number"), "cache_change": change})
    return rows


def library_appids() -> Optional[set]:
    """Apps in the user's library, taken from the folders Steam keeps for its library view."""
    d = os.path.join(steam.STEAM_ROOT, "appcache", "librarycache")
    try:
        return {int(n) for n in os.listdir(d) if n.isdigit()}
    except OSError:
        return None


def candidates() -> List[Tuple[int, str]]:
    """Games in the user's library that list Windows but no Mac build current macOS runs."""
    owned = library_appids()
    out = []
    for entry in appinfo.AppInfoFile(steam.APPINFO).entries:
        if owned is not None and entry.appid not in owned:
            continue
        common = entry.kv.get("appinfo", {}).get("common", {})
        if str(common.get("type", "")).lower() not in PLAYABLE_TYPES:
            continue
        # Everything without a macOS build, including titles that list no platform at
        # all, and games whose Mac build is 32-bit only.
        if runs_through_gluon(entry.kv.get("appinfo", {})):
            out.append((entry.appid, str(common.get("name"))))
    return sorted(out, key=lambda x: x[1].lower())


def sync(dry_run: bool = False) -> List[str]:
    """What the background helper runs: enable every Windows-only game in the library and
    re-apply refreshed entries and recover stale missing installs. While Steam runs,
    read changed metadata and warn; Steam's files are never edited then."""
    if steam.is_running():
        if dry_run:
            return []
        from . import metadata
        note_steam_running()
        # Only an installed game can fail to start or lose files after a refresh;
        # other refreshed entries are repaired quietly at the next Steam quit. The
        # repair runs through this same function once Steam has quit.
        state = load_state()
        installed = installed_appids(state)
        watched = {k: v for k, v in state.items() if int(k) in installed}
        return metadata.watch_refresh(watched, lambda ai, appid, change: planned_changes(ai, appid, change, state),
                                      repair=lambda: sync(), updating=update_window_open())
    window_notes = [] if dry_run else _advance_update_window()
    wanted = sorted({a for a, _ in candidates()} | {int(k) for k in load_state()})
    pending = [r["appid"] for r in status() if not r["in_effect"]]
    todo = sorted({a for a, _ in candidates()} | set(pending))
    if dry_run:
        return ["would enable %d apps, re-apply %d" % (len(todo) - len(pending), len(pending))]
    notes = window_notes + (enable(wanted, freeze=not update_window_open()) if wanted else [])
    from . import install
    recovered = install.recover_missing_installs([int(k) for k in load_state()])
    return [n for n in notes if ": enabled (" in n or n.startswith(("launch options", "cache backup")) or "left alone" in n or ": deferred background updates" in n
                   or ": cloud backup" in n or "Steam Cloud left off" in n or "update window" in n] + recovered



def _advance_update_window(now: Optional[float] = None) -> List[str]:
    """Open an update window when one is due; close it after a Steam session.

    While it is open, installed games keep their real change numbers, so the next
    Steam session downloads their current details and any new build. Gluon then
    applies its details again and freezes them at the following Steam quit."""
    now = time.time() if now is None else now
    data = _load_updates()
    if data.get("open"):
        if not data.get("steam_ran"):
            return []
        data.update(open=False, steam_ran=False)
        _save_updates(data)
        return ["game update window closed"]
    if not data.get("last_window"):
        # The first run starts the interval instead of releasing everything at once.
        data["last_window"] = now
        _save_updates(data)
        return []
    interval = _update_interval()
    if interval is None or now - float(data["last_window"]) < interval:
        return []
    data.update(open=True, steam_ran=False, last_window=now)
    _save_updates(data)
    return ["game update window opened: the next Steam session refreshes installed games"]


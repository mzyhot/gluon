"""One-way recovery of Gluon's retired metadata workaround. Never enables apps."""
from __future__ import annotations

import base64
import json
import hashlib
from pathlib import Path
import uuid

from . import appinfo, install, legacy_cloud, steam, vdf


class MigrationError(RuntimeError):
    pass


def _load(path):
    if not path.exists():
        return {}
    if path.is_symlink():
        raise MigrationError("unsafe legacy state: " + str(path))
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise MigrationError("invalid legacy state: " + str(path))
    return value


def _get(data, keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _unpack(value):
    if isinstance(value, dict) and set(value) == {"binary"}:
        return vdf.binary_loads(base64.b64decode(value["binary"], validate=True))
    return value


def _expected(path, original):
    if path == ["ufs"]:
        return None
    word = "macos64" if path == ["common", "osextended"] else "macos"
    value = original or ("windows" if path == ["common", "oslist"] else "")
    parts = [x.strip() for x in str(value).split(",") if x.strip()]
    if word not in parts:
        parts.append(word)
    return ",".join(parts)


def migrate():
    from .steamplay import _atomic, _digest, _write_json
    if steam.is_running():
        raise MigrationError("quit Steam before migrating old Gluon settings")
    home = Path(steam.GLUON_HOME)
    state = _load(home / "state/apps.json")
    policies = _load(home / "state/update-policies.json")
    if not state and not policies:
        return []
    changes = {}  # path -> (original bytes, replacement bytes)
    conflicts = []
    notes = []
    cache_path = Path(steam.APPINFO)
    if state and cache_path.exists():
        before = cache_path.read_bytes()
        cache = appinfo.AppInfoFile(str(cache_path))
        if cache.dumps() != before:
            raise MigrationError("legacy appinfo cache does not round-trip; backups retained")
        for appid, record in state.items():
            if not appid.isdigit() or not isinstance(record, dict):
                raise MigrationError("invalid legacy app record")
            entry = cache.get(int(appid))
            # A newer server record supersedes all our old per-field originals.
            if entry is None or entry.change_number != record.get("change_number"):
                continue
            kv = entry.kv
            ai = kv["appinfo"]
            for name, packed in record.get("original", {}).items():
                keys = name.split("/")
                allowed = (keys in (["ufs"], ["common", "oslist"], ["common", "osextended"])
                           or (len(keys) == 4 and keys[0] == "depots" and keys[2:] == ["config", "oslist"])
                           or (len(keys) == 5 and keys[:2] == ["config", "launch"] and keys[3:] == ["config", "oslist"]))
                if not allowed:
                    raise MigrationError("unrecognized legacy field: " + name)
                original = _unpack(packed)
                current = _get(ai, keys)
                if current == original:
                    continue
                mapped_cloud = (keys == ["ufs"] and isinstance(original, dict)
                                and current == legacy_cloud.mapped(original, int(appid)))
                if not mapped_cloud and current != _expected(keys, original):
                    conflicts.append("%s/%s changed independently" % (appid, name))
                    continue
                parent = _get(ai, keys[:-1])
                if not isinstance(parent, dict):
                    conflicts.append("%s/%s parent is missing" % (appid, name))
                    continue
                if original is None:
                    parent.pop(keys[-1], None)
                else:
                    parent[keys[-1]] = original
            entry.set_kv(kv)
        after = cache.dumps()
        if after != before:
            changes[cache_path] = (before, after)
    elif state:
        conflicts.append("legacy appinfo cache is missing")

    launchers = (str(home / "bin/gluon"), steam.DEV_LAUNCHER)
    for cfg in steam.user_config_dirs():
        path = Path(cfg) / "localconfig.vdf"
        if not path.exists():
            continue
        before = path.read_bytes()
        text = before.decode("utf-8", "surrogateescape")
        data = install._strict_manifest(text)
        apps = data
        for key in ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps"):
            apps = vdf.iget(apps, key, default={}) if isinstance(apps, dict) else {}
        dirty = False
        for appid in state:
            app = vdf.iget(apps, appid, default={})
            if not isinstance(app, dict):
                continue
            for key in list(app):
                if key.lower() == "launchoptions" and app[key] in ['"%s" run %%command%%' % x for x in launchers]:
                    if not dirty and vdf.dumps(data) != text:
                        conflicts.append(str(path) + " does not round-trip")
                        break
                    del app[key]
                    dirty = True
        if dirty:
            changes[path] = (before, vdf.dumps(data).encode("utf-8", "surrogateescape"))

    libraries = {str(Path(x).resolve()) for x in steam.library_dirs()}
    for name, rec in policies.items():
        path = Path(name)
        library = path.parent.parent
        if (not isinstance(rec, dict) or str(library.resolve()) not in libraries
                or path.name != "appmanifest_%s.acf" % rec.get("appid")):
            raise MigrationError("unrecognized legacy update policy: " + name)
        try:
            if install._ready_common(str(library)) is None:
                raise OSError("library unavailable")
        except OSError:
            conflicts.append("reconnect library " + str(library))
            continue
        if not path.exists():
            continue
        before = path.read_bytes()
        text = before.decode("utf-8", "surrogateescape")
        data = install._strict_manifest(text)
        app = vdf.iget(data, "AppState")
        if not isinstance(app, dict) or vdf.iget(app, "appid") != str(rec["appid"]) or vdf.dumps(data) != text:
            conflicts.append("unrecognized manifest " + name)
            continue
        key = next((key for key in app if key.lower() == "autoupdatebehavior"), None)
        if key and app[key] == "1":
            del app[key]
            if rec.get("key") is not None:
                app[rec["key"]] = rec["original"]
            changes[path] = (before, vdf.dumps(data).encode("utf-8", "surrogateescape"))
    if conflicts:
        raise MigrationError("legacy migration needs attention; no metadata changed: " + "; ".join(conflicts))
    if not changes:
        return ["Legacy metadata already restored; original records and backups retained"]
    backup = home / "backups/migration" / uuid.uuid4().hex
    backup.mkdir(parents=True)
    journal = []
    for index, (path, (before, after)) in enumerate(changes.items()):
        if path.is_symlink():
            raise MigrationError("refusing metadata symlink: " + str(path))
        saved = backup / str(index)
        _atomic(saved, before)
        journal.append({"path": str(path), "backup": str(saved), "before": _digest(saved),
                        "after": hashlib.sha256(after).hexdigest()})
    _write_json(backup / "journal.json", journal)
    for path, (before, after) in changes.items():
        if steam.is_running() or path.read_bytes() != before:
            raise MigrationError("Steam changed during migration; retry with Steam closed; journal: " + str(backup))
        mode = path.stat().st_mode & 0o777
        _atomic(path, after)
        path.chmod(mode)
        notes.append("Restored legacy settings in " + str(path))
    _write_json(home / "state/native-migration.json", {"schema": 1, "backup": str(backup)})
    return notes

"""Conservative stock-service attestation for optional Wine warm retention.

The native server must also revoke retention before late service starts/image
loads. These filesystem checks authorize only the initial neutral bootstrap.
Unknown state selects ordinary Wine shutdown; it never prevents playing a game.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

POLICY = "share/gluon/warm-service-policy.json"
CACHE = ".gluon-warm-policy-cache.json"
MAX_JSON = 4 * 1024 * 1024
MAX_REGISTRY = 32 * 1024 * 1024
MAX_FILES = 25000
CHECK_SECONDS = 0.5
HASH_BYTES = 128 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}")
_SECTION = re.compile(r"\[((?:\\.|[^\]\\])*)\](?:\s+\d+)?")
_VALUE = re.compile(r'("(?:\\.|[^"\\])*"|@)\s*=\s*(.*)')


class PolicyError(ValueError):
    pass


def _stamp(path):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise PolicyError("non-regular policy input")
    return [value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns]


def _read(path, limit):
    before = _stamp(path)
    if before[2] > limit:
        raise PolicyError("oversized policy input")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit or before != _stamp(path):
        raise PolicyError("policy input changed")
    return data


def _runtime_key(runtime):
    result = {}
    for name, filename in (("build", ".gluon-runtime-build-key"), ("patches", ".gluon-wine-patches")):
        value = _read(runtime / filename, 4096).decode("ascii").strip()
        if not _HEX.fullmatch(value):
            raise PolicyError("invalid runtime identity")
        result[name] = value
    return result


def _key_name(value):
    # Wine serializes path separators as \\; do not interpret arbitrary escapes.
    value = re.sub(r'\\([\\"])', r'\1', value)
    if "\\" in value:
        return value.replace("\\", "/").casefold()
    return value.casefold()


def _registry(path):
    if not path.exists():
        return {}
    text = _read(path, MAX_REGISTRY).decode("utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "WINE REGISTRY Version 2":
        raise PolicyError("unknown Wine registry format")
    sections, section, pending = {}, None, ""
    for raw in lines[1:]:
        line = raw.strip()
        if not pending and (not line or line.startswith(("#", ";"))):
            continue
        if line.endswith("\\"):
            pending += line[:-1]
            continue
        line, pending = pending + line, ""
        if line.startswith("["):
            match = _SECTION.fullmatch(line)
            if not match:
                raise PolicyError("ambiguous registry section")
            section = _key_name(match[1])
            if section in sections:
                raise PolicyError("duplicate registry section")
            sections[section] = {}
        elif line.startswith("#"):
            continue  # #time records are not executable configuration.
        else:
            match = _VALUE.fullmatch(line)
            if section is None or not match:
                raise PolicyError("ambiguous registry value")
            name = match[1] if match[1] == "@" else _key_name(match[1][1:-1])
            if name in sections[section]:
                raise PolicyError("duplicate registry value")
            sections[section][name] = match[2]
    if pending:
        raise PolicyError("truncated registry value")
    return sections


def _builtin_override(value):
    tokens = value.lower().split(",")
    return all(token.strip() in ("", "b", "builtin", "d", "disabled") for token in tokens)


def _environment_safe(runtime, environment=None):
    environment = os.environ if environment is None else environment
    if any(environment.get(key) for key in ("WINEDLLPATH", "WINEBUILDDIR", "WINEPATH")):
        return False
    for key, expected in (("WINESERVER", runtime / "bin/wineserver"), ("WINELOADER", runtime / "bin/wine")):
        if environment.get(key) and Path(environment[key]).resolve() != expected.resolve():
            return False
    for entry in environment.get("WINEDLLOVERRIDES", "").split(";"):
        if not entry.strip():
            continue
        name, equal, mode = entry.partition("=")
        if not equal or not name.strip() or not _builtin_override(mode):
            return False
    return True


def _registry_snapshot(prefix):
    paths = [prefix / name for name in ("system.reg", "user.reg", "userdef.reg")]
    stamps = [_stamp(path) if path.exists() else None for path in paths]
    if stamps[0] is None:
        raise PolicyError("prefix has no system registry")
    result = {}
    for index, path in enumerate(paths):
        for key, values in _registry(path).items():
            if key.endswith("/dlloverrides") and "/wine/" in key:
                for raw in values.values():
                    try:
                        value = json.loads(raw)
                    except ValueError as error:
                        raise PolicyError("unknown DLL override") from error
                    if not isinstance(value, str) or not _builtin_override(value):
                        raise PolicyError("native DLL override")
            service = re.match(r"system/(?:currentcontrolset|controlset[0-9]+)/services(?:/|$)", key)
            svchost = (key.startswith("software/") and
                       re.search(r"/microsoft/windows nt/currentversion/svchost(?:/|$)", key))
            # Stock service paths alone cannot authorize bootstrap: AppInit,
            # IFEO, COM and startup hooks can run code outside Windows before
            # the native post-seal image guard becomes active.
            execution = (key == "environment" or key.startswith("environment/")
                         or key.startswith("software/classes")
                         or re.match(r"system/(?:currentcontrolset|controlset[0-9]+)/control/session manager(?:/|$)", key)
                         or re.search(r"/microsoft/windows nt/currentversion/(?:windows|winlogon|image file execution options|silentprocessexit)(?:/|$)", key)
                         or re.search(r"/microsoft/windows/currentversion/(?:run|runonce|runservices|runservicesonce|policies/explorer/run|shellserviceobjectdelayload|app paths)(?:/|$)", key))
            if (index == 0 and (service or svchost)) or execution:
                result[str(index) + "/" + key] = values
            if re.search(r"/microsoft/windows/currentversion/explorer/(?:user shell folders|shell folders)$", key):
                result[str(index) + "/" + key] = {name: value for name, value in values.items()
                                                   if name in ("startup", "common startup")}
    if not result or stamps != [_stamp(path) if path.exists() else None for path in paths]:
        raise PolicyError("service registry changed or is absent")
    return result


def _startup_empty(prefix):
    # Wineboot processes the current user's Startup folder before a neutral
    # bootstrap can be sealed. A redirected folder is covered by the registry
    # snapshot; unknown entries in standard locations also force cold.
    users = prefix / 'drive_c/users'
    roots = list(users.iterdir()) if users.is_dir() else []
    if len(roots) > 32:
        return False
    roots.append(prefix / 'drive_c/ProgramData')
    for root in roots:
        if root.is_symlink() and not root.resolve().is_relative_to(users.resolve()):
            return False
        for relative in ('AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup',
                         'Start Menu/Programs/Startup', 'Microsoft/Windows/Start Menu/Programs/Startup'):
            path = root / relative
            if path.is_symlink() or path.exists() and (not path.is_dir() or any(path.iterdir())):
                return False
    return True


def _safe_relative(value):
    return (isinstance(value, str) and value and value == value.casefold()
            and not value.startswith("/") and "\\" not in value
            and all(part not in ("", ".", "..") for part in value.split("/")))


def _inventory(prefix, *, expected=None, cache=None, deadline=None, hash_budget=None):
    windows = prefix / "drive_c/windows"
    if windows.is_symlink() or not windows.is_dir():
        raise PolicyError("unknown Windows directory")
    files, count, hashed = {}, 0, 0
    for directory, dirs, names in os.walk(windows, followlinks=False):
        base = Path(directory)
        for name in dirs:
            if (base / name).is_symlink():
                raise PolicyError("linked Windows executable directory")
        for name in names:
            count += 1
            if count > MAX_FILES or deadline is not None and time.monotonic() > deadline:
                raise PolicyError("stock verification budget exceeded")
            path = base / name
            relative = path.relative_to(windows).as_posix().casefold()
            # The launcher invokes the exact packaged Steam helper. Historical
            # prefix copies are ordinary game helpers, never part of neutral
            # core bootstrap; late legacy system registration forces cold.
            if relative in ("system32/steam.exe", "syswow64/steam.exe"):
                continue
            stamp = _stamp(path)
            if not _safe_relative(relative) or relative in files:
                raise PolicyError("ambiguous executable path")
            saved = (cache or {}).get(relative)
            if (expected is not None and isinstance(saved, dict)
                    and saved.get("stamp") == stamp and saved.get("digest") == expected.get(relative)):
                files[relative] = saved["digest"]
                continue
            with path.open("rb") as source:
                if source.read(2) != b"MZ":
                    continue
                if expected is not None and relative not in expected:
                    raise PolicyError("non-stock Windows executable")
                source.seek(0)
                digest = hashlib.sha256()
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    hashed += len(block)
                    if ((hash_budget is not None and hashed > hash_budget)
                            or deadline is not None and time.monotonic() > deadline):
                        raise PolicyError("stock verification budget exceeded")
                    digest.update(block)
            value = digest.hexdigest()
            if stamp != _stamp(path) or expected is not None and value != expected[relative]:
                raise PolicyError("Windows executable changed")
            files[relative] = value
            if cache is not None:
                cache[relative] = {"stamp": stamp, "digest": value}
    return files


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(data, output, sort_keys=True)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _optional_bridge(runtime):
    result = {}
    for arch, directory in (("x86_64", "system32"), ("i386", "syswow64")):
        for name in ("lsteamclient.dll",):
            path = runtime / "lib/wine" / (arch + "-windows") / name
            if path.exists():
                result[directory + "/" + name] = hashlib.sha256(_read(path, 32 * 1024 * 1024)).hexdigest()
    return result


def build_stock_policy(runtime, prefix):
    """Capture only a fully stopped, pristine CI prefix for the exact runtime."""
    runtime, prefix = Path(runtime).resolve(), Path(prefix).resolve()
    if not _environment_safe(runtime) or not _startup_empty(prefix):
        raise PolicyError("stock bootstrap environment has native overrides")
    required = _inventory(prefix)
    optional = _optional_bridge(runtime)
    for path, digest in optional.items():
        if path in required and required[path] != digest:
            raise PolicyError("stock bridge differs from packaged runtime")
        required.pop(path, None)
    data = {"schema": 1, "runtime": _runtime_key(runtime),
            "registry": _registry_snapshot(prefix), "required": required, "optional": optional}
    encoded = json.dumps(data, sort_keys=True).encode()
    if len(encoded) > MAX_JSON or not required:
        raise PolicyError("empty or oversized stock policy")
    _write(runtime / POLICY, data)
    return data


def warm_policy_allows(runtime, prefix, *, environment=None) -> bool:
    runtime, prefix = Path(runtime), Path(prefix)
    cache, policy_hash = {}, None
    try:
        if not _environment_safe(runtime, environment) or not _startup_empty(prefix):
            return False
        raw = _read(runtime / POLICY, MAX_JSON)
        policy_hash = hashlib.sha256(raw).hexdigest()
        policy = json.loads(raw)
        if (not isinstance(policy, dict) or set(policy) != {"schema", "runtime", "registry", "required", "optional"}
                or type(policy["schema"]) is not int or policy["schema"] != 1
                or policy["runtime"] != _runtime_key(runtime)
                or policy["registry"] != _registry_snapshot(prefix)):
            return False
        for name in ("required", "optional"):
            values = policy[name]
            if (not isinstance(values, dict) or len(values) > MAX_FILES
                    or any(not _safe_relative(key) or not isinstance(value, str) or not _HEX.fullmatch(value)
                           for key, value in values.items())):
                return False
        if (not policy["required"] or policy["required"].keys() & policy["optional"].keys()
                or policy["optional"] != _optional_bridge(runtime)):
            return False
        try:
            path = prefix / CACHE
            info = path.lstat()
            if (info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077):
                saved = json.loads(_read(path, MAX_JSON))
                if saved.get("schema") == 1 and saved.get("policy") == policy_hash and isinstance(saved.get("files"), dict):
                    cache = saved["files"]
        except (OSError, ValueError, AttributeError):
            pass
        expected = {**policy["required"], **policy["optional"]}
        files = _inventory(prefix, expected=expected, cache=cache,
                           deadline=time.monotonic() + CHECK_SECONDS, hash_budget=HASH_BYTES)
        return policy["required"].keys() <= files.keys()
    except (OSError, ValueError, RuntimeError):
        return False
    finally:
        if policy_hash is not None and cache:
            try:
                _write(prefix / CACHE, {"schema": 1, "policy": policy_hash, "files": cache})
            except OSError:
                pass

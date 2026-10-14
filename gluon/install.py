"""Recover Steam's stale installed marker when a managed game's directory is gone.

Steam must be closed. Only a fully installed manifest with a confirmed missing
directory is parked beside the original; game files, prefixes and saves are never
removed. Ambiguous metadata, filesystem errors and pending installs are left alone.
"""
from __future__ import annotations

import os
import stat
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import steam, vdf


def _strict_manifest(text: str) -> Dict[str, Any]:
    """Reject incomplete or duplicate keys instead of guessing which value wins."""
    root: Dict[str, Any] = {}
    stack = [root]
    seen = [set()]
    key = None
    for kind, value in vdf._tokens(text.lstrip("\ufeff")):
        if kind == "str":
            if key is None:
                if value.lower() in seen[-1]:
                    raise vdf.VDFError("duplicate manifest key")
                seen[-1].add(value.lower())
                key = value
            else:
                stack[-1][key] = value
                key = None
        elif kind == "{":
            if key is None:
                raise vdf.VDFError("section without a name")
            child: Dict[str, Any] = {}
            stack[-1][key] = child
            stack.append(child)
            seen.append(set())
            key = None
        else:
            if len(stack) == 1 or key is not None:
                raise vdf.VDFError("incomplete manifest section")
            stack.pop()
            seen.pop()
    if len(stack) != 1 or key is not None:
        raise vdf.VDFError("incomplete manifest")
    return root


def _identity(info: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _ready_common(library: str) -> Optional[os.stat_result]:
    # A removed external volume can leave an empty mountpoint behind. Do not
    # interpret that directory as a mounted Steam library.
    if not os.path.isabs(library):
        return None
    normalized = os.path.normpath(library)
    parts = normalized.split(os.sep)
    if len(parts) > 2 and parts[1].lower() == "volumes":
        if not os.path.ismount(os.path.join(os.sep, parts[1], parts[2])):
            return None
    common = os.path.join(normalized, "steamapps", "common")
    info = os.lstat(common)
    if not stat.S_ISDIR(info.st_mode) or not os.access(common, os.R_OK | os.X_OK):
        return None
    # Force a directory read; a failed stat/permission check must not look like
    # an absent game. Do not enumerate or modify any game's contents.
    with os.scandir(common) as entries:
        next(entries, None)
    return info


def _install_leaf(app: Dict[str, Any], appid: int) -> Optional[str]:
    if vdf.iget(app, "appid") != str(appid) or vdf.iget(app, "StateFlags") != "4":
        return None
    leaf = vdf.iget(app, "installdir")
    if (not isinstance(leaf, str) or not leaf or leaf != leaf.strip()
            or leaf in (".", "..") or any(c in leaf for c in "/\\:\x00")
            or any(ord(c) < 32 for c in leaf)):
        return None
    return leaf


def _park_missing(library: str, appid: int) -> Optional[str]:
    common_info = _ready_common(library)
    if common_info is None:
        return None
    manifest = os.path.join(library, "steamapps", "appmanifest_%d.acf" % appid)
    before = os.lstat(manifest)
    if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
        return None
    fd = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        opened = os.fstat(handle.fileno())
        if _identity(opened) != _identity(before):
            return None
        data = _strict_manifest(handle.read())
    if len(data) != 1:
        return None
    app = vdf.iget(data, "AppState")
    if not isinstance(app, dict):
        return None
    leaf = _install_leaf(app, appid)
    if leaf is None:
        return None
    directory = os.path.join(library, "steamapps", "common", leaf)
    if os.path.lexists(directory):  # Includes dangling links and empty directories.
        return None
    try:
        os.stat(directory)
    except FileNotFoundError:
        pass
    else:
        return None
    # Recheck both the live library and manifest before the reversible rename.
    # A concurrent install/update or disappearing volume must stop recovery.
    current_common = _ready_common(library)
    if (current_common is None
            or (current_common.st_dev, current_common.st_ino)
            != (common_info.st_dev, common_info.st_ino)
            or _identity(os.lstat(manifest)) != _identity(opened)
            or os.path.lexists(directory) or steam.is_running()):
        return None
    parked = manifest + ".gluon-missing-" + uuid.uuid4().hex
    if os.path.lexists(parked):
        return None
    os.rename(manifest, parked)
    return "%d: recovered missing installation; manifest preserved at %s" % (appid, parked)


def recover_missing_installs(appids: Iterable[int],
                            libraries: Optional[Iterable[str]] = None) -> List[str]:
    """Park stale markers for explicitly managed app IDs, while Steam is closed."""
    if steam.is_running():
        return []
    try:
        roots = list(steam.library_dirs() if libraries is None else libraries)
    except (OSError, ValueError, TypeError):
        return []
    managed = sorted({a for a in appids if isinstance(a, int) and not isinstance(a, bool) and a > 0})
    notes = []
    for library in roots:
        for appid in managed:
            if steam.is_running():
                return notes
            try:
                note = _park_missing(library, appid)
            except (OSError, ValueError, TypeError):
                continue
            if note:
                notes.append(note)
    return notes

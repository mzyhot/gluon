"""Read-only recognition of PR #1's recorded legacy Cloud mappings.

Used solely to restore exact old appinfo values during migration. New Cloud
support uses native compatibility-tool prefix views and never writes appinfo.
"""
from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional, Tuple

from . import steam

# Where each Windows root lies inside a Wine user profile.
WINDOWS_ROOTS = {
    "winmydocuments": "Documents",
    "winappdatalocal": "AppData/Local",
    "winappdatalocallow": "AppData/LocalLow",
    "winappdataroaming": "AppData/Roaming",
    "winsavedgames": "Saved Games",
}
GAME_INSTALL = "gameinstall"
OVERRIDE_KEYS = {"root", "os", "oscompare", "useinstead", "addpath", "pathtransforms"}


class Unmappable(ValueError):
    pass


def _values(section: Any) -> List[Any]:
    return list(section.values()) if isinstance(section, dict) else []


def _platforms(rule: Dict[str, Any]) -> List[str]:
    return [str(p).strip().lower() for p in _values(rule.get("platforms"))]


def _runs_on_mac(rule: Dict[str, Any]) -> bool:
    platforms = _platforms(rule)
    return not platforms or "macos" in platforms or "all" in platforms


def _for_windows_game(rule: Dict[str, Any]) -> bool:
    """A rule Steam evaluates on a Mac for files the Windows build writes. Rules for
    the Mac build alone (a game whose Mac build is 32-bit) keep their own locations."""
    platforms = _platforms(rule)
    return _runs_on_mac(rule) and (not platforms or "windows" in platforms or "all" in platforms)


def _is_os(override: Dict[str, Any], name: str) -> bool:
    return str(override.get("os", "")).lower() == name


def _settings(appid: int) -> Dict[str, str]:
    """Gluon's settings files, in the launcher's order: the game's file wins."""
    values: Dict[str, str] = {}
    for path in (os.path.join(steam.GLUON_HOME, "settings.env"),
                 os.path.join(steam.GLUON_HOME, "games", "%d.env" % appid)):
        try:
            with open(path) as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            # The launcher exports each NAME=value line as written.
            name, sep, value = line.partition("=")
            if sep and name[:1].isalpha() and name.replace("_", "").isalnum():
                values[name] = value
    return values


def enabled(appid: int) -> bool:
    return _settings(appid).get("GLUON_CLOUD", "1") != "0"


def _library(appid: int) -> str:
    for library in steam.library_dirs():
        if os.path.isfile(os.path.join(library, "steamapps", "appmanifest_%d.acf" % appid)):
            return library
    # Not installed yet: Steam installs into its default library unless told otherwise.
    return steam.STEAM_ROOT


def _runtime(appid: int) -> Tuple[str, Optional[str]]:
    """(kind, path) of the runtime the launcher will pick, as select_runtime does."""
    name = _settings(appid).get("GLUON_RUNTIME", "")
    runtime_dir = os.path.join(steam.GLUON_HOME, "runtime")
    candidates = [name, os.path.join(runtime_dir, name)] if name else []
    candidates.append(os.path.join(runtime_dir, "current"))
    for candidate in candidates:
        if os.access(os.path.join(candidate, "proton"), os.X_OK) and \
                os.access(os.path.join(candidate, "files", "bin", "wine"), os.X_OK):
            return "proton", candidate
        if os.access(os.path.join(candidate, "bin", "wine"), os.X_OK):
            return "wine", candidate
    return "wine", None


def profile_dir(appid: int) -> str:
    """The Wine user profile the game's saves live under."""
    kind, runtime = _runtime(appid)
    compat = os.path.join(_library(appid), "steamapps", "compatdata", str(appid))
    prefix = os.path.join(compat, "pfx" if kind == "proton" else "pfx-wine")
    users = os.path.join(prefix, "drive_c", "users")
    try:
        names = sorted(n for n in os.listdir(users) if n != "Public" and not n.startswith(".")
                       and os.path.isdir(os.path.join(users, n)))
    except OSError:
        names = []
    if len(names) > 1:
        preferred = [n for n in names if n in ("crossover", "steamuser")]
        if len(preferred) != 1:
            raise Unmappable("the game's Wine prefix has more than one user profile")
        names = preferred
    if names:
        user = names[0]
    elif kind == "proton":
        user = "steamuser"
    elif runtime and os.path.exists(os.path.join(runtime, ".gluon-wine-patches")):
        # Gluon's runtime is built from CrossOver's Wine, which names its user crossover.
        user = "crossover"
    else:
        user = os.environ.get("USER") or os.path.basename(os.path.expanduser("~"))
    return os.path.join(users, user)


def _mac_root(path: str) -> Tuple[str, str]:
    """A Steam macOS root and the path below it."""
    home = os.path.expanduser("~").rstrip("/")
    support = os.path.join(home, "Library", "Application Support")
    for root, base in (("MacAppSupport", support), ("MacHome", home)):
        if path.startswith(base + "/"):
            return root, path[len(base) + 1:]
    raise Unmappable("the game's Wine prefix is outside the home folder")


def _join(*parts: str) -> str:
    """Steam path fragments: forward slashes, no leading slash, a trailing one."""
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return joined + "/" if joined else ""


def is_gluon_mapping(ufs: Any) -> bool:
    """A cloud section rewritten by Gluon: a macOS override into a Wine prefix."""
    if not isinstance(ufs, dict):
        return False
    return any(isinstance(o, dict) and _is_os(o, "macos") and "/drive_c/users/" in "/" + str(o.get("addpath", ""))
               for o in _values(ufs.get("rootoverrides")))


def mapped(ufs: Any, appid: int, profile: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The cloud section to use on this Mac, or None to keep Steam Cloud off.

    Idempotent: Gluon's own overrides are replaced, so a rewritten section maps
    to itself while the game's prefix stays where it is.
    """
    if not isinstance(ufs, dict) or not enabled(appid):
        return None
    savefiles = [r for r in _values(ufs.get("savefiles")) if isinstance(r, dict)]
    overrides = [o for o in _values(ufs.get("rootoverrides"))]
    if not savefiles and not overrides:
        # Steam API files sync on every platform.
        return copy.deepcopy(ufs)
    try:
        if any(not isinstance(o, dict) or set(o) - OVERRIDE_KEYS or
               str(o.get("oscompare", "=")) != "=" for o in overrides):
            raise Unmappable("an override Gluon does not understand")
        windows = {}
        for o in overrides:
            if _is_os(o, "windows"):
                key = str(o.get("root", "")).lower()
                if key in windows:
                    raise Unmappable("two Windows overrides for one root")
                windows[key] = o
        profile = profile or profile_dir(appid)
        generated: Dict[str, Dict[str, Any]] = {}
        for rule in savefiles:
            if not _for_windows_game(rule):
                continue  # Skipped by Steam here, or the Mac build's own location.
            root = str(rule.get("root", ""))
            if root.lower() in generated:
                continue
            # Where the Windows client keeps this rule's files.
            override = windows.get(root.lower()) or {}
            target = str(override.get("useinstead", root))
            addpath = str(override.get("addpath", "") or "")
            if target.lower() == GAME_INSTALL:
                if root.lower() == GAME_INSTALL and not addpath and not override.get("pathtransforms"):
                    continue  # The install folder is the same on a Mac.
                raise Unmappable("a rule moved within or into the install folder")
            if target.lower() not in WINDOWS_ROOTS:
                raise Unmappable("save root %s has no Windows location" % target)
            mac_root, below = _mac_root(os.path.join(profile, WINDOWS_ROOTS[target.lower()]))
            item: Dict[str, Any] = {"root": root, "os": "MacOS", "oscompare": "=",
                                    "useinstead": mac_root, "addpath": _join(below, addpath)}
            if override.get("pathtransforms"):
                item["pathtransforms"] = copy.deepcopy(override["pathtransforms"])
            generated[root.lower()] = item
    except Unmappable:
        return None
    result = copy.deepcopy(ufs)
    # The developer's macOS overrides describe a Mac build the Windows game does not
    # use; those for roots of Mac-only rules still apply to them.
    windows_roots = {str(r.get("root", "")).lower() for r in savefiles if _for_windows_game(r)}
    kept = [o for o in overrides if not _is_os(o, "macos") or str(o.get("root", "")).lower() not in windows_roots]
    combined = kept + [generated[k] for k in sorted(generated)]
    if combined:
        result["rootoverrides"] = {str(i): copy.deepcopy(o) for i, o in enumerate(combined)}
    else:
        result.pop("rootoverrides", None)
    return result


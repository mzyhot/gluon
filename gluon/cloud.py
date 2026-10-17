"""Steam Cloud for Windows games run through Gluon.

Steam evaluates an app's cloud rules for the platform it runs on. On a Mac, the
Windows save roots of a Windows-only game (WinAppDataLocalLow, WinMyDocuments,
...) resolve nowhere, and a cross-platform rule that does not resolve can make
Steam forget the user's cloud files after a session.

Steamworks root overrides map a root to another location on one OS. For every
cross-platform rule, Gluon adds a macOS override that points the rule's Windows
location into the game's Wine prefix, where the game writes it. Rules restricted
to another OS are left as they are: Steam skips them on a Mac and leaves their
files in the cloud alone. Cloud files written only through the Steam API sync on
every platform and need no mapping. An app with a rule Gluon cannot place keeps
Steam Cloud off on this Mac, as before.
"""
from __future__ import annotations

import copy
import os
import shutil
import time
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
BACKUP_LIMIT = 256 * 1024 * 1024


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


def _rule_dirs(ufs: Dict[str, Any], appid: int) -> List[str]:
    """Local folders of the rules that sync on this Mac, as Steam resolves them."""
    install = None
    library = _library(appid)
    manifest = os.path.join(library, "steamapps", "appmanifest_%d.acf" % appid)
    try:
        from . import vdf
        installdir = vdf.load(manifest).get("AppState", {}).get("installdir")
        if installdir:
            install = os.path.join(library, "steamapps", "common", installdir)
    except (OSError, ValueError, AttributeError):
        pass
    mac_roots = {"machome": os.path.expanduser("~"),
                 "macappsupport": os.path.expanduser("~/Library/Application Support")}
    overrides = {str(o.get("root", "")).lower(): o for o in _values(ufs.get("rootoverrides"))
                 if isinstance(o, dict) and _is_os(o, "macos")}
    out = []
    for rule in _values(ufs.get("savefiles")):
        if not isinstance(rule, dict) or not _runs_on_mac(rule):
            continue
        root = str(rule.get("root", "")).lower()
        path = str(rule.get("path", "")).replace("{64BitSteamID}", "*").replace("{Steam3AccountID}", "*")
        override = overrides.get(root)
        if override:
            base = mac_roots.get(str(override.get("useinstead", "")).lower())
            if not base:
                continue
            for transform in _values(override.get("pathtransforms")):
                if isinstance(transform, dict):
                    path = path.replace(str(transform.get("find", "")), str(transform.get("replace", "")), 1)
            base = os.path.join(base, str(override.get("addpath", "")))
        elif root == GAME_INSTALL and install:
            base = install
        else:
            continue
        folder = os.path.normpath(os.path.join(base, path.split("*")[0]))
        if folder not in out:
            out.append(folder)
    return out


def backup(ufs: Dict[str, Any], appid: int, now: Optional[float] = None) -> List[str]:
    """Copy the game's local cloud files and Steam's cloud records before Gluon
    turns Steam Cloud on for it. Bounded: folders past the size limit are skipped
    and named."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    target = os.path.join(steam.GLUON_HOME, "backups", "cloud", "%d-%s" % (appid, stamp))
    sources = []
    userdata = os.path.join(steam.STEAM_ROOT, "userdata")
    for user in sorted(os.listdir(userdata)) if os.path.isdir(userdata) else []:
        folder = os.path.join(userdata, user, str(appid))
        if user.isdigit() and os.path.isdir(folder):
            sources.append(("steam-userdata/%s" % user, folder))
    for index, folder in enumerate(_rule_dirs(ufs, appid)):
        if os.path.isdir(folder):
            sources.append(("rule-%d" % index, folder))
    notes: List[str] = []
    used = 0
    copied = False
    for name, folder in sources:
        size = 0
        for base, _, files in os.walk(folder):
            for file in files:
                try:
                    size += os.lstat(os.path.join(base, file)).st_size
                except OSError:
                    pass
        if used + size > BACKUP_LIMIT:
            notes.append("%d: cloud backup skipped %s (%d MB, over the limit)" % (appid, folder, size >> 20))
            continue
        shutil.copytree(folder, os.path.join(target, name), symlinks=True)
        used += size
        copied = True
    if copied:
        with open(os.path.join(target, "SOURCES.txt"), "w") as f:
            f.writelines("%s\t%s\n" % item for item in sources)
        notes.append("%d: cloud backup %s" % (appid, target))
    return notes

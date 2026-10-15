"""Give each Steam game its own macOS app bundle and session.

LaunchServices launches the bundle, so macOS brings the game to the front, names
it after the game and lets Game Mode recognize it. The bundle's executable
starts Wine's loader from inside the bundle, where Wine keeps every process of
the game. Bundles live under a .noindex folder so Spotlight does not offer
them, and the executable refuses to start without the one-time session file
that only a Steam launch through Gluon writes.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import sys
import uuid
from typing import Mapping, Optional

from . import vdf

EXECUTABLE = "gluon-game"
SESSION = "session.env"
# Wine and Gluon variables reach the game through the session file. These do
# not describe the session and must not override what LaunchServices sets.
SKIPPED = {"_", "SHLVL", "PWD", "OLDPWD", "GLUON_LAUNCHER_HOST", "TERM_SESSION_ID"}
SKIPPED_PREFIXES = ("__CF", "XPC_", "BASH_FUNC_", "DYLD_")
SCRIPT = """#!/bin/bash
# Started by LaunchServices for a Steam launch through Gluon. The session file
# is written for one launch and removed here; without it, nothing starts.
here="$(cd "$(dirname "$0")" && pwd)"
session="$here/../../../%(session)s"
if [ ! -f "$session" ]; then
    echo "Start this game from Steam." >&2
    exit 64
fi
. "$session"
rm -f "$session"
exec >> "${GLUON_WINE_LOG:-/dev/null}" 2>&1
cd "$GLUON_GAME_CWD" || exit 66
# Gluon's Wine gives GLUON_OVERLAY_INSERT to game processes only. A runtime
# without that patch receives Steam's overlay through dyld for its first process.
[ -z "${GLUON_DYLD_OVERLAY:-}" ] || export DYLD_INSERT_LIBRARIES="$GLUON_DYLD_OVERLAY"
unset GLUON_DYLD_OVERLAY
exec "$here/wine" "$@"
""" % {"session": SESSION}


def game_name(steamapps: Optional[str], appid: str, fallback: str) -> str:
    """The name Steam shows for the game, from its app manifest."""
    if steamapps:
        try:
            manifest = vdf.load(os.path.join(steamapps, "appmanifest_%s.acf" % appid))
            name = vdf.iget(manifest, "AppState", "name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (OSError, ValueError):
            pass
    return fallback.strip() or "Steam Game"


def bundle_file_name(name: str) -> str:
    cleaned = re.sub(r"[/:\x00-\x1f]", "", name).strip().lstrip(".")
    return (cleaned[:100].rstrip() or "Steam Game") + ".app"


def info_plist(appid: str, name: str) -> bytes:
    identifier = re.sub(r"[^A-Za-z0-9-]", "-", appid) or "game"
    return plistlib.dumps({
        "CFBundleDevelopmentRegion": "English",
        "CFBundleDisplayName": name,
        "CFBundleExecutable": EXECUTABLE,
        "CFBundleIdentifier": "org.gluon.game." + identifier,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSApplicationCategoryType": "public.app-category.games",
        # Wine starts every process as an agent and makes only processes with
        # windows regular apps, so background helpers get no Dock icons.
        "LSUIElement": True,
        "NSPrincipalClass": "WineApplication",
    })


def _current(bundle: Path, plist: bytes, loader: Path, ntdll: Path) -> bool:
    macos = bundle / "Contents/MacOS"
    try:
        return ((bundle / "Contents/Info.plist").read_bytes() == plist
                and (macos / EXECUTABLE).read_text() == SCRIPT
                and os.access(macos / EXECUTABLE, os.X_OK)
                and os.path.samefile(macos / "wine", loader)
                and os.readlink(macos / "ntdll.so") == str(ntdll))
    except OSError:
        return False


def _build(stage: Path, plist: bytes, loader: Path, ntdll: Path) -> None:
    macos = stage / "Contents/MacOS"
    macos.mkdir(parents=True)
    (stage / "Contents/Info.plist").write_bytes(plist)
    script = macos / EXECUTABLE
    script.write_text(SCRIPT)
    script.chmod(0o755)
    # A hard link keeps the bundle's loader identical to the runtime's; a copy
    # is the fallback when the two are on different volumes.
    try:
        os.link(loader, macos / "wine")
    except OSError:
        shutil.copy2(loader, macos / "wine")
    os.symlink(ntdll, macos / "ntdll.so")


def prepare(home: str, appid: str, name: str, loader: str, ntdll: str) -> Path:
    """Create or refresh the game's bundle and return its path."""
    loader_path, ntdll_path = Path(loader), Path(ntdll)
    if not os.access(loader_path, os.X_OK) or not ntdll_path.is_file():
        raise ValueError("runtime loader or ntdll.so is missing")
    root = Path(home) / "apps.noindex" / appid
    root.mkdir(parents=True, exist_ok=True)
    bundle = root / bundle_file_name(name)
    plist = info_plist(appid, name)
    with open(root / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not _current(bundle, plist, loader_path, ntdll_path):
            work = root / (".build-" + uuid.uuid4().hex)
            try:
                _build(work / bundle.name, plist, loader_path, ntdll_path)
                if bundle.exists():
                    retired = root / (".old-" + uuid.uuid4().hex)
                    bundle.rename(retired)
                    shutil.rmtree(retired, ignore_errors=True)
                (work / bundle.name).rename(bundle)
            finally:
                shutil.rmtree(work, ignore_errors=True)
        # A renamed game leaves its old bundle behind; keep only the current one.
        for other in root.glob("*.app"):
            if other != bundle:
                shutil.rmtree(other, ignore_errors=True)
    return bundle


def write_session(bundle: Path, environment: Mapping[str, str], cwd: str, wine_log: str,
                  overlay: str, scoped: bool = True) -> Path:
    """Write the one-time environment the bundle's executable loads."""
    lines = []
    values = {key: value for key, value in environment.items()
              if key not in ("GLUON_OVERLAY_INSERT", "GLUON_DYLD_OVERLAY")}
    values.update(GLUON_GAME_CWD=cwd, GLUON_WINE_LOG=wine_log)
    if overlay:
        values["GLUON_OVERLAY_INSERT" if scoped else "GLUON_DYLD_OVERLAY"] = overlay
    for key in sorted(values):
        if key in SKIPPED or key.startswith(SKIPPED_PREFIXES) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        lines.append("export %s=%s\n" % (key, shlex.quote(values[key])))
    session = bundle.parent / SESSION
    temporary = bundle.parent / (".%s.%s" % (SESSION, uuid.uuid4().hex))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.writelines(lines)
    os.replace(temporary, session)
    return session


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a game's app bundle and launch session")
    parser.add_argument("--home", required=True)
    parser.add_argument("--appid", required=True)
    parser.add_argument("--steamapps")
    parser.add_argument("--fallback-name", required=True)
    parser.add_argument("--loader", required=True)
    parser.add_argument("--ntdll", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--wine-log", required=True)
    parser.add_argument("--overlay", default="")
    parser.add_argument("--overlay-scoped", action="store_true",
                        help="the runtime's Wine limits GLUON_OVERLAY_INSERT to game processes")
    args = parser.parse_args(argv)
    try:
        name = game_name(args.steamapps, args.appid, args.fallback_name)
        bundle = prepare(args.home, args.appid, name, args.loader, args.ntdll)
        write_session(bundle, os.environ, args.cwd, args.wine_log, args.overlay, args.overlay_scoped)
    except (OSError, ValueError) as error:
        print("gluon: app bundle unavailable: %s" % error, file=sys.stderr)
        return 1
    print(bundle)
    return 0


if __name__ == "__main__":
    sys.exit(main())

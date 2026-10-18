"""Give each Steam game its own macOS app bundle.

LaunchServices launches the bundle, so macOS brings the game to the front, names
it after the game and lets Game Mode recognize it. The bundle's executable is a
hard link of the runtime's Wine loader: macOS treats a process as part of a
bundle only when it runs the bundle's declared executable, and Wine keeps every
process of the game on that loader. Bundles live under a .noindex folder so
Spotlight does not offer them; opened by hand, the loader has no program to run
and exits.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
import uuid
from typing import Optional

from . import vdf

EXECUTABLE = "wine"


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
                and os.path.samefile(macos / EXECUTABLE, loader)
                and os.readlink(macos / "ntdll.so") == str(ntdll)
                and sorted(p.name for p in macos.iterdir()) == sorted([EXECUTABLE, "ntdll.so"]))
    except OSError:
        return False


def _build(stage: Path, plist: bytes, loader: Path, ntdll: Path) -> None:
    macos = stage / "Contents/MacOS"
    macos.mkdir(parents=True)
    (stage / "Contents/Info.plist").write_bytes(plist)
    # A hard link keeps the bundle's loader identical to the runtime's; a copy
    # is the fallback when the two are on different volumes.
    try:
        os.link(loader, macos / EXECUTABLE)
    except OSError:
        shutil.copy2(loader, macos / EXECUTABLE)
    # The loader loads ntdll.so from its own folder.
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
        # Earlier launches left a session file that nothing reads any more.
        (root / "session.env").unlink(missing_ok=True)
    return bundle


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a game's app bundle")
    parser.add_argument("--home", required=True)
    parser.add_argument("--appid", required=True)
    parser.add_argument("--steamapps")
    parser.add_argument("--fallback-name", required=True)
    parser.add_argument("--loader", required=True)
    parser.add_argument("--ntdll", required=True)
    args = parser.parse_args(argv)
    try:
        name = game_name(args.steamapps, args.appid, args.fallback_name)
        bundle = prepare(args.home, args.appid, name, args.loader, args.ntdll)
    except (OSError, ValueError) as error:
        print("gluon: app bundle unavailable: %s" % error, file=sys.stderr)
        return 1
    print(bundle)
    return 0


if __name__ == "__main__":
    sys.exit(main())

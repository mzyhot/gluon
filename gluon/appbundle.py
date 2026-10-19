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
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import uuid
from typing import Optional

from . import vdf

EXECUTABLE = "wine"
ICON = "Game.icns"
ICON_RECORD = ".gluon-icon.json"


def steam_icon(root: str, appid: str) -> Optional[Path]:
    """Use Steam's cached square client icon, without fetching artwork at Play."""
    cache = Path(root) / 'appcache/librarycache'
    candidates = [cache / (appid + '_icon.jpg'), cache / (appid + '_icon.png')]
    folder = cache / appid
    if folder.is_dir():
        candidates += sorted(p for p in folder.iterdir()
                             if re.fullmatch(r'[a-fA-F0-9]{40}\.(jpg|png|ico)', p.name))
    return next((p for p in candidates if p.is_file() and not p.is_symlink()), None)


def _icon_key(source: Optional[Path]):
    if source is None:
        return None
    return {'path': str(source.resolve()), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}


def _build_icon(source: Path, resources: Path) -> bool:
    resources.mkdir(parents=True, exist_ok=True)
    iconset = resources / 'Game.iconset'
    iconset.mkdir()
    try:
        # The cached Steam icon is normally 32x32. Supply standard and retina
        # representations for the system's small Game Mode and menu icons.
        for pixels, name in ((32, 'icon_32x32.png'), (64, 'icon_32x32@2x.png'),
                             (128, 'icon_128x128.png')):
            subprocess.run(['/usr/bin/sips', '-z', str(pixels), str(pixels), '-s', 'format', 'png',
                            str(source), '--out', str(iconset / name)],
                           check=True, capture_output=True, timeout=10)
        subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o', str(resources / ICON)],
                       check=True, capture_output=True, timeout=10)
        return (resources / ICON).is_file()
    except (OSError, subprocess.SubprocessError) as error:
        print('gluon: cached game icon could not be converted: %s' % error, file=sys.stderr)
        (resources / ICON).unlink(missing_ok=True)
        return False
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


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


def info_plist(appid: str, name: str, has_icon: bool = False) -> bytes:
    identifier = re.sub(r"[^A-Za-z0-9-]", "-", appid) or "game"
    info = {
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
    }
    if has_icon:
        info['CFBundleIconFile'] = ICON
        info['CFBundleVersion'] = '2'
    return plistlib.dumps(info)


def _current(bundle: Path, appid: str, name: str, loader: Path, ntdll: Path, icon_key) -> bool:
    macos = bundle / "Contents/MacOS"
    try:
        resources = bundle / 'Contents/Resources'
        record_path = resources / ICON_RECORD
        record = json.loads(record_path.read_text()) if record_path.is_file() else {'source': None, 'icon': False}
        has_icon = record.get('icon') is True
        return (record.get('source') == icon_key
                and (not has_icon or (resources / ICON).is_file())
                and (bundle / "Contents/Info.plist").read_bytes() == info_plist(appid, name, has_icon)
                and os.path.samefile(macos / EXECUTABLE, loader)
                and os.readlink(macos / "ntdll.so") == str(ntdll)
                and sorted(p.name for p in macos.iterdir()) == sorted([EXECUTABLE, "ntdll.so"]))
    except (OSError, ValueError, TypeError):
        return False


def _build(stage: Path, appid: str, name: str, loader: Path, ntdll: Path,
           icon_source: Optional[Path], icon_key) -> None:
    macos = stage / "Contents/MacOS"
    macos.mkdir(parents=True)
    resources = stage / 'Contents/Resources'
    has_icon = _build_icon(icon_source, resources) if icon_source else False
    (stage / "Contents/Info.plist").write_bytes(info_plist(appid, name, has_icon))
    if icon_source:
        resources.mkdir(parents=True, exist_ok=True)
        (resources / ICON_RECORD).write_text(json.dumps({'source': icon_key, 'icon': has_icon}))
    # A hard link keeps the bundle's loader identical to the runtime's; a copy
    # is the fallback when the two are on different volumes.
    try:
        os.link(loader, macos / EXECUTABLE)
    except OSError:
        shutil.copy2(loader, macos / EXECUTABLE)
    # The loader loads ntdll.so from its own folder.
    os.symlink(ntdll, macos / "ntdll.so")


def prepare(home: str, appid: str, name: str, loader: str, ntdll: str,
            icon_source: Optional[Path] = None) -> Path:
    """Create or refresh the game's bundle and return its path."""
    loader_path, ntdll_path = Path(loader), Path(ntdll)
    if not os.access(loader_path, os.X_OK) or not ntdll_path.is_file():
        raise ValueError("runtime loader or ntdll.so is missing")
    root = Path(home) / "apps.noindex" / appid
    root.mkdir(parents=True, exist_ok=True)
    bundle = root / bundle_file_name(name)
    icon_key = _icon_key(icon_source)
    with open(root / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not _current(bundle, appid, name, loader_path, ntdll_path, icon_key):
            work = root / (".build-" + uuid.uuid4().hex)
            try:
                _build(work / bundle.name, appid, name, loader_path, ntdll_path, icon_source, icon_key)
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
        steam_root = os.environ.get('STEAM_ROOT', str(Path.home() / 'Library/Application Support/Steam'))
        bundle = prepare(args.home, args.appid, name, args.loader, args.ntdll,
                         steam_icon(steam_root, args.appid))
    except (OSError, ValueError) as error:
        print("gluon: app bundle unavailable: %s" % error, file=sys.stderr)
        return 1
    print(bundle)
    return 0


if __name__ == "__main__":
    sys.exit(main())

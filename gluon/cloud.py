"""Prepare native AutoCloud views before Steam resolves a game's save paths.

Steam's Proton-compatible Cloud layout uses pfx/drive_c/users/steamuser.
Keep Gluon's real pfx-wine and its existing profile, and add verified aliases.
Conflicting prefixes and links outside the profile require manual reconciliation.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import appinfo, steam, steamplay

MARKER = '.gluon-native-cloud.json'
FOLDERS = ('Documents', 'Desktop', 'Downloads', 'Music', 'Pictures', 'Videos',
           'Templates', 'Saved Games', 'AppData/Local', 'AppData/LocalLow', 'AppData/Roaming')
ALIASES = {'My Documents': 'Documents', 'Application Data': 'AppData/Roaming',
           'Local Settings/Application Data': '../AppData/Local'}
SHARED_FOLDERS = ('drive_c/ProgramData', 'drive_c/users/Public/Documents')
HOST_FOLDERS = {'Documents': 'Documents', 'Desktop': 'Desktop', 'Downloads': 'Downloads',
                'Music': 'Music', 'Pictures': 'Pictures', 'Videos': 'Movies'}
INDEX = 'cloud-index.json'


class CloudError(RuntimeError):
    pass


class WineBusy(CloudError):
    pass


def _inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def _layout(compat):
    prefix = compat / 'pfx-wine'
    if prefix.is_symlink() or (prefix.exists() and not prefix.is_dir()):
        raise CloudError('Cloud preparation requires an ordinary pfx-wine directory')
    view = compat / 'pfx'
    if os.path.lexists(view) and not (view.is_symlink() and os.readlink(view) == 'pfx-wine'):
        raise CloudError('A separate pfx already exists; it and all saves were left unchanged')
    users = prefix / 'drive_c/users'
    for path in (prefix / 'drive_c', users):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise CloudError('Cloud profile ancestors must be ordinary directories')
    for name in SHARED_FOLDERS:
        path = prefix / name
        for ancestor in (path, *path.parents):
            if ancestor == prefix:
                break
            if ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir()):
                raise CloudError('Shared Cloud root is conflicting; files were left unchanged: ' + str(ancestor))
    profiles = [p for p in users.iterdir() if p.name != 'Public' and not p.name.startswith('.')
                and p.is_dir() and not p.is_symlink()] if users.exists() else []
    if len(profiles) > 1:
        raise CloudError('Multiple Windows user profiles need reconciliation; no saves were moved')
    profile = profiles[0] if profiles else users / 'steamuser'
    if profile.is_symlink() or (profile.exists() and not profile.is_dir()):
        raise CloudError('Cloud profile must be an ordinary directory')
    for name in ('steamuser', 'crossover'):
        alias = users / name
        if os.path.lexists(alias) and alias != profile and not (
                alias.is_symlink() and alias.resolve() == profile.resolve()):
            raise CloudError(name + ' already refers to another profile')
    for name in FOLDERS:
        path = profile / name
        for ancestor in (path, *path.parents):
            if ancestor == profile:
                break
            # Older Gluon Wine prefixes deliberately share the Mac Documents
            # directory. Keep that location; moving it would strand existing saves.
            host_name = HOST_FOLDERS.get(name)
            known_host_folder = (host_name and ancestor == profile / name and
                                 ancestor.resolve() == (Path.home() / host_name).resolve())
            if ancestor.is_symlink() and not known_host_folder:
                raise CloudError('Cloud folder is a link; its files were left unchanged: ' + str(ancestor))
            if ancestor.exists() and not ancestor.is_dir():
                raise CloudError('Cloud folder is not a directory: ' + str(ancestor))
    for old, target in ALIASES.items():
        path = profile / old
        expected = path.parent / target
        if os.path.lexists(path) and not (path.is_symlink() and path.resolve() == expected.resolve()):
            raise CloudError('Legacy save folder needs reconciliation; left unchanged: ' + str(path))
        if path.parent.is_symlink() or not _inside(path.parent, profile):
            raise CloudError('Legacy save folder parent leaves the profile')
    return prefix, users, profile


def check(compat):
    if Path(compat).is_symlink():
        raise CloudError('Compatibility directory links are ambiguous; saves were left unchanged')
    compat = Path(compat).resolve()
    marker = compat / MARKER
    if marker.is_symlink() or not marker.is_file():
        raise CloudError('Native Cloud prefix has not passed automatic preparation; restart Steam')
    data = json.loads(marker.read_text())
    prefix, users, profile = _layout(compat)
    if (data != {'schema': 1, 'profile': str(profile.relative_to(prefix))}
            or not (compat / 'pfx').is_symlink()
            or not (users / 'steamuser').is_dir()
            or (users / 'steamuser').resolve() != profile.resolve()
            or not (users / 'crossover').is_dir()
            or (users / 'crossover').resolve() != profile.resolve()
            or any(not (profile / old).is_symlink()
                   or (profile / old).resolve() != ((profile / old).parent / target).resolve()
                   for old, target in ALIASES.items())
            or any(not (profile / name).is_dir() for name in FOLDERS)
            or any(not (prefix / name).is_dir() for name in SHARED_FOLDERS)):
        raise CloudError('Native Cloud layout changed; conflicting prefixes were left unchanged')
    return profile


def _candidates(appid):
    libraries = list(dict.fromkeys(Path(p).resolve() for p in steam.library_dirs()))
    installed = [p for p in libraries if (p / 'steamapps' / ('appmanifest_' + appid + '.acf')).is_file()]
    if len(installed) != 1:
        raise CloudError('Expected one installed game/library; conflicting or disconnected libraries were left unchanged')
    compat = installed[0] / 'steamapps/compatdata' / appid
    if compat.is_symlink():
        raise CloudError('Compatibility directory links are ambiguous; saves were left unchanged')
    # Steam's native Cloud root is library-relative. Do not let the launcher
    # silently select a different old prefix after Steam has synchronized it.
    for old in [p / 'steamapps/compatdata' / appid for p in libraries] + [Path(steam.GLUON_HOME) / 'compatdata' / appid]:
        if old != compat and any(os.path.lexists(old / name) for name in ('pfx', 'pfx-wine')):
            raise CloudError('Another library or legacy prefix has saves; automatic selection is ambiguous')
    return compat


def _document_backups(appid, profile):
    """Back up affected shared Documents subtrees, without copying unrelated documents.

    This is conservative backup selection only. Steam evaluates the original
    rules, variables, patterns and conflicts itself; no appinfo is ever rewritten.
    """
    documents = profile / 'Documents'
    if not documents.is_symlink():
        return []
    entry = appinfo.AppInfoFile(steam.APPINFO).get(int(appid))
    if entry is None:
        raise CloudError('Cannot protect shared Documents without this game\'s Cloud metadata')
    ufs = entry.kv['appinfo'].get('ufs', {})
    overrides = list(ufs.get('rootoverrides', {}).values())
    paths = set()
    for rule in ufs.get('savefiles', {}).values():
        root = str(rule.get('root', '')).lower()
        # Include every possible Windows Documents override, even when the
        # developer's platform filter would exclude it on this installation.
        targets = [('', root)] + [(str(o.get('addpath', '')), str(o.get('useinstead', root)).lower())
                                for o in overrides if str(o.get('root', '')).lower() == root]
        for extra, target in targets:
            if target != 'winmydocuments':
                continue
            if rule.get('pathtransforms') or any(o.get('pathtransforms') for o in overrides):
                raise CloudError('Transformed Cloud paths in shared Documents need reconciliation; saves were left unchanged')
            fragments = (extra + '/' + str(rule.get('path', ''))).replace('\\', '/').split('/')
            below = []
            for fragment in fragments:
                if fragment in ('', '.'):
                    continue
                if fragment == '..':
                    raise CloudError('Cloud Documents path leaves its root; saves were left unchanged')
                if any(c in fragment for c in '*?{['):
                    break  # Back up the enclosing subtree for dynamic user IDs.
                below.append(fragment)
            if not below:
                raise CloudError('Cloud uses the shared Documents root; reconcile this prefix before enabling native sync')
            candidate = documents.resolve().joinpath(*below)
            if not _inside(candidate, documents):
                raise CloudError('Cloud save subtree leaves Documents; saves were left unchanged')
            if candidate.is_dir():
                paths.add(candidate)
    # Avoid duplicating a nested subtree when an enclosing one is already selected.
    return [('documents-' + str(i), p) for i, p in enumerate(sorted(paths))
            if not any(other != p and other in p.parents for other in paths)]


def _wine_idle():
    result = subprocess.run(['pgrep', '-x', 'wineserver'], capture_output=True, timeout=5)
    if result.returncode != 1:
        raise WineBusy('Wine is active; prefix preparation will retry after it exits')


def prepare(appid, *, automatic=False):
    appid = str(appid)
    if not appid.isdigit() or int(appid) <= 0:
        raise CloudError('Expected a positive Steam app ID')
    installation = steamplay.Installation()
    with installation.lock('cloud-' + appid + '.lock'):
        compat = _candidates(appid)
        prefix, users, profile = _layout(compat)
        marker = compat / MARKER
        if marker.is_symlink():
            raise CloudError('Cloud readiness record must be an ordinary file')
        if marker.exists():
            check(compat)
            return ['Native Cloud is already prepared for ' + appid]
        idle = _wine_idle if automatic else installation.idle
        idle()
        sources = []
        if profile.exists():
            sources.append(('profile', profile))
        for label, source in (('programdata', prefix / 'drive_c/ProgramData'),
                              ('public', users / 'Public')):
            if source.is_dir():
                sources.append((label, source))
        sources.extend(_document_backups(appid, profile))
        userdata = installation.root / 'userdata'
        if userdata.is_dir():
            for user in userdata.iterdir():
                if user.name.isdigit() and (user / appid).is_dir():
                    sources.append(('steam-' + user.name, user / appid))
        size = sum(path.lstat().st_size for _, source in sources
                   for path in source.rglob('*') if path.is_file() and not path.is_symlink())
        if size + 1024 * 1024 > shutil.disk_usage(installation.home).free:
            raise CloudError('Not enough free space for the save backup; no layout changed')
        backup = installation.home / 'backups/cloud' / (appid + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.mkdir(parents=True)
        for name, source in sources:
            shutil.copytree(source, backup / name, symlinks=True)
        # Revalidate immediately before changing only this game's prefix aliases.
        idle()
        if _layout(compat) != (prefix, users, profile):
            raise CloudError('Cloud profile changed during preparation; backup retained')
        for name in FOLDERS:
            (profile / name).mkdir(parents=True, exist_ok=True)
        for name in SHARED_FOLDERS:
            (prefix / name).mkdir(parents=True, exist_ok=True)
        if not os.path.lexists(users / 'steamuser'):
            (users / 'steamuser').symlink_to(profile.name)
        # CodeWeavers' Wine uses crossover; a new prefix shares steamuser's files.
        if not os.path.lexists(users / 'crossover'):
            (users / 'crossover').symlink_to(profile.name)
        for old, target in ALIASES.items():
            path = profile / old
            path.parent.mkdir(parents=True, exist_ok=True)
            if not os.path.lexists(path):
                path.symlink_to(target)
        if not os.path.lexists(compat / 'pfx'):
            (compat / 'pfx').symlink_to('pfx-wine')
        steamplay._write_json(marker, {'schema': 1, 'profile': str(profile.relative_to(prefix))})
        check(compat)
        return ['Prepared native Steam Cloud for ' + appid,
                'Recovery backup: ' + str(backup)]


def _stamp(path):
    try:
        s = path.lstat()
        return '%d:%d:%d:%d:%d' % (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns, s.st_size)
    except FileNotFoundError:
        return '-'


def index():
    """Small read-only catalogue for the native tool-resolution guard.

    Directory/manifest and layout stamps invalidate cached decisions after new
    installs, library changes or a replaced prefix. No helper runs on warm lookups.
    """
    home = Path(steam.GLUON_HOME)
    libraries = list(dict.fromkeys(Path(p).resolve() for p in steam.library_dirs()))
    watch = [Path(steam.STEAM_ROOT) / 'steamapps/libraryfolders.vdf',
             home / 'compatdata']
    apps = {}
    for library in libraries:
        base = library / 'steamapps'
        watch += [base, base / 'compatdata']
        for manifest in sorted(base.glob('appmanifest_*.acf')):
            appid = manifest.stem.removeprefix('appmanifest_')
            if not appid.isdigit() or int(appid) <= 0:
                continue
            compat = base / 'compatdata' / appid
            watched = [manifest, compat, compat / 'pfx', compat / 'pfx-wine', compat / MARKER,
                       compat / 'pfx-wine/drive_c/users']
            watched += [compat / 'pfx-wine' / name for name in SHARED_FOLDERS]
            watched += [p / 'steamapps/compatdata' / appid for p in libraries]
            watched += [home / 'compatdata' / appid]
            try:
                if _candidates(appid) != compat:
                    raise CloudError('Conflicting library')
                profile = check(compat)
                watched += [profile / p for p in (*FOLDERS, *ALIASES)]
                ready = True
            except (CloudError, OSError, ValueError):
                ready = False
            # Duplicate installed copies are a conflict, even if one is ready.
            apps[appid] = {'ready': ready and appid not in apps,
                           'watch': {str(p): _stamp(p) for p in watched}}
    data = {'schema': 1, 'apps': apps, 'watch': {str(p): _stamp(p) for p in watch}}
    steamplay._write_json(home / 'steamplay' / INDEX, data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', help='verify a prepared compatibility directory')
    group.add_argument('--automatic', metavar='APPID', help='prepare before native path resolution')
    group.add_argument('--index', action='store_true', help='refresh the native guard catalogue')
    args = parser.parse_args()
    try:
        if args.index:
            index()
        elif args.automatic:
            try:
                prepare(args.automatic, automatic=True)
            finally:
                index()
        else:
            check(args.check)
        return 0
    except WineBusy as error:
        print('gluon: ' + str(error), file=sys.stderr)
        return 75
    except (CloudError, OSError, ValueError) as error:
        print('gluon: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

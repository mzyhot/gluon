"""Prepare an optional native AutoCloud view without editing Steam metadata.

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
import sys

from . import steam, steamplay

MARKER = '.gluon-native-cloud.json'
FOLDERS = ('Documents', 'Desktop', 'Downloads', 'Music', 'Pictures', 'Videos',
           'Templates', 'Saved Games', 'AppData/Local', 'AppData/LocalLow', 'AppData/Roaming')
ALIASES = {'My Documents': 'Documents', 'Application Data': 'AppData/Roaming',
           'Local Settings/Application Data': '../AppData/Local'}
BACKUP_LIMIT = 256 * 1024 * 1024


class CloudError(RuntimeError):
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
            if ancestor.is_symlink():
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
    compat = Path(compat).resolve()
    marker = compat / MARKER
    if marker.is_symlink() or not marker.is_file():
        raise CloudError('Native Cloud is not prepared. Quit Steam and run gluonctl cloud prepare APPID first')
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
            or any(not (profile / name).is_dir() for name in FOLDERS)):
        raise CloudError('Native Cloud layout changed; prepare it again while Steam is closed')
    return profile


def prepare(appid):
    appid = str(appid)
    if not appid.isdigit() or int(appid) <= 0:
        raise CloudError('Expected a positive Steam app ID')
    installation = steamplay.Installation()
    with installation.lock('cloud.lock'):
        installation.idle()
        candidates = []
        for library in steam.library_dirs():
            library = Path(library).resolve()
            compat = library / 'steamapps/compatdata' / appid
            if (compat / 'pfx-wine').exists() or (library / 'steamapps' / ('appmanifest_' + appid + '.acf')).is_file():
                candidates.append(compat)
        if len(candidates) != 1:
            raise CloudError('Expected one installed game/library; reconnect the library before preparing Cloud')
        compat = candidates[0]
        prefix, users, profile = _layout(compat)
        marker = compat / MARKER
        if marker.is_symlink():
            raise CloudError('Cloud readiness record must be an ordinary file')
        if marker.exists():
            check(compat)
            return ['Native Cloud is already prepared for ' + appid]
        sources = []
        if profile.exists():
            sources.append(('profile', profile))
        userdata = installation.root / 'userdata'
        if userdata.is_dir():
            for user in userdata.iterdir():
                if user.name.isdigit() and (user / appid).is_dir():
                    sources.append(('steam-' + user.name, user / appid))
        size = sum(path.lstat().st_size for _, source in sources
                   for path in source.rglob('*') if path.is_file() and not path.is_symlink())
        if size > BACKUP_LIMIT:
            raise CloudError('Cloud records/profile exceed the 256 MiB automatic backup limit; no layout changed')
        backup = installation.home / 'backups/cloud' / (appid + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.mkdir(parents=True)
        for name, source in sources:
            shutil.copytree(source, backup / name, symlinks=True)
        # Revalidate immediately before changing only this game's prefix aliases.
        installation.idle()
        if _layout(compat) != (prefix, users, profile):
            raise CloudError('Cloud profile changed during preparation; backup retained')
        for name in FOLDERS:
            (profile / name).mkdir(parents=True, exist_ok=True)
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
                'Select Gluon (Steam Cloud) in the game’s Steam Compatibility settings.',
                'Recovery backup: ' + str(backup)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', required=True, help='verify a prepared compatibility directory')
    args = parser.parse_args()
    try:
        check(args.check)
        return 0
    except (CloudError, OSError, ValueError) as error:
        print('gluon: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

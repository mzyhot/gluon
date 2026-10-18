"""Prepare native AutoCloud views before Steam resolves a game's save paths.

Steam's Proton-compatible Cloud layout uses pfx/drive_c/users/steamuser.
Keep Gluon's real pfx-wine and its existing profile, and add verified aliases.
Conflicting prefixes and links outside the profile require manual reconciliation.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid

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
MIGRATION = '.gluon-cloud-migration.json'
RULES = '.gluon-cloud-rules.json'
_metadata_cache = None
_metadata_stamp = None


class CloudError(RuntimeError):
    pass


class WineBusy(CloudError):
    pass


def _rules(appid):
    """Read appinfo once per helper, without ever writing Steam metadata."""
    global _metadata_cache, _metadata_stamp
    path = Path(steam.APPINFO)
    stamp = (str(path), _stamp(path))
    if stamp != _metadata_stamp:
        try:
            _metadata_cache = appinfo.AppInfoFile(str(path))
        except FileNotFoundError:
            _metadata_cache = None
        except (ValueError, struct.error) as error:
            raise CloudError('Steam Cloud metadata is incomplete; let Steam finish updating and retry') from error
        _metadata_stamp = stamp
    entry = _metadata_cache.get(int(appid)) if _metadata_cache else None
    rules = entry.kv['appinfo'].get('ufs', {}) if entry else None
    if rules is not None and not isinstance(rules, dict):
        raise CloudError('Invalid Steam Cloud metadata')
    return rules


def needs_prefix(appid):
    rules = _rules(appid)
    # Unknown metadata stays conservative. A fresh Wine view is safe, but an
    # existing conflict must not be guessed away just because appinfo is missing.
    if rules is None:
        return True
    saves = rules.get('savefiles', {})
    overrides = rules.get('rootoverrides', {})
    if not isinstance(saves, dict) or not isinstance(overrides, dict):
        raise CloudError('Invalid Steam Cloud path rules')
    if any(not isinstance(rule, dict) or not isinstance(rule.get('root'), str) or not rule['root']
           for rule in saves.values()):
        raise CloudError('Invalid Steam Cloud save root')
    if any(not isinstance(rule, dict) for rule in overrides.values()):
        raise CloudError('Invalid Steam Cloud root override')
    if not saves:
        return False  # RemoteStorage API games do not use AutoCloud prefix paths.
    roots = [str(rule.get('root', '')).lower() for rule in saves.values() if isinstance(rule, dict)]
    roots += [str(rule.get('useinstead', rule.get('root', ''))).lower() for rule in overrides.values()]
    return any(root not in ('gameinstall', 'appinstalldirectory', 'steamclouddocuments', 'machome', 'macappsupport', 'macdocuments',
                           'linuxhome', 'linuxxdgdatahome', 'linuxxdgconfighome') for root in roots)


def check_app(appid, compat=None):
    if not needs_prefix(appid):
        return 'native'
    expected = _candidates(str(appid))
    if compat is not None and Path(compat).resolve() != expected.resolve():
        raise CloudError('Launcher and Steam selected different compatibility directories')
    check(expected)
    if not _rules_current(expected, _rules_token(appid)):
        raise CloudError('Cloud save rules changed; a new verified backup is required before synchronization')
    return 'prefix'


def _rules_token(appid):
    return hashlib.sha256(json.dumps(_rules(appid), sort_keys=True).encode()).hexdigest()


def _rules_current(compat, token):
    path = compat / RULES
    if path.is_symlink():
        raise CloudError('Cloud rules record must be an ordinary file')
    return path.is_file() and steamplay._json(path) == {'schema': 1, 'rules': token}


def _legacy_prefix(compat):
    """Recognize only the dual-runtime layout produced by earlier Gluon.

    Its active Wine profile stays authoritative. Retire the old Gluon Proton
    prefix whole, retaining every file; never merge two registries or save sets.
    """
    old, active = compat / 'pfx', compat / 'pfx-wine'
    if not old.is_dir() or old.is_symlink() or not active.is_dir() or active.is_symlink():
        return None
    home = Path(steam.GLUON_HOME)
    required = (compat / '.gluon-steam-bridge', compat / 'version', compat / 'config_info',
                compat / 'tracked_files', old / 'creation_sync_guard',
                old / 'system.reg', active / 'system.reg')
    if any(p.is_symlink() or not p.is_file() for p in required):
        return None
    if (compat / '.gluon-steam-bridge').stat().st_size != 0:
        return None
    current = home / 'runtime/current'
    if not (current / 'bin/wine').is_file() or (current / 'proton').exists():
        return None
    for settings in (home / 'settings.env', home / 'games' / (compat.name + '.env')):
        if settings.is_file() and any(line.startswith(('GLUON_RUNTIME=', 'GLUON_WINE_ROOT=')) and line.partition('=')[2]
                                      for line in settings.read_text().splitlines()):
            return None
    lines = (compat / 'config_info').read_text().splitlines()
    if (len(lines) < 8 or lines[0] != (compat / 'version').read_text().strip()
            or lines[1] != str(current / 'files/share/fonts') + '/'
            or lines[2] != str(current / 'files/lib') + '/'
            or lines[7] != str(current / 'files/share/default_pfx') + '/'):
        return None
    users = old / 'drive_c/users'
    if users.is_symlink() or not users.is_dir():
        return None
    real = [p.name for p in users.iterdir() if p.is_dir() and not p.is_symlink() and p.name != 'Public']
    return old if real == ['steamuser'] else None


def _recover_migration(compat):
    journal = compat / MIGRATION
    if not journal.exists():
        return
    record = steamplay._json(journal)
    name = record.get('retired', '')
    if (record.get('schema') != 1 or not isinstance(name, str)
            or not name.startswith('.gluon-retired-pfx-') or '/' in name or '\\' in name):
        raise CloudError('Invalid Cloud migration journal; prefixes were left unchanged')
    retired, view = compat / name, compat / 'pfx'
    if (compat / MARKER).exists():
        check(compat)
        return
    if not retired.exists():
        return
    if retired.is_symlink() or not retired.is_dir():
        raise CloudError('Retired prefix changed; recovery stopped')
    if view.is_symlink() and os.readlink(view) == 'pfx-wine':
        view.unlink()
    if os.path.lexists(view):
        raise CloudError('Interrupted Cloud migration has two prefixes; recovery copies retained')
    os.rename(retired, view)
    journal.unlink()


def _inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def _layout(compat, legacy=None):
    prefix = compat / 'pfx-wine'
    if prefix.is_symlink() or (prefix.exists() and not prefix.is_dir()):
        raise CloudError('Cloud preparation requires an ordinary pfx-wine directory')
    view = compat / 'pfx'
    if os.path.lexists(view) and view != legacy and not (view.is_symlink() and os.readlink(view) == 'pfx-wine'):
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
    """Back up affected host subtrees; leave rule evaluation to Steam itself."""
    host_links = []
    known = {**HOST_FOLDERS, 'Desktop/My Mac Desktop': 'Desktop'}
    for relative, host_name in known.items():
        link = profile / relative
        if link.is_symlink():
            target = (Path.home() / host_name).resolve()
            if link.resolve() != target:
                raise CloudError('Unexpected host-folder link; saves were left unchanged: ' + str(link))
            host_links.append((link, target))
    if not host_links:
        return []
    ufs = _rules(appid)
    if ufs is None:
        raise CloudError("Cannot protect shared Documents without this game's Cloud metadata")
    roots = {'winmydocuments': profile / 'Documents', 'windesktop': profile / 'Desktop',
             'winuserprofile': profile, 'winsavedgames': profile / 'Saved Games',
             'winappdatalocal': profile / 'AppData/Local',
             'winappdatalocallow': profile / 'AppData/LocalLow',
             'winappdataroaming': profile / 'AppData/Roaming'}
    overrides = list(ufs.get('rootoverrides', {}).values())
    paths = set()
    for rule in ufs.get('savefiles', {}).values():
        root = str(rule.get('root', '')).lower()
        # Include both add and replacement interpretations conservatively. This
        # covers publisher overrides without implementing a second sync engine.
        targets = [(root, str(rule.get('path', '')))]
        for override in overrides:
            if str(override.get('root', '')).lower() != root:
                continue
            destination = str(override.get('useinstead', root)).lower()
            extra = str(override.get('addpath', ''))
            targets.append((destination, extra + '/' + str(rule.get('path', ''))))
            if extra or str(override.get('replacepath', '0')).lower() in ('1', 'true'):
                targets.append((destination, extra))
        for root, below in targets:
            if root not in roots:
                continue
            if rule.get('pathtransforms') or any(o.get('pathtransforms') for o in overrides):
                raise CloudError('Transformed Cloud paths in host folders need reconciliation; saves were left unchanged')
            candidate = roots[root]
            for fragment in below.replace(chr(92), '/').split('/'):
                if fragment in ('', '.'):
                    continue
                if fragment == '..' or ':' in fragment:
                    raise CloudError('Cloud save path leaves its root; saves were left unchanged')
                if any(c in fragment for c in '*?{[%$'):
                    break  # Back up the enclosing game subtree for dynamic IDs.
                candidate /= fragment
            for ancestor in (candidate, *candidate.parents):
                if ancestor == profile:
                    break
                if ancestor.is_symlink() and not any(ancestor == link for link, _ in host_links):
                    if not _inside(ancestor, profile):
                        raise CloudError('Cloud save subtree follows an unexpected external link: ' + str(ancestor))
            for link, target in host_links:
                if candidate == link or candidate in link.parents:
                    raise CloudError('Cloud uses a shared host folder root; reconcile this prefix before enabling native sync')
                if link not in candidate.parents:
                    continue
                source = target / candidate.relative_to(link)
                if not _inside(source, target):
                    raise CloudError('Cloud save subtree leaves its host folder; saves were left unchanged')
                if source.is_dir():
                    paths.add(source)
    # Back up each affected subtree once, preserving unrelated personal folders.
    return [('documents-' + str(i), p) for i, p in enumerate(sorted(paths))
            if not any(other != p and other in p.parents for other in paths)]


def _wine_idle():
    result = subprocess.run(['pgrep', '-x', 'wineserver'], capture_output=True, timeout=5)
    if result.returncode != 1:
        raise WineBusy('Wine is active; prefix preparation will retry after it exits')


def _error_path(appid):
    if not str(appid).isdigit() or int(appid) <= 0:
        raise CloudError('Expected a positive Steam app ID')
    return Path(steam.GLUON_HOME) / 'steamplay/cloud-errors' / (str(appid) + '.json')


def status(appid):
    try:
        mode = check_app(appid)
        return ['Steam Cloud is ready for %s (%s paths)' % (appid, mode)]
    except (CloudError, OSError, ValueError) as error:
        saved = _error_path(appid)
        reason = steamplay._json(saved).get('reason', str(error)) if saved.exists() else str(error)
        return ['Steam Cloud needs attention for %s: %s' % (appid, reason),
                'Existing prefixes and saves are retained. Details: ' + str(Path(steam.GLUON_HOME) / 'steamplay/cloud.log')]


def prepare_installed():
    """Prepare upgrades while Steam is closed, before its first Cloud scan."""
    notes = []
    for appid in index()['apps']:
        try:
            notes += prepare(appid)
            _error_path(appid).unlink(missing_ok=True)
        except (CloudError, steamplay.SteamPlayError, OSError, ValueError) as error:
            notes.append('Cloud needs attention for %s: %s; prefixes retained' % (appid, error))
            try:
                steamplay._write_json(_error_path(appid), {'schema': 1, 'reason': str(error)})
            except OSError:
                pass
    index()
    return notes


def _show_error(message):
    if sys.platform != 'darwin' or os.environ.get('GITHUB_ACTIONS') == 'true':
        return
    script = 'on run argv\ndisplay alert "Gluon: Steam Cloud needs attention" message (item 1 of argv) as critical\nend run'
    try:
        subprocess.run(['/usr/bin/osascript', '-e', script, message[:3000]],
                       capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass  # The same error is always written to stderr and the per-app record.


def prepare(appid, *, automatic=False):
    appid = str(appid)
    if not appid.isdigit() or int(appid) <= 0:
        raise CloudError('Expected a positive Steam app ID')
    installation = steamplay.Installation()
    with installation.lock('cloud-' + appid + '.lock'):
        if not needs_prefix(appid):
            return ['Steam Cloud uses native/API paths for ' + appid + '; existing prefixes are unchanged']
        compat = _candidates(appid)
        token = _rules_token(appid)
        idle = _wine_idle if automatic else installation.idle
        if (compat / MIGRATION).exists() and not (compat / MARKER).exists():
            idle()
            _recover_migration(compat)
        legacy = _legacy_prefix(compat)
        prefix, users, profile = _layout(compat, legacy)
        marker = compat / MARKER
        if marker.is_symlink():
            raise CloudError('Cloud readiness record must be an ordinary file')
        if marker.exists():
            check(compat)
            if _rules_current(compat, token):
                return ['Native Cloud is already prepared for ' + appid]
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
        snapshots = {}
        for name, source in sources:
            snapshots[name] = steamplay._tree(source)
            shutil.copytree(source, backup / name, symlinks=True)
            if steamplay._tree(backup / name) != snapshots[name] or steamplay._tree(source) != snapshots[name]:
                raise CloudError('Save backup verification failed; no layout changed')
        # Revalidate immediately before changing only this game's prefix aliases.
        idle()
        if (_rules_token(appid) != token or _layout(compat, legacy) != (prefix, users, profile)
                or legacy != _legacy_prefix(compat)
                or any(steamplay._tree(source) != snapshots[name] for name, source in sources)):
            raise CloudError('Cloud profile changed during preparation; backup retained')
        if legacy:
            retired = '.gluon-retired-pfx-' + uuid.uuid4().hex
            steamplay._write_json(compat / MIGRATION, {'schema': 1, 'retired': retired, 'backup': str(backup)})
            os.rename(legacy, compat / retired)
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
        steamplay._write_json(compat / RULES, {'schema': 1, 'rules': token})
        check_app(appid, compat)
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
    watch = [Path(steam.STEAM_ROOT) / 'steamapps/libraryfolders.vdf',
             Path(steam.APPINFO), home / 'compatdata', home / 'runtime/current',
             home / 'settings.env', home / 'games']
    # Capture before reading metadata/enumerating manifests. A concurrent update
    # must invalidate this catalogue, not stamp a stale decision as current.
    global_stamps = {str(p): _stamp(p) for p in watch}
    libraries = list(dict.fromkeys(Path(p).resolve() for p in steam.library_dirs()))
    apps = {}
    for library in libraries:
        base = library / 'steamapps'
        global_stamps.update({str(p): _stamp(p) for p in (base, base / 'compatdata')})
        for manifest in sorted(base.glob('appmanifest_*.acf')):
            appid = manifest.stem.removeprefix('appmanifest_')
            if not appid.isdigit() or int(appid) <= 0:
                continue
            compat = base / 'compatdata' / appid
            watched = [manifest, compat, compat / 'pfx', compat / 'pfx-wine', compat / MARKER,
                       compat / 'pfx-wine/drive_c/users', compat / MIGRATION, compat / RULES,
                       home / 'games' / (appid + '.env')]
            watched += [compat / name for name in ('.gluon-steam-bridge', 'config_info', 'version', 'tracked_files')]
            watched += [compat / 'pfx-wine' / name for name in SHARED_FOLDERS]
            watched += [p / 'steamapps/compatdata' / appid for p in libraries]
            watched += [home / 'compatdata' / appid]
            stamps = {str(p): _stamp(p) for p in watched}
            try:
                if needs_prefix(appid) and _candidates(appid) != compat:
                    raise CloudError('Conflicting library')
                if needs_prefix(appid):
                    profile = check(compat)
                    watched += [profile / p for p in (*FOLDERS, *ALIASES)]
                    stamps.update({str(p): _stamp(p) for p in watched if str(p) not in stamps})
                check_app(appid, compat)
                ready = all(_stamp(Path(p)) == stamp for p, stamp in stamps.items())
            except (CloudError, steamplay.SteamPlayError, OSError, ValueError):
                ready = False
            # Duplicate installed copies are a conflict, even if one is ready.
            apps[appid] = {'ready': ready and appid not in apps,
                           'watch': stamps}
    data = {'schema': 1, 'apps': apps, 'watch': global_stamps}
    steamplay._write_json(home / 'steamplay' / INDEX, data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', help='verify a prepared compatibility directory')
    group.add_argument('--check-app', metavar='APPID', help='verify only the Cloud paths this game actually uses')
    group.add_argument('--explain', metavar='APPID', help='report a refused preparation without starting the game')
    group.add_argument('--automatic', metavar='APPID', help='prepare before native path resolution')
    group.add_argument('--index', action='store_true', help='refresh the native guard catalogue')
    parser.add_argument('--compat', help='launcher-selected compatibility directory')
    parser.add_argument('--show-error', action='store_true', help='show an actionable error when Play requested a failed preparation')
    args = parser.parse_args()
    try:
        if args.index:
            index()
        elif args.automatic:
            try:
                prepare(args.automatic, automatic=True)
            finally:
                index()
        elif args.check_app:
            print(check_app(args.check_app, args.compat))
        elif args.explain:
            raise CloudError('\n'.join(status(args.explain)) + '\nRetry Play after preparation succeeds. No game was started with unsynchronized saves.')
        else:
            check(args.check)
        return 0
    except WineBusy as error:
        appid = args.automatic or args.check_app or args.explain
        if appid:
            try:
                steamplay._write_json(_error_path(appid), {'schema': 1, 'reason': str(error)})
            except OSError:
                pass
        print('gluon: Cloud for %s: %s' % (appid, error), file=sys.stderr)
        return 75
    except (CloudError, steamplay.SteamPlayError, OSError, ValueError) as error:
        appid = args.automatic or args.check_app or args.explain
        message = 'Cloud for %s: %s' % (appid, error) if appid else str(error)
        if appid and str(appid).isdigit() and int(appid) > 0 and not args.explain:
            try:
                steamplay._write_json(_error_path(appid), {'schema': 1, 'reason': str(error)})
            except OSError:
                pass
        print('gluon: ' + message, file=sys.stderr)
        if args.show_error:
            _show_error(message + '\n\nExisting prefixes and saves were retained.\nDetails: ' + str(Path(steam.GLUON_HOME) / 'steamplay/cloud.log'))
        return 1


if __name__ == '__main__':
    sys.exit(main())

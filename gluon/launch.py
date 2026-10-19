"""Validate and prepare one launch in a single Python process."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

from . import appbundle, cloud, overlay, steamplay


def prepare(args):
    started = time.perf_counter()
    mode = 'none'
    if os.environ.get('GLUON_NATIVE_CLOUD') == '1':
        mode = cloud.check_app(args.appid, args.compat)
        if mode == 'prefix' and args.kind != 'wine':
            raise cloud.CloudError("Gluon's native Steam Cloud requires its plain-Wine runtime")
    checked = time.perf_counter()
    insertion, bundle = '', ''
    runtime = Path(args.runtime)
    if args.game_session and args.kind == 'wine':
        if os.environ.get('GLUON_OVERLAY', '1') != '0':
            client = os.environ.get('STEAM_CLIENT_CONFIG_FILE')
            client = str(Path(client).parent) if client else str(
                Path(os.environ.get('STEAM_ROOT', str(Path.home() / 'Library/Application Support/Steam')))
                / 'Steam.AppBundle/Steam/Contents/MacOS')
            insertion = overlay.validated_insertions(os.environ, client) or ''
        patches = runtime / 'licenses/gluon-wine-patches/APPLIED.txt'
        applied = set(patches.read_text().splitlines()) if patches.is_file() else set()
        loader = runtime / 'lib/wine/x86_64-unix/wine'
        ntdll = loader.with_name('ntdll.so')
        if (os.environ.get('GLUON_APP_BUNDLE', '1') != '0'
                and {'wine-macos-app-bundle', 'wine-macos-overlay-scope'} <= applied
                and os.access(loader, os.X_OK) and ntdll.is_file()):
            try:
                root = os.environ.get('STEAM_ROOT', str(Path.home() / 'Library/Application Support/Steam'))
                name = appbundle.game_name(args.steamapps, args.appid, args.fallback_name)
                bundle = str(appbundle.prepare(args.home, args.appid, name, str(loader), str(ntdll),
                                              appbundle.steam_icon(root, args.appid)))
            except (OSError, ValueError) as error:
                print('gluon: app bundle unavailable: %s' % error, file=sys.stderr)
    # Fixed keys, literal values: the shell never evaluates helper output as code.
    for key, value in (('cloud', mode), ('overlay', insertion), ('bundle', bundle)):
        if '\n' in value or '\r' in value:
            raise ValueError('A launch path contains an unsupported newline')
        print(key + '=' + value)
    print('gluon: launch preparation: cloud %.1f ms, overlay/bundle %.1f ms' %
          ((checked - started) * 1000, (time.perf_counter() - checked) * 1000), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('home', 'appid', 'compat', 'runtime', 'kind', 'fallback-name'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--steamapps')
    parser.add_argument('--game-session', action='store_true')
    args = parser.parse_args()
    try:
        prepare(args)
        return 0
    except (cloud.CloudError, steamplay.SteamPlayError, OSError, ValueError) as error:
        message = 'Cloud/launch preparation for %s: %s' % (args.appid, error)
        print('gluon: ' + message, file=sys.stderr)
        if os.environ.get('GLUON_NATIVE_CLOUD') == '1':
            cloud._show_error(message)
        return 1


if __name__ == '__main__':
    sys.exit(main())

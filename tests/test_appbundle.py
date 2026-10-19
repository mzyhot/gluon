"""Offline fixtures for per-game app bundles."""
import os
import base64
from pathlib import Path
import plistlib
import tempfile
import sys
import unittest
from unittest.mock import patch

from gluon import appbundle


class AppBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gluon-appbundle-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "Gluon Home"
        runtime = self.root / "runtime/lib/wine/x86_64-unix"
        runtime.mkdir(parents=True)
        self.loader = runtime / "wine"
        self.loader.write_bytes(b"loader")
        self.loader.chmod(0o755)
        self.ntdll = runtime / "ntdll.so"
        self.ntdll.write_bytes(b"ntdll")
        self.steamapps = self.root / "steamapps"
        self.steamapps.mkdir()

    def prepare(self, name="Happy Wheels"):
        return appbundle.prepare(str(self.home), "4705510", name, str(self.loader), str(self.ntdll))

    def test_loader_is_the_bundle_executable_and_names_the_game(self):
        bundle = self.prepare()
        self.assertEqual(bundle, self.home / "apps.noindex/4705510/Happy Wheels.app")
        info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
        self.assertEqual(info["CFBundleName"], "Happy Wheels")
        self.assertEqual(info["CFBundleIdentifier"], "org.gluon.game.4705510")
        self.assertEqual(info["LSApplicationCategoryType"], "public.app-category.games")
        self.assertIs(info["LSUIElement"], True)
        # macOS treats a process as part of the bundle only when it runs the
        # declared executable, so that must be Wine's loader itself.
        macos = bundle / "Contents/MacOS"
        self.assertTrue(os.path.samefile(macos / info["CFBundleExecutable"], self.loader))
        self.assertEqual(os.readlink(macos / "ntdll.so"), str(self.ntdll))
        self.assertEqual(sorted(p.name for p in macos.iterdir()), ["ntdll.so", "wine"])

    def test_unchanged_bundle_is_reused_and_runtime_change_rebuilds(self):
        bundle = self.prepare()
        marker = bundle / "Contents/marker"
        marker.write_text("kept")
        self.assertEqual(self.prepare(), bundle)
        self.assertTrue(marker.exists(), "an unchanged bundle was rebuilt")
        replacement = self.loader.with_name("wine-new")
        replacement.write_bytes(b"new loader")
        replacement.chmod(0o755)
        os.replace(replacement, self.loader)
        self.prepare()
        self.assertFalse(marker.exists(), "a new runtime loader did not rebuild the bundle")
        self.assertTrue(os.path.samefile(bundle / "Contents/MacOS/wine", self.loader))

    def test_earlier_script_bundle_and_session_file_are_replaced(self):
        root = self.home / "apps.noindex/4705510"
        old = root / "Happy Wheels.app/Contents/MacOS"
        old.mkdir(parents=True)
        (old / "gluon-game").write_text("#!/bin/bash\n")
        (root / "session.env").write_text("export X=1\n")
        bundle = self.prepare()
        self.assertFalse((bundle / "Contents/MacOS/gluon-game").exists())
        self.assertFalse((root / "session.env").exists())

    def test_renamed_game_replaces_its_old_bundle(self):
        self.prepare("Old Name")
        bundle = self.prepare("New: Name/With Slashes")
        self.assertEqual(bundle.name, "New NameWith Slashes.app")
        self.assertEqual(sorted(p.name for p in bundle.parent.glob("*.app")), [bundle.name])

    def test_game_name_comes_from_the_app_manifest(self):
        (self.steamapps / "appmanifest_4705510.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"4705510"\n\t"name"\t\t"Happy Wheels"\n}\n')
        self.assertEqual(appbundle.game_name(str(self.steamapps), "4705510", "fallback"), "Happy Wheels")
        self.assertEqual(appbundle.game_name(str(self.steamapps), "1", "Install Dir"), "Install Dir")

    def test_steam_cache_selects_the_client_icon_and_ignores_library_art(self):
        cache = self.root / 'Steam/appcache/librarycache/4705510'
        cache.mkdir(parents=True)
        (cache / 'library_hero.jpg').touch()
        (cache / 'logo.png').touch()
        icon = cache / ('a' * 40 + '.jpg')
        icon.write_bytes(b'cached client icon')
        self.assertEqual(appbundle.steam_icon(str(self.root / 'Steam'), '4705510'), icon)

    def test_icon_is_registered_and_reused_until_the_cached_art_changes(self):
        source = self.root / 'icon.png'
        source.write_bytes(b'first icon')

        def convert(path, resources):
            resources.mkdir(parents=True)
            (resources / appbundle.ICON).write_bytes(b'icns' + path.read_bytes())
            return True

        def prepare():
            return appbundle.prepare(str(self.home), '4705510', 'Happy Wheels',
                                     str(self.loader), str(self.ntdll), source)

        with patch.object(appbundle, '_build_icon', side_effect=convert) as build:
            bundle = prepare()
            info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
            self.assertEqual(info['CFBundleIconFile'], appbundle.ICON)
            prepare()
            self.assertEqual(build.call_count, 1, 'a warm launch reconverted the icon')
            source.write_bytes(b'new icon')
            prepare()
            self.assertEqual(build.call_count, 2)
            self.assertEqual((bundle / 'Contents/Resources/Game.icns').read_bytes(), b'icnsnew icon')

    def test_bad_icon_is_not_retried_on_every_launch(self):
        source = self.root / 'icon.jpg'
        source.write_bytes(b'incomplete download')
        with patch.object(appbundle, '_build_icon', return_value=False) as build:
            for _ in range(2):
                bundle = appbundle.prepare(str(self.home), '4705510', 'Happy Wheels',
                                           str(self.loader), str(self.ntdll), source)
            self.assertEqual(build.call_count, 1)
            self.assertNotIn('CFBundleIconFile', plistlib.loads((bundle / 'Contents/Info.plist').read_bytes()))

    @unittest.skipUnless(sys.platform == 'darwin' and os.environ.get('GITHUB_ACTIONS') == 'true',
                         'native image conversion runs on the Actions Mac')
    def test_native_icon_conversion_produces_a_real_icns(self):
        source = self.root / 'icon.png'
        source.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='))
        resources = self.root / 'Resources'
        self.assertTrue(appbundle._build_icon(source, resources))
        self.assertEqual((resources / appbundle.ICON).read_bytes()[:4], b'icns')


if __name__ == "__main__":
    unittest.main()

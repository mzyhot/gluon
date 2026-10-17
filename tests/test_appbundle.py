"""Offline fixtures for per-game app bundles."""
import os
from pathlib import Path
import plistlib
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()

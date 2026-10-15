"""Offline fixtures for per-game app bundles; the "loader" is a shell script."""
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest

from gluon import appbundle

FAKE_LOADER = """#!/bin/bash
{
    printf 'cwd=%s\\n' "$PWD"
    printf 'insert=%s\\n' "${DYLD_INSERT_LIBRARIES:-}"
    printf 'overlay-var=%s\\n' "${GLUON_OVERLAY_INSERT:-}"
    printf 'prefix=%s\\n' "${WINEPREFIX:-}"
    printf 'quoted=%s\\n' "${GLUON_QUOTED:-}"
    printf 'cf=%s\\n' "${__CFBundleIdentifier:-}"
    for argument in "$@"; do printf 'arg=%s\\n' "$argument"; done
} > "$GLUON_FIXTURE_OUT"
"""


class AppBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gluon-appbundle-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "Gluon Home"
        runtime = self.root / "runtime/lib/wine/x86_64-unix"
        runtime.mkdir(parents=True)
        self.loader = runtime / "wine"
        self.loader.write_text(FAKE_LOADER)
        self.loader.chmod(0o755)
        self.ntdll = runtime / "ntdll.so"
        self.ntdll.write_bytes(b"ntdll")
        self.steamapps = self.root / "steamapps"
        self.steamapps.mkdir()

    def prepare(self, name="Happy Wheels"):
        return appbundle.prepare(str(self.home), "4705510", name, str(self.loader), str(self.ntdll))

    def test_bundle_names_the_game_and_keeps_wine_helpers_agents(self):
        bundle = self.prepare()
        self.assertEqual(bundle, self.home / "apps.noindex/4705510/Happy Wheels.app")
        info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
        self.assertEqual(info["CFBundleName"], "Happy Wheels")
        self.assertEqual(info["CFBundleIdentifier"], "org.gluon.game.4705510")
        self.assertEqual(info["LSApplicationCategoryType"], "public.app-category.games")
        self.assertIs(info["LSUIElement"], True)
        self.assertEqual(info["CFBundleExecutable"], appbundle.EXECUTABLE)
        macos = bundle / "Contents/MacOS"
        self.assertTrue(os.path.samefile(macos / "wine", self.loader))
        self.assertEqual(os.readlink(macos / "ntdll.so"), str(self.ntdll))
        self.assertTrue(os.stat(macos / appbundle.EXECUTABLE).st_mode & stat.S_IXUSR)

    def test_unchanged_bundle_is_reused_and_runtime_change_rebuilds(self):
        bundle = self.prepare()
        marker = bundle / "Contents/marker"
        marker.write_text("kept")
        self.assertEqual(self.prepare(), bundle)
        self.assertTrue(marker.exists(), "an unchanged bundle was rebuilt")
        replacement = self.loader.with_name("wine-new")
        replacement.write_text(FAKE_LOADER)
        replacement.chmod(0o755)
        os.replace(replacement, self.loader)
        self.prepare()
        self.assertFalse(marker.exists(), "a new runtime loader did not rebuild the bundle")
        self.assertTrue(os.path.samefile(bundle / "Contents/MacOS/wine", self.loader))

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

    def run_bundle(self, bundle, *arguments, environment=None):
        output = self.root / "fixture-out"
        env = {"PATH": "/usr/bin:/bin", "GLUON_FIXTURE_OUT": str(output), **(environment or {})}
        result = subprocess.run([str(bundle / "Contents/MacOS" / appbundle.EXECUTABLE), *arguments],
                                env=env, capture_output=True, text=True, timeout=10)
        lines = output.read_text().splitlines() if output.exists() else []
        return result, lines

    def test_scoped_overlay_is_left_for_wine_to_place(self):
        bundle = self.prepare()
        appbundle.write_session(bundle, {"GLUON_FIXTURE_OUT": str(self.root / "fixture-out")}, str(self.root),
                                str(self.root / "wine.log"), "/overlay/a.dylib", scoped=True)
        result, lines = self.run_bundle(bundle, "game.exe")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("insert=", lines)
        self.assertIn("overlay-var=/overlay/a.dylib", lines)

    def test_executable_refuses_to_start_without_a_steam_session(self):
        result, lines = self.run_bundle(self.prepare(), "game.exe")
        self.assertEqual(result.returncode, 64)
        self.assertIn("Start this game from Steam", result.stderr)
        self.assertEqual(lines, [])

    def test_session_starts_the_game_once_with_overlay_only_in_wine(self):
        bundle = self.prepare()
        game_dir = self.root / "Game Folder"
        game_dir.mkdir()
        environment = {
            "WINEPREFIX": "/prefix with spaces", "GLUON_QUOTED": "it's \"quoted\" $HOME `x`",
            "DYLD_INSERT_LIBRARIES": "/must/not/leak.dylib", "__CFBundleIdentifier": "com.valvesoftware.steam",
            "GLUON_FIXTURE_OUT": str(self.root / "fixture-out"),
        }
        session = appbundle.write_session(bundle, environment, str(game_dir), str(self.root / "wine.log"),
                                          "/overlay/a.dylib:/overlay/b.dylib", scoped=False)
        self.assertEqual(stat.S_IMODE(session.stat().st_mode), 0o600)
        text = session.read_text()
        self.assertNotIn("DYLD_INSERT_LIBRARIES", text)
        self.assertNotIn("__CF", text)
        result, lines = self.run_bundle(bundle, "c:\\windows\\system32\\steam.exe", "Game Name.exe", "--flag")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cwd=%s" % game_dir, lines)
        # macOS strips DYLD_* when starting a protected system binary such as
        # this fixture's /bin/bash; Wine's own loader is not one. Linux shows it.
        expected_insert = "" if sys.platform == "darwin" else "/overlay/a.dylib:/overlay/b.dylib"
        self.assertIn("insert=" + expected_insert, lines)
        self.assertIn("overlay-var=", lines)
        self.assertIn("prefix=/prefix with spaces", lines)
        self.assertIn("quoted=it's \"quoted\" $HOME `x`", lines)
        self.assertIn("cf=", lines)
        self.assertEqual([line for line in lines if line.startswith("arg=")],
                         ["arg=c:\\windows\\system32\\steam.exe", "arg=Game Name.exe", "arg=--flag"])
        self.assertFalse(session.exists(), "the session file was not consumed")
        result, _ = self.run_bundle(bundle, "again.exe")
        self.assertEqual(result.returncode, 64, "a second start reused the session")


if __name__ == "__main__":
    unittest.main()

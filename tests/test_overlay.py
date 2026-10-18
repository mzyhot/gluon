"""Offline header fixtures; Valve libraries, Wine and graphics are never loaded."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from gluon import overlay


def thin(cpu=overlay.CPU_X86_64, file_type=overlay.MH_DYLIB):
    return struct.pack("<IIIIIIII", 0xFEEDFACF, cpu, 3, file_type, 0, 0, 0, 0)


def universal(wide=False):
    first, second = (128, 256) if wide else (64, 128)
    magic = 0xCAFEBABF if wide else 0xCAFEBABE
    entry = ">IIQQII" if wide else ">IIIII"
    args1 = [0x0100000C, 0, first, 32, 4]
    args2 = [overlay.CPU_X86_64, 3, second, 32, 4]
    if wide:
        args1.append(0)
        args2.append(0)
    raw = bytearray(struct.pack(">II", magic, 2) + struct.pack(entry, *args1) + struct.pack(entry, *args2))
    raw.extend(b"\0" * (second + 32 - len(raw)))
    raw[first:first + 32] = thin(0x0100000C)
    raw[second:second + 32] = thin()
    return bytes(raw)


class OverlayValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gluon-overlay-test-")
        self.addCleanup(self.temp.cleanup)
        self.client = Path(self.temp.name).resolve() / "Steam Client"
        self.client.mkdir()
        self.renderer = self.client / "gameoverlayrenderer.dylib"
        self.loader = self.client / "steamloader.dylib"
        self.renderer.write_bytes(thin())
        self.loader.write_bytes(thin())
        self.environment = {"SteamClientLaunch": "1", "SteamOverlayGameId": "4705510",
                            "STEAM_DYLD_INSERT_LIBRARIES": str(self.loader) + ":" + str(self.renderer)}

    def validate(self):
        return overlay.validated_insertions(self.environment, str(self.client))

    def test_steam_supplied_pair_preserves_order(self):
        self.assertEqual(self.validate(), self.environment["STEAM_DYLD_INSERT_LIBRARIES"])

    def test_renderer_alone_is_valid(self):
        self.environment["STEAM_DYLD_INSERT_LIBRARIES"] = str(self.renderer)
        self.assertEqual(self.validate(), str(self.renderer))

    def test_client_directory_alias_returns_canonical_library_paths(self):
        alias = self.client.parent / "Steam Alias"
        alias.symlink_to(self.client, target_is_directory=True)
        self.environment["STEAM_DYLD_INSERT_LIBRARIES"] = ":".join(
            str(alias / name) for name in ("steamloader.dylib", "gameoverlayrenderer.dylib"))
        self.assertEqual(overlay.validated_insertions(self.environment, str(alias)),
                         str(self.loader) + ":" + str(self.renderer))

    def test_universal_x64_slices_are_valid(self):
        for wide in (False, True):
            with self.subTest(wide=wide):
                self.renderer.write_bytes(universal(wide))
                self.assertIsNotNone(self.validate())

    def test_missing_steam_launch_is_rejected(self):
        for value in ("", "0", "2"):
            with self.subTest(value=value):
                self.environment["SteamClientLaunch"] = value
                self.assertIsNone(self.validate())

    def test_invalid_overlay_game_id_is_rejected(self):
        for value in ("", "0", "-1", "+1", "01", "1.5", "123\n", str(1 << 64)):
            with self.subTest(value=value):
                self.environment["SteamOverlayGameId"] = value
                self.assertIsNone(self.validate())

    def test_loader_without_renderer_is_rejected(self):
        self.environment["STEAM_DYLD_INSERT_LIBRARIES"] = str(self.loader)
        self.assertIsNone(self.validate())

    def test_empty_duplicate_and_extra_libraries_are_rejected(self):
        for value in ("", ":" + str(self.renderer), str(self.renderer) + ":",
                      str(self.renderer) + ":" + str(self.renderer),
                      str(self.renderer) + ":" + str(self.client / "other.dylib"),
                      str(self.loader) + ":" + str(self.renderer) + ":" + str(self.renderer)):
            with self.subTest(value=value):
                self.environment["STEAM_DYLD_INSERT_LIBRARIES"] = value
                self.assertIsNone(self.validate())

    def test_same_filename_outside_client_is_rejected(self):
        outside = Path(self.temp.name) / "gameoverlayrenderer.dylib"
        outside.write_bytes(thin())
        self.environment["STEAM_DYLD_INSERT_LIBRARIES"] = str(outside)
        self.assertIsNone(self.validate())

    def test_missing_and_relative_libraries_are_rejected(self):
        self.renderer.unlink()
        self.assertIsNone(self.validate())
        self.environment["STEAM_DYLD_INSERT_LIBRARIES"] = "gameoverlayrenderer.dylib"
        self.assertIsNone(self.validate())

    def test_symbolic_library_is_rejected(self):
        other = self.client / "original.dylib"
        self.renderer.rename(other)
        self.renderer.symlink_to(other)
        self.assertIsNone(self.validate())

    def test_non_macho_arm_only_and_non_dylib_are_rejected(self):
        for raw in (b"not a Mach-O", thin(0x0100000C), thin(file_type=2)):
            with self.subTest(raw=raw):
                self.renderer.write_bytes(raw)
                self.assertIsNone(self.validate())

    def test_malformed_universal_bounds_and_slice_identity_are_rejected(self):
        cases = []
        raw = bytearray(universal())
        struct.pack_into(">I", raw, 4, 65)
        cases.append(bytes(raw))
        raw = bytearray(universal())
        struct.pack_into(">I", raw, 36, 999999)  # x64 slice offset exceeds file.
        cases.append(bytes(raw))
        raw = bytearray(universal())
        struct.pack_into(">I", raw, 36, 64)  # x64 overlaps the arm slice.
        cases.append(bytes(raw))
        raw = bytearray(universal())
        raw[128:160] = thin(0x0100000C)  # Declared x64 slice contains arm header.
        cases.append(bytes(raw))
        for raw in cases:
            with self.subTest(raw=raw[:48]):
                self.renderer.write_bytes(raw)
                self.assertIsNone(self.validate())

    def test_relative_client_directory_is_rejected(self):
        self.assertIsNone(overlay.validated_insertions(self.environment, "Steam Client"))

    def test_cli_prints_only_validated_value(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, self.environment, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(overlay.main(["--client-dir", str(self.client)]), 0)
        self.assertEqual(output.getvalue(), self.environment["STEAM_DYLD_INSERT_LIBRARIES"] + "\n")


@unittest.skipUnless(sys.platform.startswith("linux"), "Shell stubs cannot preserve DYLD_* through macOS SIP")
class LauncherOverlayTests(unittest.TestCase):
    """Linux shell stubs exercise routing without loading any Mach-O library.

    Only the macOS-specific Python host command is adapted in the fixture copy.
    No executable named wine or proton here contains runtime or graphics code.
    """
    def exercise(self, bridge=False, proton=False):
        with tempfile.TemporaryDirectory(prefix="gluon-overlay-launch-test-") as directory:
            root = Path(directory)
            home = root / "Gluon"
            library = root / "Library"
            game = library / "steamapps/common/Game/game.exe"
            game.parent.mkdir(parents=True)
            game.write_bytes(b"not executed")
            client = root / "Steam/Steam.AppBundle/Steam/Contents/MacOS"
            client.mkdir(parents=True)
            renderer = client / "gameoverlayrenderer.dylib"
            loader = client / "steamloader.dylib"
            renderer.write_bytes(thin())
            loader.write_bytes(thin())
            insertions = str(loader) + ":" + str(renderer)
            package = home / "lib/gluon"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("")
            shutil.copy2(Path(overlay.__file__), package / "overlay.py")
            launcher = root / "launcher"
            source = (Path(__file__).resolve().parents[1] / "tool/gluon").read_text()
            launcher.write_text(source.replace("/usr/bin/arch -arm64 /usr/bin/python3", shlex.quote(sys.executable)))
            launcher.chmod(0o755)
            runtime = root / "runtime"
            bin_dir = runtime / ("files/bin" if proton else "bin")
            bin_dir.mkdir(parents=True)
            trace = root / "trace.jsonl"
            stub = "#!%s\n" % sys.executable + """import json, os, pathlib, sys
role = pathlib.Path(sys.argv[0]).name
with open(os.environ['OVERLAY_TEST_TRACE'], 'a') as out:
    out.write(json.dumps({'role': role, 'args': sys.argv[1:],
                          'dyld': os.environ.get('DYLD_INSERT_LIBRARIES'),
                          'appid': os.environ.get('SteamOverlayGameId')}) + '\\n')
if role == 'wine' and sys.argv[1:3] == ['wineboot', '--init']:
    prefix = pathlib.Path(os.environ['WINEPREFIX'])
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / 'system.reg').write_text('fixture')
    for leaf in ('system32', 'syswow64'):
        (prefix / 'drive_c/windows' / leaf).mkdir(parents=True, exist_ok=True)
"""
            for path in (bin_dir / "wine", bin_dir / "wineserver"):
                path.write_text(stub)
                path.chmod(0o755)
            if proton:
                (runtime / "proton").write_text(stub)
                (runtime / "proton").chmod(0o755)
            if bridge:
                dlls = runtime / "lib/wine/x86_64-windows"
                dlls.mkdir(parents=True)
                for name in ("steam.exe", "lsteamclient.dll"):
                    (dlls / name).write_bytes(b"fixture")
            environment = dict(os.environ, GLUON_HOME=str(home), GLUON_RUNTIME=str(runtime),
                               SteamAppId="4705510", SteamOverlayGameId="4705510", SteamClientLaunch="1",
                               STEAM_ROOT=str(root / "Steam"), STEAM_DYLD_INSERT_LIBRARIES=insertions,
                               DYLD_INSERT_LIBRARIES="/must/not/reach/setup.dylib", OVERLAY_TEST_TRACE=str(trace))
            for name in ("GLUON_DRY_RUN", "GLUON_WINE_ROOT", "STEAM_COMPAT_DATA_PATH", "STEAM_CLIENT_CONFIG_FILE"):
                environment.pop(name, None)
            subprocess.run([str(launcher), "waitforexitandrun", str(game)], env=environment, check=True, timeout=5,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            return events, insertions

    def assert_game_only(self, bridge):
        events, insertions = self.exercise(bridge=bridge)
        games = [e for e in events if e["role"] == "wine" and e["args"][0] not in ("wineboot", "reg")]
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["dyld"], insertions)
        self.assertEqual(games[0]["appid"], "4705510")
        for event in events:
            if event is not games[0]:
                self.assertIsNone(event["dyld"])

    def test_plain_wine_only_restores_game_launch(self):
        self.assert_game_only(bridge=False)

    def test_bridge_only_restores_game_launch(self):
        self.assert_game_only(bridge=True)

    def test_proton_wrappers_remain_without_native_insertions(self):
        events, _ = self.exercise(proton=True)
        self.assertEqual([e["role"] for e in events], ["proton"])
        self.assertIsNone(events[0]["dyld"])


if __name__ == "__main__":
    unittest.main()

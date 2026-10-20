"""Stock bootstrap provenance fixtures; no Wine, service or game is executed."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gluon import service_policy as policy


class ServicePolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime, self.prefix = self.root / 'runtime', self.root / 'prefix'
        self.runtime.mkdir()
        for name in ('.gluon-runtime-build-key', '.gluon-wine-patches'):
            (self.runtime / name).write_text('a' * 64)
        self.windows = self.prefix / 'drive_c/windows/system32'
        self.windows.mkdir(parents=True)
        (self.windows / 'services.exe').write_bytes(b'MZ' + b'stock-service')
        (self.windows / 'core.dll').write_bytes(b'MZ' + b'stock-library')
        self.registry = self.prefix / 'system.reg'
        self.registry.write_text('WINE REGISTRY Version 2\n\n'
                                 '[System\\\\CurrentControlSet\\\\Services\\\\RpcSs] 123\n'
                                 '"ImagePath"="C:\\\\windows\\\\system32\\\\rpcss.exe"\n'
                                 '"Type"=dword:00000020\n')
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start(); self.addCleanup(environment.stop)
        policy.build_stock_policy(self.runtime, self.prefix)

    def allowed(self):
        return policy.warm_policy_allows(self.runtime, self.prefix)

    def test_exact_stock_prefix_and_cached_repeat_are_eligible(self):
        self.assertTrue(self.allowed())
        with patch.object(policy.hashlib, 'sha256', wraps=policy.hashlib.sha256):
            self.assertTrue(self.allowed())
        cache = json.loads((self.prefix / policy.CACHE).read_text())
        self.assertEqual(len(cache['files']), 2)

    def test_custom_service_and_shared_host_configuration_choose_cold(self):
        for section in ('System\\\\CurrentControlSet\\\\Services\\\\GameService',
                        'Software\\\\Microsoft\\\\Windows NT\\\\CurrentVersion\\\\Svchost'):
            with self.subTest(section=section):
                original = self.registry.read_text()
                self.registry.write_text(original + '\n[' + section + '] 1\n"custom"="external.dll"\n')
                self.assertFalse(self.allowed())
                self.registry.write_text(original)

    def test_startup_com_and_image_debugger_hooks_choose_cold(self):
        original = self.registry.read_text()
        for section in ('Software\\\\Classes\\\\CLSID\\\\custom',
                        'Software\\\\Microsoft\\\\Windows NT\\\\CurrentVersion\\\\Windows',
                        'Software\\\\Microsoft\\\\Windows NT\\\\CurrentVersion\\\\Image File Execution Options\\\\services.exe',
                        'Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Run'):
            with self.subTest(section=section):
                self.registry.write_text(original + '\n[' + section + '] 1\n"hook"="Z:\\\\external.dll"\n')
                self.assertFalse(self.allowed())
        self.registry.write_text(original)

    def test_native_registry_override_in_any_profile_chooses_cold(self):
        for filename in ('system.reg', 'user.reg', 'userdef.reg'):
            path = self.prefix / filename
            original = path.read_text() if path.exists() else 'WINE REGISTRY Version 2\n'
            path.write_text(original + '\n[Software\\\\Wine\\\\AppDefaults\\\\services.exe\\\\DllOverrides] 1\n"core"="native,builtin"\n')
            self.assertFalse(self.allowed())
            if filename == 'system.reg':
                path.write_text(original)
            else:
                path.unlink()

    def test_replaced_or_added_pe_is_not_authorized_by_unchanged_registry(self):
        self.assertTrue(self.allowed())
        binary = self.windows / 'core.dll'
        binary.write_bytes(b'MZ' + b'changed-by-game')
        self.assertFalse(self.allowed())
        binary.write_bytes(b'MZ' + b'stock-library')
        (self.windows / 'additional.dll').write_bytes(b'MZcustom')
        self.assertFalse(self.allowed())

    def test_startup_items_cannot_run_during_neutral_bootstrap(self):
        startup = self.prefix / 'drive_c/users/crossover/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup'
        startup.mkdir(parents=True)
        (startup / 'custom.lnk').write_bytes(b'external application')
        self.assertFalse(self.allowed())

    def test_native_environment_and_missing_or_stale_policy_choose_cold(self):
        for values in ({'WINEDLLOVERRIDES': 'core=n,b'}, {'WINEPATH': 'Z:\\external'},
                       {'WINELOADER': '/another/wine'}, {'WINEDLLPATH': '/external'}):
            with patch.dict(os.environ, values):
                self.assertFalse(self.allowed())
        (self.runtime / '.gluon-wine-patches').write_text('b' * 64)
        self.assertFalse(self.allowed())
        (self.runtime / policy.POLICY).unlink()
        self.assertFalse(self.allowed())

    def test_timestamps_are_ignored_but_actual_registry_values_are_not(self):
        self.registry.write_text(self.registry.read_text().replace('] 123', '] 456'))
        self.assertTrue(self.allowed())
        self.registry.write_text(self.registry.read_text().replace('00000020', '00000010'))
        self.assertFalse(self.allowed())

    def test_budget_exhaustion_is_cold_and_partial_cache_can_make_progress(self):
        with patch.object(policy, 'HASH_BYTES', 20):
            self.assertFalse(self.allowed())
            self.assertTrue(self.allowed())

    def test_policy_symlinks_and_malformed_records_never_authorize_retention(self):
        path = self.runtime / policy.POLICY
        original = path.read_bytes()
        for value in ('null', '{}', '{"schema": 1}', 'unfinished'):
            path.write_text(value)
            self.assertFalse(self.allowed())
        path.write_bytes(original)
        saved = path.with_suffix('.saved')
        path.rename(saved); path.symlink_to(saved)
        self.assertFalse(self.allowed())

    def test_legacy_game_helper_is_untouched_and_not_a_core_bootstrap_image(self):
        old = self.windows / 'steam.exe'
        old.write_bytes(b'MZolder game helper')
        self.assertTrue(self.allowed())
        self.assertEqual(old.read_bytes(), b'MZolder game helper')


if __name__ == '__main__':
    unittest.main()

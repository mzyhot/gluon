"""Cloud and upgrades retain their global barrier after owned idle retirement."""
import subprocess
import unittest
from unittest import mock

from gluon import cloud, runtime, session, steam


class WarmIdleIntegrationTests(unittest.TestCase):
    def inventory(self, status):
        return subprocess.CompletedProcess([], status, b'', b'')

    def test_upgrade_drains_owned_idle_servers_then_rechecks_process_inventory(self):
        events = []
        results = iter((self.inventory(0), self.inventory(1)))
        with mock.patch.object(steam, 'is_running', return_value=False), \
                mock.patch.object(session, 'drain_warm_sessions', side_effect=lambda home: events.append(('drain', home))), \
                mock.patch.object(runtime.subprocess, 'run', side_effect=lambda *a, **k: (events.append(('inventory', None)), next(results))[1]):
            self.assertTrue(runtime._idle(home='/fixture/Gluon'))
        self.assertEqual(events, [('inventory', None), ('drain', '/fixture/Gluon'), ('inventory', None)])

    def test_active_steam_never_drains_sessions_for_an_upgrade(self):
        with mock.patch.object(steam, 'is_running', return_value=True), \
                mock.patch.object(session, 'drain_warm_sessions') as drain:
            self.assertFalse(runtime._idle(home='/fixture/Gluon'))
            drain.assert_not_called()

    def test_read_only_idle_check_does_not_retire_warm_servers(self):
        with mock.patch.object(steam, 'is_running', return_value=False), \
                mock.patch.object(session, 'drain_warm_sessions') as drain, \
                mock.patch.object(runtime.subprocess, 'run', return_value=self.inventory(0)):
            self.assertFalse(runtime._idle(home='/fixture/Gluon', drain_warm=False))
            drain.assert_not_called()

    def test_remaining_active_or_foreign_wine_blocks_upgrade_and_cloud(self):
        for drained in (False, True):
            with self.subTest(drained=drained), \
                    mock.patch.object(steam, 'is_running', return_value=False), \
                    mock.patch.object(session, 'drain_warm_sessions', return_value=drained), \
                    mock.patch.object(runtime.subprocess, 'run', return_value=self.inventory(0)):
                self.assertFalse(runtime._idle(home='/fixture/Gluon'))
                with self.assertRaises(cloud.WineBusy):
                    cloud._wine_idle()

    def test_missing_or_stale_warm_records_do_not_block_an_absent_server(self):
        with mock.patch.object(steam, 'is_running', return_value=False), \
                mock.patch.object(session, 'drain_warm_sessions', return_value=False) as drain, \
                mock.patch.object(runtime.subprocess, 'run', return_value=self.inventory(1)):
            self.assertTrue(runtime._idle(home='/fixture/Gluon'))
            cloud._wine_idle()
            drain.assert_not_called()

    def test_cloud_retires_idle_wine_but_requires_confirmed_global_exit(self):
        with mock.patch.object(session, 'drain_warm_sessions') as drain, \
                mock.patch.object(cloud.subprocess, 'run', side_effect=[self.inventory(0), self.inventory(1)]):
            cloud._wine_idle()
            drain.assert_called_once_with(steam.GLUON_HOME)

    def test_unknown_process_inventory_never_allows_prefix_changes(self):
        with mock.patch.object(steam, 'is_running', return_value=False), \
                mock.patch.object(session, 'drain_warm_sessions', return_value=True), \
                mock.patch.object(runtime.subprocess, 'run', return_value=self.inventory(2)):
            with self.assertRaises(runtime.RuntimeSelectionError):
                runtime._idle(home='/fixture/Gluon')
            with self.assertRaises(cloud.WineBusy):
                cloud._wine_idle()

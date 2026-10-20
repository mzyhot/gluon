"""Offline warm-session authority and lifecycle cases; no Wine/game is executed."""
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from gluon import session


class WarmSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='gluon-warm-session-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / 'Gluon Home'
        self.compat = self.root / 'Game Prefix'
        self.prefix = self.compat / 'pfx-wine'
        self.prefix.mkdir(parents=True)
        self.lease = self.compat / session.LEASE_NAME
        self.lease.touch(mode=0o600)
        self.runtime = self.root / 'Runtime'
        (self.runtime / 'bin').mkdir(parents=True)
        for name in ('wine', 'wineserver'):
            path = self.runtime / 'bin' / name
            path.write_text('fixture, never executed')
            path.chmod(0o755)
        marker = self.runtime / 'licenses/gluon-wine-patches/APPLIED.txt'
        marker.parent.mkdir(parents=True)
        marker.write_text(session.WARM_PATCH + '\n')
        for name in ('.gluon-runtime-build-key', '.gluon-wine-patches'):
            (self.runtime / name).write_text('a' * 64 + '\n')
        self.environment = mock.patch.dict(os.environ, {'WINEMSYNC': '1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.bootstrap = mock.patch.object(session, '_bootstrap_services')
        self.bootstrap.start()
        self.addCleanup(self.bootstrap.stop)

    def record(self, *, phase="ready"):
        identity = session._identity(self.prefix, self.runtime)
        path = session._state_path(self.home, self.prefix)
        session._write_state(path, identity, phase=phase)
        return path

    def test_first_launch_starts_bounded_warm_server_after_full_wait(self):
        with mock.patch.object(session, '_run_helper', return_value=0) as run:
            session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         ['-w', '--gluon-session=120', '--wait-idle'])
        self.assertTrue(all(call.kwargs['prefix'] == self.prefix for call in run.call_args_list))
        self.assertTrue(run.call_args_list[1].kwargs['neutral'])
        path = session._state_path(self.home, self.prefix)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_repeat_launch_waits_for_real_user_idle_without_server_shutdown(self):
        self.record()
        with mock.patch.object(session, '_run_helper', side_effect=[0]) as run:
            session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         ['--wait-idle'])
        session._bootstrap_services.assert_not_called()
        self.assertNotIn('-k', [call.args[1] for call in run.call_args_list])

    def test_expired_server_uses_ordinary_wait_then_starts_new_warm_server(self):
        self.record()
        with mock.patch.object(session, '_run_helper', side_effect=[3, 0, 0, 0]) as run:
            session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         ['--wait-idle', '-w', '--gluon-session=120', '--wait-idle'])

    def test_explicit_cold_mode_atomically_drains_warm_server(self):
        self.record()
        with mock.patch.object(session, '_run_helper', return_value=0) as run:
            session.prepare_warm(self.home, self.prefix, self.runtime, enabled=False)
        self.assertEqual([call.args[1] for call in run.call_args_list], ['--check-running', '--kill-idle', '-w'])

    def test_busy_idle_only_shutdown_never_escalates_to_kill(self):
        self.record()
        with mock.patch.object(session, '_run_helper', return_value=2) as run:
            self.assertFalse(session.drain_warm_sessions(self.home))
        self.assertEqual([call.args[1] for call in run.call_args_list], ['--kill-idle'])

    def test_confirmed_idle_drain_prunes_exact_state(self):
        path = self.record()
        with mock.patch.object(session, '_run_helper', return_value=0) as run:
            self.assertTrue(session.drain_warm_sessions(self.home))
        self.assertEqual([call.args[1] for call in run.call_args_list], ['--kill-idle', '-w'])
        self.assertFalse(path.exists())

    def test_active_shell_lease_prevents_any_drain_command(self):
        self.record()
        with self.lease.open('a') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            with mock.patch.object(session, '_run_helper') as run:
                self.assertFalse(session.drain_warm_sessions(self.home))
        run.assert_not_called()

    def test_runtime_and_prefix_replacement_reject_reused_state(self):
        for change in ('runtime', 'prefix'):
            with self.subTest(change=change):
                path = self.record()
                if change == 'runtime':
                    (self.runtime / '.gluon-wine-patches').write_text('b' * 64 + '\n')
                else:
                    self.prefix.rename(self.compat / 'saved-prefix')
                    self.prefix.mkdir()
                with mock.patch.object(session, '_run_helper') as run:
                    self.assertFalse(session.drain_warm_sessions(self.home))
                run.assert_not_called()
                path.unlink()

    def test_msync_change_requires_confirmed_drain_before_new_warm_server(self):
        self.record()
        os.environ['WINEMSYNC'] = '0'
        with mock.patch.object(session, '_run_helper', return_value=0) as run:
            session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         ['--check-running', '--kill-idle', '-w', '--gluon-session=120', '--wait-idle'])

    def test_expired_old_runtime_may_be_replaced_only_after_server_absence(self):
        self.record()
        old_runtime = self.runtime.with_name("Old Runtime")
        self.runtime.rename(old_runtime)
        with mock.patch.object(session, '_run_helper', side_effect=[3, 0, 0]) as run:
            session.prepare_warm(self.home, self.prefix, old_runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         ['--check-running', '--gluon-session=120', '--wait-idle'])

    def test_live_unverifiable_old_runtime_remains_rejected(self):
        self.record()
        old_runtime = self.runtime.with_name("Old Runtime")
        self.runtime.rename(old_runtime)
        with mock.patch.object(session, '_run_helper', return_value=0) as run:
            with self.assertRaises((session.SessionError, OSError)):
                session.prepare_warm(self.home, self.prefix, old_runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list], ['--check-running'])

    def test_symlink_or_public_state_never_authorizes_shutdown(self):
        path = self.record()
        path.chmod(0o644)
        with mock.patch.object(session, '_run_helper') as run:
            self.assertFalse(session.drain_warm_sessions(self.home))
        run.assert_not_called()
        path.chmod(0o600)
        real = path.with_suffix('.saved')
        path.rename(real)
        path.symlink_to(real)
        with mock.patch.object(session, '_run_helper') as run:
            self.assertFalse(session.drain_warm_sessions(self.home))
        run.assert_not_called()

    def test_total_drain_budget_does_not_scale_with_records(self):
        self.record()
        # First helper consumes the complete global budget. No -w or second
        # record may extend a Cloud/migration caller's eight-second deadline.
        ticks = iter([0.0, 0.0, 0.0, 0.0, 9.0])
        with mock.patch.object(session.time, 'monotonic', side_effect=lambda: next(ticks)), \
             mock.patch.object(session, '_run_helper', return_value=0) as run:
            self.assertFalse(session.drain_warm_sessions(self.home))
        self.assertEqual([call.args[1] for call in run.call_args_list], ['--kill-idle'])

    def test_lease_lock_is_retained_by_parent_open_file_description(self):
        source = Path(__file__).resolve().parents[1]
        with self.lease.open('a') as lease:
            subprocess.run([sys.executable, '-m', 'gluon.session', '--lease-fd', str(lease.fileno()),
                            '--compat', str(self.compat)], env=dict(os.environ, PYTHONPATH=str(source)),
                           pass_fds=(lease.fileno(),), check=True, timeout=3)
            with self.lease.open('a') as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.lease.open('a') as other:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_new_server_bootstrap_failure_is_drained_and_record_removed(self):
        self.bootstrap.stop()
        with mock.patch.object(session, '_bootstrap_services', side_effect=session.SessionError("bootstrap failed")), \
             mock.patch.object(session, '_run_helper', return_value=0) as run:
            with self.assertRaisesRegex(session.SessionError, "bootstrap failed"):
                session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         ['-w', '--gluon-session=120', '--kill-idle', '-w'])
        self.assertFalse(session._state_path(self.home, self.prefix).exists())

    def test_new_server_capability_failure_is_drained(self):
        with mock.patch.object(session, '_run_helper', side_effect=[0, 0, 1, 0, 0]) as run:
            with self.assertRaisesRegex(session.SessionError, "capability"):
                session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         ['-w', '--gluon-session=120', '--wait-idle', '--kill-idle', '-w'])
        self.assertFalse(session._state_path(self.home, self.prefix).exists())

    def test_state_write_failure_cannot_create_an_anonymous_warm_server(self):
        with mock.patch.object(session, '_write_state', side_effect=OSError("read only")), \
             mock.patch.object(session, '_run_helper', return_value=0) as run:
            with self.assertRaises(OSError):
                session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list], ['-w'])

    def test_existing_server_is_preserved_if_our_capability_check_fails(self):
        self.record(phase="starting")
        with mock.patch.object(session, '_run_helper', side_effect=[0, 2, 1]) as run:
            with self.assertRaisesRegex(session.SessionError, "capability"):
                session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertNotIn('--kill-idle', [call.args[1] for call in run.call_args_list])
        self.assertTrue(session._state_path(self.home, self.prefix).exists())

    def test_interrupted_bootstrap_is_retried_before_ready_phase(self):
        path = self.record(phase="starting")
        with mock.patch.object(session, '_run_helper', side_effect=[0, 2, 0]) as run:
            session.prepare_warm(self.home, self.prefix, self.runtime)
        session._bootstrap_services.assert_called_once()
        self.assertEqual([call.args[1] for call in run.call_args_list],
                         ['--wait-idle', '--gluon-session=120', '--wait-idle'])
        self.assertEqual(session._read_state(path)['phase'], 'ready')

    def test_legacy_unphased_record_does_not_skip_neutral_bootstrap(self):
        path = self.record()
        previous = json.loads(path.read_text())
        previous.pop('phase')
        path.write_text(json.dumps(previous))
        with mock.patch.object(session, '_run_helper', side_effect=[0, 2, 0]):
            session.prepare_warm(self.home, self.prefix, self.runtime)
        session._bootstrap_services.assert_called_once()
        self.assertEqual(session._read_state(path)['phase'], 'ready')

    def test_failed_ready_publication_cleans_up_new_server(self):
        write = session._write_state
        def publish(path, identity, *, phase='ready'):
            if phase == 'ready':
                raise OSError('ready write failed')
            return write(path, identity, phase=phase)
        with mock.patch.object(session, '_write_state', side_effect=publish), \
             mock.patch.object(session, '_run_helper', return_value=0) as run:
            with self.assertRaisesRegex(OSError, 'ready write'):
                session.prepare_warm(self.home, self.prefix, self.runtime)
        self.assertEqual([call.args[1] for call in run.call_args_list][-2:], ['--kill-idle', '-w'])
        self.assertFalse(session._state_path(self.home, self.prefix).exists())

    def test_cloud_recheck_is_read_only_and_runs_under_the_lease(self):
        from gluon import cloud
        def check(appid, compat):
            self.assertEqual((appid, compat), ('123', str(self.compat)))
            with self.lease.open('a') as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return 'prefix'
        with self.lease.open('a') as owner, \
             mock.patch.object(cloud, 'check_app', side_effect=check), \
             mock.patch.object(cloud, 'prepare') as prepare, mock.patch.object(cloud, 'index') as index:
            self.assertEqual(session.main(['--lease-fd', str(owner.fileno()), '--compat', str(self.compat),
                                           '--check-cloud', '123', '--cloud-mode', 'prefix']), 0)
        prepare.assert_not_called()
        index.assert_not_called()

    def test_changed_cloud_mode_cannot_start_with_an_old_preparation(self):
        from gluon import cloud
        with mock.patch.object(cloud, 'check_app', return_value='native'):
            with self.assertRaisesRegex(session.SessionError, 'changed while'):
                session.check_cloud_after_lease('123', str(self.compat), 'prefix')

    def test_bootstrap_has_no_game_identity_and_disables_cmd_autorun(self):
        self.bootstrap.stop()
        with mock.patch.dict(os.environ, {"SteamAppId": "123", "SteamGameId": "456",
                                         "STEAM_COMPAT_APP_ID": "123", "GLUON_HOST_PID": "999",
                                         "GLUON_OVERLAY_INSERT": "/overlay", "DYLD_INSERT_LIBRARIES": "/overlay"}), \
             mock.patch.object(session.subprocess, 'Popen') as popen:
            popen.return_value.wait.return_value = 0
            session._bootstrap_services(self.runtime, self.prefix)
        arguments, options = popen.call_args
        self.assertEqual(arguments[0], [str(self.runtime / "bin/wine"), "cmd.exe", "/d", "/c", "exit", "0"])
        self.assertTrue(options["close_fds"])
        self.assertEqual(options["env"]["WINEPREFIX"], str(self.prefix))
        self.assertEqual(options["env"]["WINELOADER"], str(self.runtime / "bin/wine"))
        self.assertFalse(any(key.startswith(("Steam", "STEAM_", "GLUON_", "DYLD_")) for key in options["env"]))

    def test_unpatched_runtime_cannot_start_or_drain_warm_session(self):
        (self.runtime / 'licenses/gluon-wine-patches/APPLIED.txt').write_text('legacy\n')
        with mock.patch.object(session, '_run_helper') as run:
            with self.assertRaises(session.SessionError):
                session.prepare_warm(self.home, self.prefix, self.runtime)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

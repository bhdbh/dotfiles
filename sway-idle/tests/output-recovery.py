"""Check unlock events and output recovery without touching the live session."""
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

idle = SimpleNamespace(**runpy.run_path(str(Path(__file__).resolve().parents[1] /
                                         'sway-session-idle')))
# Functions keep runpy's globals; patch those rather than the namespace copy.
scope = idle.recover_outputs.__globals__


def output(name, active=True, power=True, **extra):
    return dict(name=name, active=active, power=power, **extra)


class RecoveryTests(unittest.TestCase):
    def test_both_outputs_and_lid_disabled_output(self):
        power = Mock()
        with patch.dict(scope, sway_query=Mock(return_value=[
                output('HDMI-A-1'), output('eDP-1'), output('DP-1', active=False),
                output('DP-2', power=False), output('VR-1', non_desktop=True)]),
                output_power=power), patch.object(idle.time, 'sleep'):
            idle.recover_outputs()
        self.assertEqual(power.call_args_list, [
            (('HDMI-A-1', False),), (('eDP-1', False),),
            (('HDMI-A-1', True),), (('eDP-1', True),)])

    def test_restore_after_power_off_failure(self):
        power = Mock(side_effect=[None, RuntimeError('off failed'), None, None])
        with patch.dict(scope, sway_query=Mock(return_value=[
                output('HDMI-A-1'), output('eDP-1')]), output_power=power):
            with self.assertRaisesRegex(RuntimeError, 'off failed'):
                idle.recover_outputs()
        self.assertEqual(power.call_args_list[-2:], [
            (('HDMI-A-1', True),), (('eDP-1', True),)])

    def test_one_restore_failure_still_restores_other_output(self):
        power = Mock(side_effect=[None, None, RuntimeError('on failed'), None])
        with patch.dict(scope, sway_query=Mock(return_value=[
                output('HDMI-A-1'), output('eDP-1')]), output_power=power), \
                patch.object(idle.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'on failed'):
                idle.recover_outputs()
        self.assertEqual(power.call_args_list[-1], (('eDP-1', True),))

    def test_sway_command_failures_are_reported(self):
        with patch.object(idle.subprocess, 'check_output', return_value='[{"success":false}]'):
            with self.assertRaisesRegex(RuntimeError, 'command failed'):
                idle.output_power('eDP-1', False)

    def test_process_query_errors_do_not_look_like_unlock(self):
        with patch.object(idle.subprocess, 'run', return_value=SimpleNamespace(returncode=2)):
            with self.assertRaisesRegex(RuntimeError, 'locker processes'):
                idle.lock_processes()

    def make_daemon(self):
        daemon = idle.Idle.__new__(idle.Idle)
        daemon.return_from_lock = False
        daemon.recovery_timer = None
        daemon.recovery_deadline = idle.time.monotonic() + 10
        daemon.tty = 'tty1'
        return daemon

    def event(self, daemon, active):
        values = SimpleNamespace(unpack=lambda: ('org.freedesktop.login1.Session',
                                                {'Active': active}, []))
        daemon.session_event(None, None, None, None, None, values)

    def test_unlock_is_scheduled_once_after_console_lock(self):
        daemon = self.make_daemon()
        with patch.dict(scope, lock_processes=Mock(return_value=['123'])), \
                patch.object(idle.GLib, 'timeout_add', return_value=7) as timer:
            self.event(daemon, False)
            self.event(daemon, True)
            self.event(daemon, True)
        self.assertTrue(daemon.return_from_lock)
        timer.assert_called_once_with(400, daemon.recover_after_unlock)

    def test_ordinary_vt_switch_does_not_cycle_outputs(self):
        daemon = self.make_daemon()
        with patch.dict(scope, lock_processes=Mock(return_value=[])), \
                patch.object(idle.GLib, 'timeout_add') as timer:
            self.event(daemon, False)
            self.event(daemon, True)
        timer.assert_not_called()

    def test_locking_again_cancels_pending_recovery(self):
        daemon = self.make_daemon()
        daemon.recovery_timer = 7
        with patch.dict(scope, lock_processes=Mock(return_value=['456'])), \
                patch.object(idle.GLib, 'source_remove') as remove:
            self.event(daemon, False)
        remove.assert_called_once_with(7)
        self.assertIsNone(daemon.recovery_timer)

    def test_wait_for_locker_and_correct_console(self):
        daemon = self.make_daemon()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(scope, runtime=lambda: Path(temporary),
                           lock_processes=Mock(side_effect=[['123'], [], []]),
                           recover_outputs=Mock()) as patched, \
                patch.object(Path, 'read_text', side_effect=['tty2\n', 'tty1\n']):
            self.assertTrue(daemon.recover_after_unlock())
            patched['recover_outputs'].assert_not_called()
            self.assertTrue(daemon.recover_after_unlock())
            patched['recover_outputs'].assert_not_called()
            self.assertFalse(daemon.recover_after_unlock())
            patched['recover_outputs'].assert_called_once()

    def test_failed_authentication_never_refreshes_outputs(self):
        daemon = self.make_daemon()
        daemon.return_from_lock = True
        daemon.recovery_timer = 7
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(scope, runtime=lambda: Path(temporary),
                           lock_processes=Mock(return_value=[]), recover_outputs=Mock()) as patched, \
                patch.object(Path, 'read_text', return_value='tty2\n'):
            self.assertTrue(daemon.recover_after_unlock())
            daemon.recovery_deadline = 0
            self.assertFalse(daemon.recover_after_unlock())
            patched['recover_outputs'].assert_not_called()
        self.assertIsNone(daemon.recovery_timer)


if __name__ == '__main__':
    unittest.main()

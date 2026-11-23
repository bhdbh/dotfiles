"""Security-sensitive idle transitions across AC changes and suspend."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from idle import handle_event, invoke_suspend, on_battery, power_changed, command, watch, retry_pending_suspend


class IdlePolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runtime = Path(self.directory.name)
        self.lock = Mock()
        self.suspend = Mock()

    def event(self, event, battery=False):
        handle_event(event, self.runtime, battery=battery, lock=self.lock, suspend=self.suspend)

    def test_five_minutes_on_battery_locks_and_blanks(self):
        self.event('short', battery=True)
        self.lock.assert_called_once_with('idle')

    def test_five_minutes_on_mains_does_not_lock(self):
        self.event('short')
        self.lock.assert_not_called()

    def test_thirty_minutes_on_mains_locks_and_blanks(self):
        self.event('long')
        self.lock.assert_called_once_with('idle')

    def test_disconnect_after_five_minutes_locks_without_new_timer(self):
        self.event('short')
        power_changed(False, True, self.runtime, self.lock, self.suspend)
        self.lock.assert_called_once_with('idle')

    def test_disconnect_before_five_minutes_does_not_lock(self):
        power_changed(False, True, self.runtime, self.lock, self.suspend)
        self.lock.assert_not_called()

    def test_connecting_mains_does_not_lock_or_unlock(self):
        self.event('short')
        power_changed(True, False, self.runtime, self.lock, self.suspend)
        self.lock.assert_not_called()

    def test_activity_clears_the_five_minute_marker(self):
        self.event('short')
        self.event('active')
        power_changed(False, True, self.runtime, self.lock, self.suspend)
        self.lock.assert_not_called()

    def test_suspend_locks_immediately_on_either_power_source(self):
        self.event('before-sleep')
        self.lock.assert_called_once_with('idle')

    def test_resume_requests_wake_without_unlocking(self):
        self.event('short')
        self.event('after-resume')
        self.lock.assert_called_once_with('wake')
        self.assertFalse((self.runtime / 'five-minutes-idle').exists())

    def test_logind_lock_requests_a_real_console_prompt(self):
        self.event('lock')
        self.lock.assert_called_once_with('lock')

    def test_connected_supply_wins_over_disconnected_usb(self):
        for name, online in (('ACAD', '1'), ('USB', '0')):
            supply = self.runtime / name
            supply.mkdir()
            (supply / 'online').write_text(online)
        self.assertFalse(on_battery(self.runtime))
        (self.runtime / 'ACAD' / 'online').write_text('0')
        self.assertTrue(on_battery(self.runtime))

    def test_four_timers_share_one_waiting_swayidle_process(self):
        args = command()
        self.assertEqual(args[:4], ['swayidle', '-w', '-C', '/dev/null'])
        self.assertIn('300', args)
        self.assertIn('600', args)
        self.assertIn('1800', args)
        self.assertIn('3600', args)
        self.assertIn('before-sleep', args)
        self.assertIn('after-resume', args)
        self.assertIn('lock', args)

    def test_ten_minutes_on_battery_requests_suspend(self):
        self.event('suspend-short', battery=True)
        self.suspend.assert_called_once_with()

    def test_ten_minutes_on_mains_does_not_suspend(self):
        self.event('suspend-short')
        self.suspend.assert_not_called()

    def test_one_hour_on_mains_requests_suspend(self):
        self.event('suspend-long')
        self.suspend.assert_called_once_with()

    def test_unplugging_after_ten_minutes_suspends_immediately(self):
        self.event('short')
        self.event('suspend-short')
        power_changed(False, True, self.runtime, self.lock, self.suspend)
        self.suspend.assert_called_once_with()
        self.lock.assert_not_called()

    def test_connecting_after_ten_minutes_does_not_suspend(self):
        self.event('suspend-short')
        power_changed(True, False, self.runtime, self.lock, self.suspend)
        self.suspend.assert_not_called()

    def test_activity_clears_suspend_deadline_before_unplugging(self):
        self.event('suspend-short')
        self.event('active')
        power_changed(False, True, self.runtime, self.lock, self.suspend)
        self.suspend.assert_not_called()

    def test_power_change_and_one_hour_timer_do_not_duplicate_suspend(self):
        self.event('suspend-short')
        power_changed(False, True, self.runtime, self.lock, self.suspend)
        self.event('suspend-long')
        self.suspend.assert_called_once_with()

    def test_resume_allows_a_new_idle_cycle(self):
        self.event('suspend-short', battery=True)
        self.event('after-resume')
        self.event('suspend-short', battery=True)
        self.assertEqual(self.suspend.call_count, 2)
        self.lock.assert_called_once_with('wake')

    def test_failed_suspend_does_not_prevent_a_retry(self):
        self.suspend.side_effect = [RuntimeError('Suspend failed'), None]
        with self.assertRaisesRegex(RuntimeError, 'Suspend failed'):
            self.event('suspend-short', battery=True)
        self.assertFalse((self.runtime / 'suspend-requested').exists())
        self.event('suspend-short', battery=True)
        self.assertEqual(self.suspend.call_count, 2)

    def test_watcher_retries_failed_deadline_without_another_power_transition(self):
        self.suspend.side_effect = [subprocess.CalledProcessError(1, 'suspend'), None]
        with patch('idle.time.monotonic', return_value=100):
            with self.assertRaises(subprocess.CalledProcessError):
                self.event('suspend-short', battery=True)
        retry_pending_suspend(self.runtime, True, now=129, suspend=self.suspend)
        self.assertEqual(self.suspend.call_count, 1)
        retry_pending_suspend(self.runtime, True, now=130, suspend=self.suspend)
        self.assertEqual(self.suspend.call_count, 2)
        self.assertFalse((self.runtime / 'suspend-retry-at').exists())

    def test_activity_cancels_a_failed_suspend_retry(self):
        self.suspend.side_effect = [RuntimeError('transient'), None]
        with self.assertRaises(RuntimeError):
            self.event('suspend-short', battery=True)
        self.event('active')
        retry_pending_suspend(self.runtime, True, now=float('inf'), suspend=self.suspend)
        self.assertEqual(self.suspend.call_count, 1)

    def test_ac_return_cancels_battery_retry_but_not_one_hour_deadline(self):
        self.suspend.side_effect = [RuntimeError('transient'), None]
        with self.assertRaises(RuntimeError):
            self.event('suspend-short', battery=True)
        retry_pending_suspend(self.runtime, False, now=float('inf'), suspend=self.suspend)
        self.assertEqual(self.suspend.call_count, 1)
        self.assertFalse((self.runtime / 'suspend-retry-at').exists())
        self.suspend.side_effect = [RuntimeError('transient'), None]
        with self.assertRaises(RuntimeError):
            self.event('suspend-long')
        retry_pending_suspend(self.runtime, False, now=float('inf'), suspend=self.suspend)
        self.assertEqual(self.suspend.call_count, 3)

    def test_repeated_failure_backs_off_and_success_stays_deduplicated(self):
        self.suspend.side_effect = [RuntimeError('transient'), RuntimeError('again'), None]
        with patch('idle.time.monotonic', return_value=100):
            with self.assertRaises(RuntimeError):
                self.event('suspend-short', battery=True)
        with patch('idle.time.monotonic', return_value=130):
            with self.assertRaises(RuntimeError):
                retry_pending_suspend(self.runtime, True, now=130, suspend=self.suspend)
        retry_pending_suspend(self.runtime, True, now=159, suspend=self.suspend)
        self.assertEqual(self.suspend.call_count, 2)
        retry_pending_suspend(self.runtime, True, now=160, suspend=self.suspend)
        self.event('suspend-long')
        self.assertEqual(self.suspend.call_count, 3)

    def test_suspend_uses_only_the_exact_privileged_action(self):
        with patch('idle.subprocess.run') as run:
            invoke_suspend()
        run.assert_called_once_with(
            ['sudo', '-n', '/usr/local/libexec/sway-tty-lock', 'suspend'], check=True)

    def test_waking_a_locked_tty_restarts_the_idle_timers(self):
        first, second = Mock(), Mock()
        first.poll.return_value = None
        second.poll.return_value = 0

        def resume_while_waiting(timeout):
            if timeout == 1:
                self.event('suspend-short')
                self.event('after-resume')
                raise subprocess.TimeoutExpired('swayidle', timeout)
            return 0

        first.wait.side_effect = resume_while_waiting
        with patch('idle.subprocess.Popen', side_effect=[first, second]) as start, \
                patch('idle.signal.signal'), patch('idle.on_battery', return_value=False):
            self.assertEqual(watch(self.runtime), 0)
        self.assertEqual(start.call_count, 2)
        first.terminate.assert_called_once_with()
        self.lock.assert_called_once_with('wake')
        self.assertFalse((self.runtime / 'ten-minutes-idle').exists())
        self.assertFalse((self.runtime / 'restart-idle-timers').exists())


if __name__ == '__main__':
    unittest.main()

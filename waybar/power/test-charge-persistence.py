"""Charge persistence regressions using temporary files, without real firmware writes."""
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).with_name('acer-charge-helper')
helper = SimpleNamespace(**runpy.run_path(str(SOURCE)))


def toggle_in_process(paths):
    return helper.ChargeControl(*map(Path, paths)).toggle()


class ChargePersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.mode = root / 'health_mode'
        self.config = root / 'waybar-acer-charge.conf'
        self.lock = root / 'run' / 'lock'
        self.mode.write_text('1\n')
        self.controller = helper.ChargeControl(self.mode, self.config, self.lock)

    def test_both_choices_survive_firmware_reset_and_process_restart(self):
        for selected in ('0', '1'):
            with self.subTest(selected=selected):
                self.controller.save(selected)
                self.mode.write_text('1\n' if selected == '0' else '0\n')
                restarted = helper.ChargeControl(self.mode, self.config, self.lock)
                restarted.restore()
                self.assertEqual(restarted.current(), selected)
                self.assertEqual(restarted.saved(), selected)

    def test_first_install_keeps_current_choice(self):
        for current in ('0', '1'):
            with self.subTest(current=current):
                self.config.unlink(missing_ok=True)
                self.mode.write_text(current + '\n')
                self.controller.restore(force=True)
                self.assertEqual(self.controller.current(), current)
                self.assertEqual(self.controller.saved(), current)

    def test_forced_resume_writes_even_when_driver_cache_matches(self):
        self.controller.save('1')
        with patch.object(Path, 'write_text', autospec=True, return_value=2) as write:
            self.controller.restore(force=True)
        write.assert_called_once_with(self.mode, '1\n')

    def test_periodic_check_does_not_rewrite_an_unchanged_mode(self):
        self.controller.save('1')
        with patch.object(Path, 'write_text', autospec=True) as write:
            self.controller.restore()
        write.assert_not_called()

    def test_toggle_updates_firmware_and_persisted_choice(self):
        self.controller.save('1')
        for target in ('0', '1'):
            self.assertEqual(self.controller.toggle(), target)
            self.assertEqual(self.controller.current(), target)
            self.assertEqual(self.controller.saved(), target)
            self.controller.restore(force=True)
            self.assertEqual(self.controller.current(), target)

    def test_rejected_firmware_change_does_not_replace_saved_choice(self):
        self.controller.save('1')
        with patch.object(Path, 'write_text', autospec=True, return_value=2):
            with self.assertRaises(RuntimeError):
                self.controller.toggle()
        self.assertEqual(self.controller.saved(), '1')

    def test_failed_save_restores_previous_hardware_and_choice(self):
        self.controller.save('1')
        with patch('os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.controller.toggle()
        self.assertEqual(self.controller.current(), '1')
        self.assertEqual(self.controller.saved(), '1')
        self.assertEqual(list(self.config.parent.glob('.waybar-acer-charge-*')), [])

    def test_invalid_saved_mode_never_overrides_firmware(self):
        for value in ('', 'enable_health_mode=0', 'options acer-wmi-battery enable_health_mode=-1\n'):
            with self.subTest(value=value):
                self.config.write_text(value)
                with self.assertRaises(ValueError):
                    self.controller.restore(force=True)
                self.assertEqual(self.controller.current(), '1')

    def test_missing_driver_does_not_forget_saved_choice(self):
        self.controller.save('1')
        self.mode.unlink()
        with self.assertRaises(OSError):
            self.controller.restore()
        self.assertEqual(self.controller.saved(), '1')
        self.mode.write_text('0\n')
        self.controller.restore()
        self.assertEqual(self.controller.current(), '1')

    def test_concurrent_clicks_and_restores_are_serialized(self):
        self.controller.save('1')
        paths = tuple(map(str, (self.mode, self.config, self.lock)))
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('fork')) as pool:
            changes = [pool.submit(toggle_in_process, paths) for _ in range(12)]
            for _ in range(12):
                self.controller.restore(force=True)
            self.assertEqual([job.result() for job in changes].count('0'), 6)
        self.assertEqual(self.controller.current(), '1')
        self.assertEqual(self.controller.saved(), '1')

    def test_service_applies_on_startup_and_resume_without_waybar(self):
        controller = Mock()
        callbacks = {}
        bus = Mock()

        def subscribe(*args):
            callbacks['sleep'] = args[-2]

        def timer(_seconds, callback):
            callbacks['timer'] = callback

        def run():
            controller.restore.assert_called_once_with(force=True)
            controller.restore.reset_mock()
            callbacks['sleep'](None, None, None, None, None,
                               SimpleNamespace(unpack=lambda: (True,)), None)
            controller.restore.assert_not_called()
            callbacks['sleep'](None, None, None, None, None,
                               SimpleNamespace(unpack=lambda: (False,)), None)
            controller.restore.assert_called_once_with(force=True)
            controller.restore.reset_mock()
            callbacks['timer']()
            controller.restore.assert_called_once_with(force=False)

        bus.signal_subscribe.side_effect = subscribe
        gio = SimpleNamespace(bus_get_sync=lambda *_: bus, BusType=SimpleNamespace(SYSTEM=0),
                              DBusSignalFlags=SimpleNamespace(NONE=0))
        glib = SimpleNamespace(MainLoop=lambda: SimpleNamespace(run=run, quit=Mock()),
                               SOURCE_REMOVE=False, SOURCE_CONTINUE=True, PRIORITY_DEFAULT=0,
                               unix_signal_add=Mock(), timeout_add_seconds=timer)
        repository = SimpleNamespace(Gio=gio, GLib=glib)
        with patch.dict(sys.modules, {'gi.repository': repository}):
            helper.watch(controller)


class BatteryActionTests(unittest.TestCase):
    def setUp(self):
        self.battery = runpy.run_path(str(SOURCE.parent.parent / 'scripts' / 'battery.py'))
        self.namespace = self.battery['toggle_charge'].__globals__

    def test_click_checks_helper_result_after_concurrent_restore(self):
        with patch.dict(self.namespace, plugged_in=lambda: True, read=lambda _: '0'):
            with patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout='100\n')):
                # Earlier code inferred 80% from its stale initial read of 100%.
                self.battery['toggle_charge']()

    def test_unplugged_click_does_not_change_saved_mode(self):
        with patch.dict(self.namespace, plugged_in=lambda: False):
            with patch.object(subprocess, 'run') as execute:
                self.battery['toggle_charge']()
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()

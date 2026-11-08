"""Verify fallback transitions and recovery without changing real CPU settings."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cpu_power', Path(__file__).with_name('cpu-power.py'))
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)
PLACEHOLDER = [{'Profile': name, 'Driver': 'placeholder'}
               for name in ('balanced', 'power-saver')]


class CpuPowerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'cpufreq'
        self.root.mkdir()
        (self.root / 'boost').write_text('1\n')
        for number, governor in enumerate(('schedutil', 'ondemand')):
            p = self.root / f'policy{number}'
            p.mkdir()
            (p / 'scaling_driver').write_text('acpi-cpufreq\n')
            (p / 'scaling_governor').write_text(governor + '\n')
            (p / 'scaling_available_governors').write_text('schedutil ondemand conservative powersave\n')
        self.state = Path(self.temporary.name) / 'run' / 'original.json'
        self.controller = power.CpuPower(self.root, self.state)

    def values(self):
        return (power.read(self.root / 'boost'),
                [power.read(p / 'scaling_governor') for p in self.controller.policies()])

    def test_saving_and_balanced_restore_each_original_value(self):
        self.controller.apply('power-saver', PLACEHOLDER)
        self.assertEqual(self.values(), ('0', ['conservative', 'conservative']))
        self.controller.apply('balanced', PLACEHOLDER)
        self.assertEqual(self.values(), ('1', ['schedutil', 'ondemand']))
        self.assertFalse(self.state.exists())

    def test_repeat_and_restart_keep_original_snapshot(self):
        self.controller.apply('power-saver', PLACEHOLDER)
        original = json.loads(self.state.read_text())
        self.controller.apply('power-saver', PLACEHOLDER)
        restarted = power.CpuPower(self.root, self.state)
        restarted.apply('power-saver', PLACEHOLDER)
        self.assertEqual(json.loads(self.state.read_text()), original)
        restarted.restore()
        self.assertEqual(self.values(), ('1', ['schedutil', 'ondemand']))

    def test_turbo_previously_disabled_stays_disabled_after_restore(self):
        (self.root / 'boost').write_text('0\n')
        self.controller.apply('power-saver', PLACEHOLDER)
        self.controller.apply('balanced', PLACEHOLDER)
        self.assertEqual(self.values(), ('0', ['schedutil', 'ondemand']))

    def test_native_driver_is_not_overridden(self):
        self.controller.apply('power-saver', [{'Driver': 'amd_pstate', 'CpuDriver': 'amd_pstate'}])
        self.assertEqual(self.values(), ('1', ['schedutil', 'ondemand']))
        self.assertFalse(self.state.exists())

    def test_native_driver_appearing_releases_fallback(self):
        self.controller.apply('power-saver', PLACEHOLDER)
        self.controller.apply('power-saver', [{'Driver': 'amd_pstate', 'CpuDriver': 'amd_pstate'}])
        self.assertEqual(self.values(), ('1', ['schedutil', 'ondemand']))

    def test_unsupported_cpufreq_driver_is_not_changed(self):
        (self.root / 'policy0/scaling_driver').write_text('amd-pstate-epp\n')
        self.controller.apply('power-saver', PLACEHOLDER)
        self.assertEqual(self.values(), ('1', ['schedutil', 'ondemand']))

    def test_dbus_owner_loss_restores_original_settings(self):
        self.controller.apply('power-saver', PLACEHOLDER)
        self.controller.apply(None, [])
        self.assertEqual(self.values(), ('1', ['schedutil', 'ondemand']))

    def test_partial_failure_rolls_back_cpu_changes(self):
        original_write = power.write

        def failing_write(path, value):
            if path.parent.name == 'policy1' and value == 'conservative':
                raise OSError('CPU setting rejected')
            original_write(path, value)

        with patch.object(power, 'write', side_effect=failing_write):
            with self.assertRaises(OSError):
                self.controller.apply('power-saver', PLACEHOLDER)
        self.assertEqual(self.values(), ('1', ['schedutil', 'ondemand']))
        self.assertFalse(self.state.exists())

    def test_new_activation_snapshots_updated_user_settings(self):
        self.controller.apply('power-saver', PLACEHOLDER)
        self.controller.apply('balanced', PLACEHOLDER)
        (self.root / 'policy0/scaling_governor').write_text('ondemand\n')
        self.controller.apply('power-saver', PLACEHOLDER)
        self.controller.apply('balanced', PLACEHOLDER)
        self.assertEqual(self.values(), ('1', ['ondemand', 'ondemand']))


if __name__ == '__main__':
    unittest.main()

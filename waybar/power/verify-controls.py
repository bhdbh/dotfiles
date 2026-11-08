"""Verify power profile switching and restore the original profile."""
import json
import runpy
import subprocess
from pathlib import Path
import time

controls = runpy.run_path(str(Path.home() / '.config/waybar/scripts/battery.py'))
initial_profile = controls['profile']()
assert initial_profile in ('balanced', 'power-saver', 'performance'), initial_profile


def cpu_state():
    root = Path('/sys/devices/system/cpu/cpufreq')
    return ((root / 'boost').read_text().strip(),
            {p.name: (p / 'scaling_governor').read_text().strip()
             for p in sorted(root.glob('policy[0-9]*'))})


def wait_for(predicate, description):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(description)
        time.sleep(0.05)


cpu_helper = Path('/usr/local/libexec/waybar-cpu-power').exists()
if cpu_helper:
    subprocess.run(['systemctl', 'is-active', '--quiet', 'waybar-cpu-power.service'], check=True)
    if initial_profile == 'power-saver':
        wait_for(lambda: cpu_state()[0] == '0'
                 and all(v == 'conservative' for v in cpu_state()[1].values()),
                 'The CPU helper has not applied the initial saving profile')
initial_cpu = cpu_state() if cpu_helper else None
balanced_cpu = initial_cpu
if cpu_helper and initial_profile == 'power-saver':
    try:
        original = json.loads(Path('/run/waybar-cpu-power/original.json').read_text())
        balanced_cpu = (original['boost'], original['governors'])
    except PermissionError:
        # The root installer can check this snapshot; ordinary users cannot.
        balanced_cpu = None

try:
    controls['toggle_profile']()
    target = 'balanced' if initial_profile == 'power-saver' else 'power-saver'
    assert controls['profile']() == target
    if target == 'power-saver':
        assert '#f97316' in controls['battery_markup']()
    if cpu_helper:
        expected = ('0', {name: 'conservative' for name in initial_cpu[1]}) if target == 'power-saver' else balanced_cpu
        if expected is not None:
            wait_for(lambda: cpu_state() == expected,
                     'The CPU helper did not apply the target profile')
        print('CPU after toggle:', cpu_state(), flush=True)
    print('Power profile toggle verified:', initial_profile, '->', target, flush=True)
finally:
    subprocess.run(['powerprofilesctl', 'set', initial_profile], check=True)
    if cpu_helper:
        wait_for(lambda: cpu_state() == initial_cpu,
                 'The original CPU settings were not restored')

print('Original power profile restored:', initial_profile, flush=True)
print('Battery:', controls['battery_markup'](), flush=True)

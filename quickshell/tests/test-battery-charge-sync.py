#!/usr/bin/env python3
"""Exercise the actual QML transition component without touching the battery."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

fixture = Path(__file__).with_suffix('.qml')
with tempfile.TemporaryDirectory(prefix='quickshell-charge-test-') as directory:
    shutil.copyfile(fixture, Path(directory, 'shell.qml'))
    shutil.copyfile(fixture.parent.parent / 'BatteryChargeSync.qml',
                    Path(directory, 'BatteryChargeSync.qml'))
    result = subprocess.run(
        ['quickshell', '--path', directory, '--no-color'],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10,
    )
if result.returncode or 'PASS battery charge synchronization' not in result.stdout:
    raise SystemExit(result.stdout)
print('PASS: confirmed transitions, both limits, invalid states, unplug, '
      'full battery, reversal, cancellation and 5-second timeout')

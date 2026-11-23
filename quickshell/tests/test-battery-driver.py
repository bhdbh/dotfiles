#!/usr/bin/env python3
"""Compile the real WMI parser against a fake bus; never load a module."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = root / 'vendor/acer-wmi-battery/acer-wmi-battery.c'
with tempfile.TemporaryDirectory(prefix='quickshell-driver-test-') as directory:
    temporary = Path(directory)
    headers = temporary / 'linux'
    headers.mkdir()
    for name in ('init', 'kernel', 'limits', 'string', 'module', 'acpi', 'version', 'wmi', 'unaligned'):
        (headers / (name + '.h')).write_text('/* Kernel API supplied by userspace test harness. */\n')
    binary = temporary / 'battery-driver-test'
    subprocess.run(['gcc', '-std=gnu11', '-g', '-O1', '-I', str(temporary),
                    '-DACER_DRIVER_SOURCE="' + str(source) + '"',
                    str(root / 'tests/battery-driver.c'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

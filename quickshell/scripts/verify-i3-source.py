#!/usr/bin/env python3
"""Check the pinned I3 patch and a source tree; never modify or run Quickshell."""
import argparse
import hashlib
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source_tree', type=Path, help='extracted Quickshell source tree, not a running process')
args = parser.parse_args()
vendor = Path(__file__).resolve().parents[1] / 'vendor/quickshell-i3'
for line in (vendor / 'SHA256SUMS').read_text().splitlines():
    expected, name = line.split(maxsplit=1)
    if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != expected:
        raise SystemExit('Checksum mismatch: ' + name)
print('PASS pinned patch and license checksums')

source = args.source_tree.resolve(strict=True)
checks = {
    'monitor.cpp': ('&I3Monitor::onActiveWorkspaceDestroyed', 'this->bActiveWorkspace = nullptr'),
    'workspace.cpp': ('&I3Workspace::onMonitorDestroyed', 'this->bMonitor = nullptr'),
}
if all(all(marker in (source / 'src/x11/i3/ipc' / name).read_text() for marker in markers)
       for name, markers in checks.items()):
    print('PASS source contains destruction hooks for both pointers; this does not verify an installed binary')
else:
    result = subprocess.run(['git', 'apply', '--check', str(vendor / 'pointer-lifetime.patch')],
                            cwd=source, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('Fix markers absent; patch needs review for this source tree:\n' + result.stderr)
    raise SystemExit('FIX MISSING: pinned upstream patch applies cleanly (check only; no files changed)')

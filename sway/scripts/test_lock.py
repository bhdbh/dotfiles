"""Run lockers against fake commands and temporary runtimes, never a live seat."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''#!/usr/bin/python3
import json, os
from pathlib import Path
import subprocess, sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps([name, args]) + '\n')
if name == 'sudo':
    sys.exit(int(os.environ.get('ROOT_RC', '1')))
if name == 'timeout':
    command = args[2:]
    if command[0] == 'sudo' and os.environ.get('HELPER_TIMEOUT'):
        sys.exit(124)
    if command[0] == 'swaylock' and os.environ.get('FALLBACK_TIMEOUT'):
        sys.exit(124)
    sys.exit(subprocess.run(command).returncode)
if name == 'swaylock':
    code = int(os.environ.get('FALLBACK_RC', '0'))
    if code == 0:
        Path(os.environ['PID_FILE']).write_text('8123')
    sys.exit(code)
if name == 'pgrep':
    pid = Path(os.environ['PID_FILE'])
    if pid.exists():
        print(pid.read_text())
        sys.exit(0)
    sys.exit(1)
if name == 'swaymsg':
    sys.exit(int(os.environ.get('OUTPUT_RC', '0')))
if name == 'systemctl':
    if args == ['suspend']:
        # A before-sleep callback must be able to take the same mutex.
        path = os.environ.get('CHECK_UNLOCKED')
        if path:
            sys.exit(subprocess.run(['/usr/bin/flock', '--nonblock', path, '/usr/bin/true']).returncode)
        sys.exit(0)
    if args[:1] == ['is-active']:
        sys.exit(0)
    sys.exit(0)
if name == 'id':
    print('0')
    sys.exit(0)
raise RuntimeError(name)
'''


class LockFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ['sudo', 'timeout', 'swaylock', 'pgrep', 'swaymsg', 'systemctl']:
            script = self.bin / name
            script.write_text(MOCK)
            script.chmod(0o755)
        self.runtime = self.root / 'run'
        self.runtime.mkdir()
        self.log = self.root / 'calls'
        self.env = dict(os.environ, PATH=f'{self.bin}:/usr/bin:/bin',
                        XDG_RUNTIME_DIR=str(self.runtime), CALL_LOG=str(self.log),
                        PID_FILE=str(self.root / 'pid'))
        self.env.pop('NOTIFY_SOCKET', None)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_lock(self, mode, **env):
        return subprocess.run(['/bin/sh', str(ROOT / 'scripts/lock.sh'), mode],
                              env=dict(self.env, **env), capture_output=True, text=True,
                              timeout=5)

    def ready_fallback(self):
        directory = self.runtime / 'sway-idle'
        directory.mkdir(exist_ok=True)
        (directory / 'fallback-ready').write_text('8123\n')
        (directory / 'fallback-displays-off').touch()
        Path(self.env['PID_FILE']).write_text('8123')

    def test_primary_suspend_never_runs_fallback_or_a_second_suspend(self):
        result = self.run_lock('suspend', ROOT_RC='0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [['sudo', ['-n', '/usr/local/libexec/sway-tty-lock', 'suspend']]])

    def test_wake_existing_confirmed_fallback_turns_outputs_on(self):
        self.ready_fallback()
        result = self.run_lock('wake')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn(['swaymsg', ['output * power on']], calls)
        self.assertFalse(any(name == 'swaylock' for name, _ in calls))
        self.assertFalse((self.runtime / 'sway-idle/fallback-displays-off').exists())

    def test_wake_output_failure_keeps_marker_for_activity_retry(self):
        self.ready_fallback()
        result = self.run_lock('wake', OUTPUT_RC='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.runtime / 'sway-idle/fallback-displays-off').exists())

    def test_failed_or_timed_out_fallback_never_suspends_or_wakes(self):
        for mode in ['suspend', 'wake']:
            for failure in [{'FALLBACK_RC': '1'}, {'FALLBACK_TIMEOUT': '1'}]:
                with self.subTest(mode=mode, failure=failure):
                    self.log.write_text('')
                    result = self.run_lock(mode, **failure)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(any(name in ['systemctl', 'swaymsg'] for name, _ in self.calls()))

    def test_unconfirmed_process_does_not_count_as_ready(self):
        Path(self.env['PID_FILE']).write_text('8123')
        result = self.run_lock('suspend', FALLBACK_RC='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(name == 'systemctl' for name, _ in self.calls()))

    def test_helper_timeout_uses_bounded_fallback_before_waking(self):
        result = self.run_lock('wake', HELPER_TIMEOUT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        ready = next(i for i, (name, _) in enumerate(calls) if name == 'swaylock')
        wake = next(i for i, (name, _) in enumerate(calls) if name == 'swaymsg')
        self.assertLess(ready, wake)
        self.assertIn(['timeout', ['--foreground', '3s', 'sudo', '-n', '/usr/local/libexec/sway-tty-lock', 'wake']], calls)
        self.assertIn(['timeout', ['--foreground', '1s', 'swaylock', '--daemonize', '--color', '09090b']], calls)

    def test_fallback_suspend_releases_startup_lock_for_before_sleep(self):
        result = self.run_lock('suspend', CHECK_UNLOCKED=str(self.runtime / 'sway-idle/fallback.lock'))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn(['systemctl', ['suspend']], calls)
        self.assertLess(next(i for i, c in enumerate(calls) if c[0] == 'swaylock'),
                        next(i for i, c in enumerate(calls) if c[0] == 'systemctl'))

    def test_root_active_suspend_releases_mutex_before_systemctl(self):
        script = self.bin / 'id'
        script.write_text(MOCK)
        script.chmod(0o755)
        root_runtime = self.root / 'root-run'
        # Only filesystem root/PATH are substituted. Control flow is production code.
        source = (ROOT / 'scripts/tty-lock-root.sh').read_text()
        source = source.replace('/run/sway-tty-lock', str(root_runtime))
        source = source.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                                f'export PATH={self.bin}:/usr/bin:/bin')
        result = subprocess.run(['/bin/bash', '-s', '--', 'suspend'], input=source,
                                env=dict(self.env, CHECK_UNLOCKED=str(root_runtime / 'start.lock')),
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['systemctl', ['suspend']], self.calls())


if __name__ == '__main__':
    unittest.main()

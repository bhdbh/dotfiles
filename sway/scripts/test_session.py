"""Session lifecycle ordering with fake managers and an isolated IPC socket."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = '''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps([Path(sys.argv[0]).name, sys.argv[1:]]) + '\\n')
'''
MOCK_SWAYMSG = MOCK + '''
runtime = Path(os.environ['XDG_RUNTIME_DIR'])
command = sys.argv[-1]
if command == 'get_tree':
    if (runtime / 'ipc-error').exists():
        raise SystemExit(1)
    views = [{'shell': 'xwayland'}] if (runtime / 'window').exists() else []
    print(json.dumps({'floating_nodes': views}))
elif command == '[all] kill':
    if not (runtime / 'refuses-close').exists():
        (runtime / 'window').unlink()
elif command == 'exit':
    Path(os.environ['SWAYSOCK']).unlink()
    print('Unable to receive IPC response', file=sys.stderr)
    raise SystemExit(1)
'''


class SessionEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ['systemctl', 'busctl', 'swaymsg', 'timeout']:
            p = self.bin / name
            p.write_text(MOCK_SWAYMSG if name == 'swaymsg' else MOCK)
            p.chmod(0o755)
        self.listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(self.listener.close)
        self.sock = self.root / 'sway.sock'
        self.listener.bind(str(self.sock))
        self.log = self.root / 'calls'
        self.env = dict(os.environ, PATH=f'{self.bin}:/usr/bin:/bin',
                        XDG_RUNTIME_DIR=str(self.root), SWAYSOCK=str(self.sock),
                        WAYLAND_DISPLAY='wayland-test', CALL_LOG=str(self.log))
        for name in ['DISPLAY', 'XDG_SESSION_ID', 'XDG_SEAT', 'XDG_VTNR']:
            self.env.pop(name, None)

    def run_entry(self):
        return subprocess.run(['/bin/bash', str(ROOT / 'scripts/start-session.sh')],
                              env=self.env, text=True, capture_output=True, timeout=3)

    def run_logout(self):
        return subprocess.run(['/bin/bash', str(ROOT / 'scripts/logout.sh')],
                              env=self.env, text=True, capture_output=True, timeout=3)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_environment_precedes_services_and_reload_does_not_restart(self):
        for _ in range(2):
            result = self.run_entry()
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        imports = [i for i, call in enumerate(calls) if call[0] == 'systemctl' and 'import-environment' in call[1]]
        starts = [i for i, call in enumerate(calls) if call == ['systemctl', ['--user', 'start', 'sway-session.service']]]
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(imports), 2)
        self.assertTrue(all(i < s for i, s in zip(imports, starts)))
        self.assertFalse(any('restart' in args for _, args in calls))
        self.assertIn(['systemctl', ['--user', 'unset-environment', 'DISPLAY', 'XDG_SESSION_ID', 'XDG_SEAT', 'XDG_VTNR']], calls)
        activation = next(args for name, args in calls if name == 'busctl')
        pairs = dict(zip(activation[8::2], activation[9::2]))
        self.assertEqual(pairs['XDG_SESSION_TYPE'], 'wayland')
        self.assertEqual(pairs['XDG_CURRENT_DESKTOP'], 'sway')
        self.assertEqual(pairs['DISPLAY'], '')

    def test_explicit_config_root_is_imported_without_shell_splitting(self):
        config = str(self.root / 'profile with spaces')
        self.env['XDG_CONFIG_HOME'] = config
        result = self.run_entry()
        self.assertEqual(result.returncode, 0, result.stderr)
        activation = next(args for name, args in self.calls() if name == 'busctl')
        pairs = dict(zip(activation[8::2], activation[9::2]))
        self.assertEqual(pairs['XDG_CONFIG_HOME'], config)

    def test_no_manager_is_contacted_without_compositor_socket(self):
        self.env['SWAYSOCK'] = str(self.root / 'missing')
        result = self.run_entry()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_no_manager_is_contacted_without_wayland_display(self):
        self.env.pop('WAYLAND_DISPLAY')
        result = self.run_entry()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_logout_closes_windows_then_stops_services_before_sway(self):
        (self.root / 'window').touch()
        result = self.run_logout()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        calls = self.calls()
        self.assertEqual([(name, args[-1]) for name, args in calls[:5]], [
            ('swaymsg', 'get_tree'), ('swaymsg', '[all] kill'), ('swaymsg', 'get_tree'),
            ('systemctl', 'graphical-session.target'), ('swaymsg', 'exit'),
        ])
        self.assertEqual(calls[3][1], ['--user', 'stop', 'graphical-session.target'])
        addresses = ['SWAYSOCK', 'WAYLAND_DISPLAY', 'DISPLAY']
        self.assertEqual(calls[5][0], 'busctl')
        self.assertEqual(dict(zip(calls[5][1][8::2], calls[5][1][9::2])), dict.fromkeys(addresses, ''))
        self.assertEqual(calls[6], ['systemctl', ['--user', 'unset-environment', *addresses]])

    def test_logout_keeps_services_running_if_ipc_is_unreachable(self):
        (self.root / 'ipc-error').touch()
        result = self.run_logout()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([name for name, _ in self.calls()], ['swaymsg'])

    def test_logout_leaves_session_running_when_a_window_refuses_to_close(self):
        (self.root / 'window').touch()
        (self.root / 'refuses-close').touch()
        clock = self.root / 'clock.sh'
        clock.write_text('sleep() { SECONDS=$((SECONDS + 30)); }\n')
        self.env['BASH_ENV'] = str(clock)
        result = self.run_logout()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Logout cancelado', result.stderr)
        self.assertTrue(self.sock.is_socket())
        self.assertEqual([(name, args[-1]) for name, args in self.calls()[:-1]], [
            ('swaymsg', 'get_tree'), ('swaymsg', '[all] kill'), ('swaymsg', 'get_tree'),
        ])
        self.assertEqual(self.calls()[-1][1][:4], ['10s', 'swaynag', '--type', 'warning'])


if __name__ == '__main__':
    unittest.main()

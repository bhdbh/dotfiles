"""Exercise the login hook with a fake launcher; never start a compositor."""

import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import unittest


HOOK = Path(__file__).resolve().parents[1] / "conf.d" / "sway-login.fish"


@unittest.skipUnless(shutil.which("fish"), "fish is required")
class SwayLoginTests(unittest.TestCase):
    def run_login(self, *, updates=None, terminal=True, login=True, interactive=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "kmscon-launch-gui"
            launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$LAUNCH_LOG"\n')
            launcher.chmod(0o755)
            log = root / "launches"
            env = {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_CACHE_HOME": str(root / "cache"),
                "PATH": str(root) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
                "TERM": "kmscon",
                "XDG_VTNR": "1",
                "XDG_SESSION_TYPE": "tty",
                "LAUNCH_LOG": str(log),
                "LOGIN_HOOK": str(HOOK),
            }
            env.update(updates or {})
            command = ["fish", "--no-config"]
            if login:
                command.append("--login")
            if interactive:
                command.append("--interactive")
            command += ["--command", 'source "$LOGIN_HOOK"; printf hook-completed']
            master, slave = pty.openpty() if terminal else (None, None)
            try:
                result = subprocess.run(
                    command, env=env, stdin=slave if terminal else subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=5,
                )
            finally:
                if terminal:
                    os.close(slave)
                    os.close(master)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "hook-completed")
            return log.read_text().splitlines() if log.exists() else []

    def test_console_login_starts_sway(self):
        self.assertEqual(self.run_login(), ["sway"])

    def test_existing_graphical_session_does_not_start_sway(self):
        for variable, value in {
            "WAYLAND_DISPLAY": "wayland-1",
            "DISPLAY": ":0",
            "SWAYSOCK": "/run/user/1000/sway.sock",
        }.items():
            with self.subTest(variable=variable):
                self.assertEqual(self.run_login(updates={variable: value}), [])

    def test_vscode_environment_resolution_does_not_start_sway(self):
        self.assertEqual(self.run_login(updates={
            "VSCODE_RESOLVING_ENVIRONMENT": "1",
            "WAYLAND_DISPLAY": "wayland-1",
            "SWAYSOCK": "/run/user/1000/sway.sock",
            "XDG_SESSION_TYPE": "wayland",
        }, terminal=False), [])

    def test_vscode_marker_alone_does_not_start_sway(self):
        self.assertEqual(self.run_login(updates={"VSCODE_RESOLVING_ENVIRONMENT": "1"}), [])

    def test_without_terminal_does_not_start_sway(self):
        self.assertEqual(self.run_login(terminal=False), [])

    def test_non_login_shell_does_not_start_sway(self):
        self.assertEqual(self.run_login(login=False), [])

    def test_non_interactive_shell_does_not_start_sway(self):
        self.assertEqual(self.run_login(interactive=False), [])

    def test_other_console_does_not_start_sway(self):
        self.assertEqual(self.run_login(updates={"XDG_VTNR": "2"}), [])

    def test_terminal_emulator_does_not_start_sway(self):
        self.assertEqual(self.run_login(updates={"TERM": "foot"}), [])


if __name__ == "__main__":
    unittest.main()

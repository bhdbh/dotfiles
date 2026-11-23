#!/usr/bin/python3
"""Run the native Polkit text agent for the current login session."""

import os
import signal
import subprocess
import sys
import termios

import gi

gi.require_version('Polkit', '1.0')
gi.require_version('PolkitAgent', '1.0')
from gi.repository import GLib, Polkit, PolkitAgent


def show_password_prompt(_listener, reading_password):
    if reading_password:
        try:
            subprocess.run(
                ['swaymsg', '[app_id="^sway-polkit-agent$"] '
                 'move scratchpad, scratchpad show, move position center'],
                stdout=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f'Polkit: {error}', file=sys.stderr)


def cancel_authentication(terminal, attributes):
    # The listener may be blocked reading the terminal. Restore echo and
    # replace the process so Polkit drops the request and the agent registers again.
    termios.tcsetattr(terminal, termios.TCSAFLUSH, attributes)
    os.execv(sys.executable, [sys.executable, *sys.argv])


def main():
    terminal = sys.stdin.fileno()
    attributes = termios.tcgetattr(terminal)
    # An EOF leaves the native listener unable to read subsequent prompts.
    attributes[6][termios.VEOF] = bytes([os.fpathconf(terminal, 'PC_VDISABLE')])
    termios.tcsetattr(terminal, termios.TCSANOW, attributes)
    GLib.unix_signal_add(
        GLib.PRIORITY_DEFAULT, signal.SIGINT, cancel_authentication, terminal, attributes,
    )
    try:
        session = Polkit.UnixSession.new_for_process_sync(os.getpid())
        listener = PolkitAgent.TextListener.new()
        listener.connect('tty-attrs-changed', show_password_prompt)
        registration = listener.register(
            PolkitAgent.RegisterFlags.RUN_IN_THREAD,
            session,
            '/org/freedesktop/PolicyKit1/AuthenticationAgent',
        )
    except GLib.Error as error:
        sys.exit(f'Polkit: {error.message}')

    print('Aguardando pedidos de autenticação do Polkit. Ctrl+C cancela o pedido.', flush=True)
    try:
        GLib.MainLoop().run()
    finally:
        PolkitAgent.Listener.unregister(registration)


if __name__ == '__main__':
    main()

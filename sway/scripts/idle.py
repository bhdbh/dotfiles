#!/usr/bin/env python3
"""Lock after 5/30 minutes and suspend after 10/60 minutes on battery/mains."""
import argparse
import fcntl
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

SHORT_TIMEOUT = 300
LONG_TIMEOUT = 1800
SUSPEND_BATTERY_TIMEOUT = 600
SUSPEND_MAINS_TIMEOUT = 3600
SUSPEND_RETRY_SECONDS = 30
IDLE_MARKERS = ('five-minutes-idle', 'ten-minutes-idle', 'one-hour-idle',
                'suspend-requested', 'suspend-retry-at')
POWER_SUPPLIES = Path('/sys/class/power_supply')


def on_battery(supplies=POWER_SUPPLIES):
    for path in supplies.glob('*/online'):
        try:
            if path.read_text().strip() == '1':
                return False
        except OSError:
            continue
    return True


def runtime_directory():
    path = Path(os.environ['XDG_RUNTIME_DIR']) / 'sway-idle'
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def invoke_lock(mode):
    subprocess.run([str(Path(__file__).resolve().with_name('lock.sh')), mode], check=True)


def invoke_suspend():
    # logind considers the graphical session inactive while the real TTY owns
    # the seat. The privileged helper secures the console before suspending.
    subprocess.run(['sudo', '-n', '/usr/local/libexec/sway-tty-lock', 'suspend'], check=True)


def clear_idle_state(runtime):
    for name in IDLE_MARKERS:
        (runtime / name).unlink(missing_ok=True)


def request_suspend(runtime, suspend=invoke_suspend):
    requested = runtime / 'suspend-requested'
    try:
        with requested.open('x'):
            pass
    except FileExistsError:
        return
    try:
        suspend()
    except Exception:
        requested.unlink(missing_ok=True)
        (runtime / 'suspend-retry-at').write_text(str(time.monotonic() + SUSPEND_RETRY_SECONDS))
        raise
    else:
        (runtime / 'suspend-retry-at').unlink(missing_ok=True)


def handle_event(event, runtime, battery=None, lock=invoke_lock, suspend=invoke_suspend):
    marker = runtime / 'five-minutes-idle'
    if event == 'short':
        marker.touch(mode=0o600)
        is_battery = on_battery() if battery is None else battery
        if is_battery:
            lock('idle')
    elif event == 'long':
        lock('idle')
    elif event == 'suspend-short':
        (runtime / 'ten-minutes-idle').touch(mode=0o600)
        is_battery = on_battery() if battery is None else battery
        if is_battery:
            request_suspend(runtime, suspend)
    elif event == 'suspend-long':
        (runtime / 'one-hour-idle').touch(mode=0o600)
        request_suspend(runtime, suspend)
    elif event == 'active':
        clear_idle_state(runtime)
        # Relevant only if starting the real TTY locker failed and fallback ran.
        fallback = runtime / 'fallback-displays-off'
        if fallback.exists():
            subprocess.run(['swaymsg', 'output * power on'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            fallback.unlink(missing_ok=True)
    elif event == 'before-sleep':
        lock('idle')
    elif event == 'after-resume':
        clear_idle_state(runtime)
        lock('wake')
        # A locked TTY doesn't send input activity to Wayland. Recreate the
        # notifications so another unattended wake gets a full new idle cycle.
        (runtime / 'restart-idle-timers').touch(mode=0o600)
    elif event == 'lock':
        lock('lock')
    else:
        raise ValueError('Unknown idle event: ' + event)


def power_changed(was_on_battery, now_on_battery, runtime, lock=invoke_lock, suspend=invoke_suspend):
    # Keep all timers running across AC changes and apply any battery deadline
    # that has already elapsed. Never unlock or suspend just because AC returns.
    if not was_on_battery and now_on_battery:
        if (runtime / 'ten-minutes-idle').exists():
            request_suspend(runtime, suspend)
        elif (runtime / 'five-minutes-idle').exists():
            lock('idle')


def retry_pending_suspend(runtime, battery, now=None, suspend=invoke_suspend):
    """Retry a failed idle request, without restarting idle time or ignoring AC."""
    retry = runtime / 'suspend-retry-at'
    if not retry.exists():
        return
    eligible = ((runtime / 'one-hour-idle').exists()
                or (battery and (runtime / 'ten-minutes-idle').exists()))
    if not eligible:
        retry.unlink(missing_ok=True)
        return
    if (time.monotonic() if now is None else now) >= float(retry.read_text()):
        request_suspend(runtime, suspend)


def command():
    helper = shlex.quote(str(Path(__file__).resolve()))

    def event(name):
        return f'/usr/bin/python3 {helper} --event {name}'

    return ['swayidle', '-w', '-C', '/dev/null',
            'timeout', str(SHORT_TIMEOUT), event('short'), 'resume', event('active'),
            'timeout', str(SUSPEND_BATTERY_TIMEOUT), event('suspend-short'),
            'timeout', str(LONG_TIMEOUT), event('long'),
            'timeout', str(SUSPEND_MAINS_TIMEOUT), event('suspend-long'),
            'before-sleep', event('before-sleep'),
            'after-resume', event('after-resume'), 'lock', event('lock')]


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def watch(runtime):
    with (runtime / 'watch.lock').open('a') as lockfile:
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        clear_idle_state(runtime)
        restart = runtime / 'restart-idle-timers'
        restart.unlink(missing_ok=True)
        stopped = False

        def stop(signum, frame):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        previous = on_battery()
        process = subprocess.Popen(command())
        try:
            while not stopped and process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if restart.exists():
                        restart.unlink(missing_ok=True)
                        stop_process(process)
                        clear_idle_state(runtime)
                        process = subprocess.Popen(command())
                        previous = on_battery()
                        continue
                    current = on_battery()
                    try:
                        power_changed(previous, current, runtime)
                        retry_pending_suspend(runtime, current)
                    except (subprocess.CalledProcessError, OSError, ValueError) as error:
                        print('Could not apply battery idle policy:', error, file=sys.stderr)
                    previous = current
            return process.poll() or 0
        finally:
            stop_process(process)
            clear_idle_state(runtime)
            restart.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--watch', action='store_true')
    mode.add_argument('--event', choices=('short', 'long', 'suspend-short', 'suspend-long',
                                        'active', 'before-sleep', 'after-resume', 'lock'))
    args = parser.parse_args()
    runtime = runtime_directory()
    if args.watch:
        return watch(runtime)
    handle_event(args.event, runtime)
    return 0


if __name__ == '__main__':
    sys.exit(main())

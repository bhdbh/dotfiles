"""Exercise the actual Waybar modules in an isolated Sway session."""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('config', type=Path)
parser.add_argument('--cycles', type=int, default=2)
parser.add_argument('--helper', type=Path)
args = parser.parse_args()
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
runtime = Path(tempfile.mkdtemp(prefix='waybar-monitor-fallback-'))
runtime.chmod(0o700)
(runtime/'sway.conf').write_text('xwayland disable\nswaybg_command -\noutput * mode 1366x768\n')
config = json.loads(args.config.read_text())
config['output'] = 'HEADLESS-1'
bar_config = runtime/'config.json'
bar_config.write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n')
env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), WLR_BACKENDS='headless',
           WLR_RENDERER='pixman', WLR_HEADLESS_OUTPUTS='2', GDK_BACKEND='wayland',
           NO_AT_BRIDGE='1')
for key in ('SWAYSOCK', 'WAYLAND_DISPLAY', 'DISPLAY'):
    env.pop(key, None)
sway_log = (runtime/'sway.log').open('w')
sway = subprocess.Popen(['sway', '-c', str(runtime/'sway.conf')], env=env,
                        stdout=sway_log, stderr=sway_log)
bar = None
helper = None
observations = []


def ipc(kind=None, command=None):
    argv = ['swaymsg', '-r']
    if kind:
        argv += ['-t', kind]
    if command:
        argv.append(command)
    result = json.loads(subprocess.check_output(argv, env=env, text=True))
    if not kind:
        assert all(item.get('success') for item in result), result
    return result


def wait_for(predicate, message, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if bar is not None and bar.poll() is not None:
            raise AssertionError(f'Waybar exited: {bar.returncode}\n' + (runtime/'waybar.log').read_text())
        if helper is not None and helper.poll() is not None:
            raise AssertionError('Workspace helper exited')
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError(message)


def bars():
    return [line for line in (runtime/'waybar.log').read_text().splitlines()
            if 'Bar configured' in line]


def session_bars():
    result = []
    pids = subprocess.run(['pgrep', '-u', str(os.getuid()), '-x', 'waybar'],
                          capture_output=True, text=True)
    for pid in pids.stdout.split():
        try:
            environment = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
            if ('SWAYSOCK=' + env['SWAYSOCK']).encode() in environment:
                result.append(int(pid))
        except OSError:
            pass
    return result


try:
    wait_for(lambda: list(runtime.glob('sway-ipc.*.sock')), 'No Sway socket')
    env['SWAYSOCK'] = str(next(runtime.glob('sway-ipc.*.sock')))
    env['WAYLAND_DISPLAY'] = next(p.name for p in runtime.glob('wayland-*')
                                  if not p.name.endswith('.lock'))
    bar_log = (runtime/'waybar.log').open('w')
    helper_args = (['python3', str(args.helper), '--preferred-output', 'HEADLESS-1',
                    '--laptop-output', 'HEADLESS-2', '--bar-config', str(bar_config)]
                   if args.helper else None)
    if helper_args:
        helper = subprocess.Popen(helper_args, env=env, stdout=bar_log, stderr=bar_log)
    else:
        bar = subprocess.Popen(['waybar', '-c', str(bar_config), '-s',
                                str(Path.home() / '.config/waybar/style.css')], env=env,
                               stdout=bar_log, stderr=bar_log)
    wait_for(lambda: bars(), 'Bar did not map')
    time.sleep(1.5)
    for cycle in range(args.cycles):
        for primary, output_command in [('HEADLESS-2', 'output HEADLESS-1 disable'),
                                         ('HEADLESS-1', 'output HEADLESS-1 enable')]:
            previous = len(bars())
            ipc(command=output_command)
            if not helper_args:
                config['output'] = primary
                bar_config.write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n')
                bar.send_signal(signal.SIGUSR2)
            wait_for(lambda: len(bars()) > previous and primary in bars()[-1],
                     'Bar did not map on ' + primary)
            # The hidden modules' cursor retry runs every second. Wait past it.
            deadline = time.monotonic() + 1.7
            while time.monotonic() < deadline:
                if bar is not None:
                    assert bar.poll() is None, f'Waybar exited after output change: {bar.returncode}'
                assert len(session_bars()) == 1, 'Expected exactly one live Waybar'
                time.sleep(.05)
            workspaces = ipc('get_workspaces')
            assert all(ws['output'] == primary or ws['name'] == '__secondary_HEADLESS-2'
                       for ws in workspaces), workspaces
            observations.append({'cycle': cycle + 1, 'output': primary, 'mapped': bars()[-1]})
            print('PASS:', cycle + 1, primary, flush=True)
    if helper_args:
        # Repeated exec_always / reload must preserve the single helper and bar.
        subprocess.run(helper_args, env=env, stdout=bar_log, stderr=bar_log,
                       check=True, timeout=5)
        previous = session_bars()
        ipc(command='reload')
        time.sleep(.5)
        assert session_bars() == previous
        print('PASS: reload and duplicate startup preserve a single bar', flush=True)
        os.kill(previous[0], signal.SIGTERM)
        wait_for(lambda: not session_bars(), 'Waybar did not terminate')
        count = len(bars())
        ipc(command='workspace 9')
        wait_for(lambda: len(bars()) > count and len(session_bars()) == 1,
                 'Missing Waybar was not recovered on reconciliation')
        print('PASS: reconciliation recovers a missing bar', flush=True)
        observations.append({'reload': 'passed', 'missing_bar_recovery': 'passed'})
except AssertionError as error:
    observations.append({'error': str(error)})
    raise
finally:
    if helper is not None and helper.poll() is None:
        helper.terminate()
        helper.wait(timeout=5)
        for pid in session_bars():
            os.kill(pid, signal.SIGINT)
    if bar is not None and bar.poll() is None:
        bar.send_signal(signal.SIGINT)
        try:
            bar.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bar.kill()
            bar.wait()
    if sway.poll() is None:
        subprocess.run(['swaymsg', '-r', 'exit'], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sway.wait(timeout=5)
    (runtime/'result.json').write_text(json.dumps(observations, indent=2))
    print('Artifacts:', runtime, flush=True)

#!/usr/bin/python3
"""Integration checks against an isolated, headless compositor with two outputs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'stage/usr/bin/sway'
BASE = ROOT / 'native-test-artifacts'
BASE.mkdir(exist_ok=True)
directory = Path(tempfile.mkdtemp(prefix='run-', dir=BASE))
directory.chmod(0o700)
config = directory / 'config'
config.write_text('''xwayland disable
output * mode 800x600@144Hz
output HEADLESS-1 pos 0 0
output HEADLESS-2 pos 800 0
output * bg #101010 solid_color
font monospace 10
default_border pixel 3
default_floating_border pixel 3
for_window [all] border pixel 3
client.focused #52525b #52525b #ffffff #52525b #52525b
client.unfocused #3f3f46 #3f3f46 #ffffff #3f3f46 #3f3f46
gaps inner 10
seat seat0 fallback true
mouse_warping none
focus_follows_mouse no
''')
env = os.environ.copy()
env.update(XDG_RUNTIME_DIR=str(directory), WLR_BACKENDS='headless',
           WLR_RENDERER='pixman', WLR_HEADLESS_OUTPUTS='2', GDK_BACKEND='wayland',
           NO_AT_BRIDGE='1')
env.pop('SWAYSOCK', None)
env.pop('WAYLAND_DISPLAY', None)
processes, logs = [], []
main_id = None


def spawn(args, name, **kwargs):
    log = (directory / f'{name}.log').open('w')
    logs.append(log)
    process = subprocess.Popen(args, env=env, stdout=kwargs.pop('stdout', log),
                               stderr=log, start_new_session=True, **kwargs)
    processes.append(process)
    return process


def query(kind):
    return json.loads(subprocess.check_output(['swaymsg', '-r', '-t', kind], env=env))


def command(value, success=True):
    reply = subprocess.run(['swaymsg', '-r', value], env=env,
                           stdout=subprocess.PIPE, check=False)
    result = json.loads(reply.stdout)
    assert all(item['success'] == success for item in result), result


def walk(node):
    yield node
    for child in node.get('nodes', []) + node.get('floating_nodes', []):
        yield from walk(child)


def fixture(identifier=None):
    identifier = main_id if identifier is None else identifier
    return next(n for n in walk(query('get_tree')) if n['id'] == identifier)


def wait_window(process):
    for _ in range(100):
        for node in walk(query('get_tree')):
            if node.get('pid') == process.pid:
                return node['id']
        time.sleep(0.02)
    raise AssertionError('Test client did not map')


def capture(name):
    path = directory / f'{name}.png'
    subprocess.run(['grim', str(path)], env=env, check=True)
    return Image.open(path).convert('RGB')


def points(node):
    outer, inner = node['rect'], node['window_rect']
    x, y = outer['x']+inner['x'], outer['y']+inner['y']
    width, height = inner['width'], inner['height']
    return [(x+1, y+1), (x+width-2, y+1), (x+1, y+height-2),
            (x+width-2, y+height-2), (x+width//2, y+height//2)]


def filled(name, color=(82, 82, 91), delay=0.15):
    if delay:
        time.sleep(delay)
    node = fixture()
    image = capture(name)
    pixels = [image.getpixel(point) for point in points(node)]
    if color is None:
        rect = node['rect']
        color = image.getpixel((rect['x'], rect['y']+rect['height']//2))
        assert color in [(82, 82, 91), (63, 63, 70)], color
    assert all(pixel == color for pixel in pixels), (name, pixels, color)
    return node, image


def pointer_command(value):
    pointer.stdin.write(value+'\n')
    pointer.stdin.flush()
    assert pointer.stdout.readline().strip() == 'OK'


try:
    compositor = spawn([str(BINARY), '-c', str(config)], 'sway')
    for _ in range(100):
        sockets = list(directory.glob('sway-ipc.*.sock'))
        if sockets:
            break
        if compositor.poll() is not None:
            raise RuntimeError((directory/'sway.log').read_text())
        time.sleep(0.05)
    else:
        raise RuntimeError('Isolated compositor did not start')
    env['SWAYSOCK'] = str(sockets[0])
    env['WAYLAND_DISPLAY'] = next(p.name for p in directory.glob('wayland-*')
                                  if not p.name.endswith('.lock'))
    window = spawn(['python3', str(ROOT/'local-tests/rounded-window.py')], 'window')
    main_id = wait_window(window)
    select_main = f'[con_id={main_id}] '
    filled('default-disabled', (16, 16, 16))
    command('client.background border')
    filled('tiled')
    print('PASS: opt-in background fills all transparent content', flush=True)

    command(select_main+'floating enable, resize set 240 180, move position 80 140')
    filled('floating')
    for index in range(12):
        x, y = 40 + (index*37) % 400, 60 + (index*23) % 290
        command(select_main+f'move position {x} {y}')
        filled(f'move-{index}', delay=0)
    command(select_main+'resize set 430 320')
    filled('resized')
    print('PASS: immediate captures after moves and resize have matching backgrounds', flush=True)

    # Feed actual pointer events; swaymsg cursor warps do not dispatch drag motion.
    command(select_main+'border normal 3, resize set 260 200, move position 80 130')
    filled('before-drag')
    pointer = spawn([str(ROOT/'build/virtual-pointer'), '1600', '600'], 'pointer',
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert pointer.stdout.readline().strip() == 'READY'
    node = fixture()
    title = node['deco_rect']
    pointer_command(f"m {node['rect']['x']+70} {node['rect']['y']-title['height']+10}")
    pointer_command('b 272 1')
    previous_position = None
    for index in range(12):
        title_x, title_y = 90 + index*24, 80 + index*13
        pointer_command(f'm {title_x+70} {title_y+10}')
        node, image = filled(f'drag-{index}', color=None, delay=0)
        position = (node['rect']['x'], node['rect']['y'])
        if previous_position is not None:
            assert position[0] > previous_position[0] and position[1] > previous_position[1], position
        previous_position = position
    pointer_command('b 272 0')
    pointer.stdin.close()
    assert pointer.wait(timeout=3) == 0
    command(select_main+'border pixel 3, focus')
    print('PASS: real virtual-pointer drag, no trailing background in captured frames', flush=True)

    underneath = spawn(['python3', str(ROOT/'local-tests/rounded-window.py')], 'underneath')
    under_id = wait_window(underneath)
    command(f'[con_id={under_id}] floating enable, resize set 650 450, move position 50 70')
    command(select_main+'resize set 240 180, move position 180 180, focus')
    time.sleep(0.15)
    command(select_main+'focus')
    filled('overlapping')
    command(f'[con_id={under_id}] focus')
    # Focus raises the other window, so test the original after moving it away.
    command(f'[con_id={under_id}] move position 510 80')
    filled('unfocused', (63, 63, 70))
    command(select_main+'focus')
    filled('focused')
    command('client.background #123456')
    filled('explicit-color', (18, 52, 86))
    command('client.background invalid-color', success=False)
    filled('invalid-preserves-color', (18, 52, 86))
    command('client.background border')
    command(f'[con_id={under_id}] kill')
    print('PASS: overlapping windows, focus colors, explicit color, invalid arguments', flush=True)

    command(select_main+'move container to output HEADLESS-2, focus')
    node, image = filled('second-monitor')
    assert node['rect']['x'] >= 800
    command(select_main+'move container to output HEADLESS-1, focus')
    filled('first-monitor')
    command(select_main+'fullscreen enable')
    filled('fullscreen')
    command(select_main+'fullscreen disable')
    filled('fullscreen-return')
    command(select_main+'move scratchpad')
    time.sleep(0.15)
    assert capture('scratchpad-hidden').getpixel((400, 300)) == (16, 16, 16)
    command('scratchpad show')
    filled('scratchpad-shown')
    command(select_main+'opacity 0')
    filled('opacity-zero', (16, 16, 16))
    command(select_main+'opacity 1')
    filled('opacity-restored')
    command('client.background none')
    filled('disabled', (16, 16, 16))
    with config.open('a') as out:
        out.write('client.background border\n')
    command('reload')
    filled('config-reload')
    command(select_main+'kill')
    time.sleep(0.15)
    assert capture('closed').getpixel((400, 300)) == (16, 16, 16)
    assert compositor.poll() is None
    print('PASS: two outputs, fullscreen, scratchpad, opacity, disable, reload, teardown', flush=True)
    print('Artifacts:', directory, flush=True)
finally:
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
    for log in logs:
        log.close()

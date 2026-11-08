"""Real Wayland input against an isolated GTK fixture and the production modules."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT/'validation'
runtime = Path(tempfile.mkdtemp(prefix='waybar-hit-areas-'))
runtime.chmod(0o700)
(runtime/'sway.conf').write_text('xwayland disable\nswaybg_command -\noutput * mode 1280x720\ndefault_border none\nfocus_follows_mouse no\n')
env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), WLR_BACKENDS='headless', WLR_RENDERER='pixman', WLR_HEADLESS_OUTPUTS='1', GDK_BACKEND='wayland', NO_AT_BRIDGE='1')
for key in ('SWAYSOCK','WAYLAND_DISPLAY','DISPLAY'): env.pop(key, None)
log = (runtime/'sway.log').open('w')
sway = subprocess.Popen(['sway', '-c', str(runtime/'sway.conf')], env=env, stdout=log, stderr=log)
fixture = None
input_client = None
state_path = HERE/'battery-state.json'
state_path.write_text(json.dumps(dict(plugged=True, full=False, charge=0, profile=0)))

def ipc(command):
    output = json.loads(subprocess.check_output(['swaymsg','-r',command], env=env, text=True))
    assert all(x.get('success') for x in output), output

def sample(command='sample'):
    fixture.stdin.write(command+'\n'); fixture.stdin.flush()
    line = fixture.stdout.readline()
    assert line, 'Fixture exited: '+(runtime/'fixture.log').read_text()
    return json.loads(line)

def move(x,y):
    input_client.stdin.write(f'm {x} {y}\n'); input_client.stdin.flush()
    assert input_client.stdout.readline().strip() == 'OK'
    time.sleep(.006)

def click(x,y):
    move(x,y)
    for state in [1,0]:
        input_client.stdin.write(f'b 272 {state}\n'); input_client.stdin.flush()
        assert input_client.stdout.readline().strip() == 'OK'
    time.sleep(.08)
    return sample()

try:
    for _ in range(100):
        sockets = list(runtime.glob('sway-ipc.*.sock'))
        displays = [p for p in runtime.glob('wayland-*') if not p.name.endswith('.lock')]
        if sockets and displays: break
        if sway.poll() is not None: raise AssertionError((runtime/'sway.log').read_text())
        time.sleep(.05)
    env.update(SWAYSOCK=str(sockets[0]), WAYLAND_DISPLAY=displays[0].name)
    input_client = subprocess.Popen(['/tmp/sway-workspace-bugs/input','1280','720'],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
    assert input_client.stdout.readline().strip() == 'READY'
    fixture = subprocess.Popen(['python3',str(HERE/'fixture.py')], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=(runtime/'fixture.log').open('w'), text=True)
    assert fixture.stdout.readline().strip() == 'READY', (runtime/'fixture.log').read_text()
    time.sleep(.5)
    s = sample()
    print('Initial geometry:', s['geometry'], flush=True)
    for key in ([] if os.environ.get('QUICK') else ['workspace-1','workspace-2','network','volume-status','battery-control']):
        x,y,w,h,_ = s['geometry'][key]
        assert h == 30, (key,h)
        before = sample()['counts'].copy()
        before_battery = json.loads(state_path.read_text())
        # Every pixel across the top/bottom border and along each vertical edge.
        points = {(px,py) for px in range(x,x+w) for py in [y,y+1,y+2,y+h-3,y+h-2,y+h-1]}
        points |= {(px,py) for px in [x+w-3,x+w-2,x+w-1] for py in range(y,y+h)}
        # Shared borders are painted by the preceding frame.
        if key in ['workspace-1','workspace-2']:
            left = 3 if key == 'workspace-1' else 0
            points |= {(px,py) for px in range(x,x+left) for py in range(y,y+h)}
        for px,py in points:
            move(px,py)
            hovered = sample()
            assert not hovered['pointer'], ('Pointer on border',key,px,py,hovered)
        for px,py in [(x+w//2,y+1),(x+w//2,y+h-2),(x+w-2,y+h//2)]:
            click(px,py)
        assert sample()['counts'] == before, ('Border action',key,sample())
        assert json.loads(state_path.read_text()) == before_battery, ('Battery border action',key)
        print('PASS border and cursor:',key,len(points),'pixels',flush=True)
    # Click the exact first/last interior pixels, including padding.
    for key,count_key in [('workspace-1','workspace'),('network','network'),('volume-status','volume')]:
        x,y,w,h,_ = s['geometry'][key]
        old = sample()['counts'][count_key]
        for px,py in [(x+4,y+3),(x+w-4,y+h-4)]:
            move(px,py)
            assert sample()['pointer'], ('Missing pointer inside',key,px,py,sample())
            click(px,py)
        assert sample()['counts'][count_key] == old+2, (key,sample())
        print('PASS interior including padding:',key,flush=True)
    for key,state_key in [('battery-profile','profile'),('battery-charge-toggle','charge')]:
        x,y,w,h,_ = s['geometry'][key]
        old = json.loads(state_path.read_text())
        for px,py in [(x,y),(x+w-1,y+h-1)]:
            move(px,py)
            assert sample()['pointer'], ('Missing battery pointer',key,px,py)
            click(px,py)
        updated = json.loads(state_path.read_text())
        assert updated[state_key] == old[state_key]+2, (key,old,updated)
        other = 'charge' if state_key == 'profile' else 'profile'
        assert updated[other] == old[other], (key,'Action leaked',old,updated)
        print('PASS independent battery action:',key,flush=True)
    x,y,w,h,_ = s['geometry']['battery-charge-divider']
    old = json.loads(state_path.read_text())
    for px in range(x,x+w):
        for py in range(y,y+h):
            move(px,py)
            hovered = sample()
            assert not hovered['pointer'], ('Divider pointer',px,py,hovered)
    click(x+w//2,y+h//2)
    assert json.loads(state_path.read_text()) == old
    subprocess.run(['grim','-g','0,0 1280x40',str(HERE/'limited.png')],env=env,check=True)
    x,y,w,h,_ = s['geometry']['battery-charge-toggle']
    click(x+w//2,y+h//2)
    subprocess.run(['grim','-g','0,0 1280x40',str(HERE/'full.png')],env=env,check=True)
    state = json.loads(state_path.read_text()); state['plugged'] = False
    state_path.write_text(json.dumps(state))
    sample('refresh'); time.sleep(.2)
    unplugged = sample()
    subprocess.run(['grim','-g','0,0 1280x40',str(HERE/'unplugged.png')],env=env,check=True)
    assert not unplugged['geometry']['battery-charge-toggle'][4]
    assert not unplugged['geometry']['battery-charge-divider'][4]
    optional_width = s['geometry']['battery-charge-toggle'][2] + s['geometry']['battery-charge-divider'][2]
    assert unplugged['geometry']['battery-control'][2] == s['geometry']['battery-control'][2]-optional_width
    # Simulate parent show_all during a reload; optional pieces must stay hidden.
    print('PASS plug/unplug visibility, no space retained, grey/green circles',flush=True)
    (HERE/'integration-result.json').write_text(json.dumps(dict(initial=s, unplugged=unplugged, runtime=str(runtime)),indent=2))
finally:
    if input_client is not None and input_client.poll() is None:
        input_client.stdin.close(); input_client.wait(timeout=5)
    if fixture is not None and fixture.poll() is None:
        fixture.stdin.write('quit\n'); fixture.stdin.flush()
        fixture.wait(timeout=5)
        assert fixture.returncode == 0, (runtime/'fixture.log').read_text()
    if sway.poll() is None:
        subprocess.run(['swaymsg','-r','exit'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        sway.wait(timeout=5)
    print('Artifacts:',runtime,flush=True)

"""Exercise production volume widgets and hit areas on an isolated compositor."""
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'validation/volume'


def fixture(runtime):
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gtk, Gdk, GLib
    css = Gtk.CssProvider()
    css.load_from_path(str(ROOT / 'style.css'))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 800)
    window = Gtk.Window(title='Volume controls fixture')
    window.set_name('waybar')
    row = Gtk.Box()
    row.set_valign(Gtk.Align.START)
    window.add(row)
    get_type = C.CFUNCTYPE(C.c_void_p, C.c_void_p)
    queue_type = C.CFUNCTYPE(None, C.c_void_p)

    class Info(C.Structure):
        _fields_ = [('obj', C.c_void_p), ('version', C.c_char_p),
                    ('get', get_type), ('queue', queue_type)]

    pointer = C.pythonapi.PyCapsule_GetPointer
    pointer.argtypes = [C.py_object, C.c_char_p]
    pointer.restype = C.c_void_p
    modules = []
    for path in (runtime / 'volume.so', ROOT / 'scripts/optical-spacing.so', ROOT / 'scripts/hit-areas.so'):
        root = Gtk.EventBox()
        row.pack_start(root, False, False, 0)
        get = get_type(lambda obj, root=root: pointer(root.__gpointer__, None))
        queue = queue_type(lambda obj: None)
        info = Info(None, b'1', get, queue)
        lib = C.CDLL(str(path))
        lib.wbcffi_init.argtypes = [C.POINTER(Info), C.c_void_p, C.c_size_t]
        lib.wbcffi_init.restype = C.c_void_p
        lib.wbcffi_refresh.argtypes = [C.c_void_p, C.c_int]
        lib.wbcffi_deinit.argtypes = [C.c_void_p]
        instance = lib.wbcffi_init(C.byref(info), None, 0)
        modules.append((lib, instance, get, queue))
    window.show_all()

    def command(channel, condition):
        line = channel.readline().strip()
        if line == 'quit':
            for lib, instance, *_ in reversed(modules):
                lib.wbcffi_deinit(instance)
            window.destroy()
            Gtk.main_quit()
            return False
        if line == 'refresh':
            for lib, instance, *_ in modules:
                lib.wbcffi_refresh(instance, 0)
        if line == 'show-all':
            window.show_all()
        named = {}

        def collect(widget):
            named[widget.get_name()] = widget
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    collect(child)

        collect(window)
        if line == 'scroll':
            event = Gdk.Event.new(Gdk.EventType.SCROLL)
            event.direction = Gdk.ScrollDirection.UP
            named['volume-toggle'].emit('scroll-event', event)
        geometry = {}
        for key in ('volume-status', 'volume-toggle', 'audio-output-toggle', 'audio-output-divider'):
            widget = named[key]
            a = widget.get_allocation()
            x, y = widget.translate_coordinates(window, 0, 0)
            geometry[key] = [x, y, a.width, a.height, widget.get_visible()]
        device = Gdk.Display.get_default().get_default_seat().get_pointer()
        hovered, _, _ = device.get_window_at_position()
        cursor = hovered.get_cursor() if hovered else None
        while hovered and cursor is None:
            hovered = hovered.get_parent()
            cursor = hovered.get_cursor() if hovered else None
        hand = Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.HAND2)
        print(json.dumps(dict(geometry=geometry, pointer=cursor == hand,
                             muted=named['volume-toggle'].get_style_context().has_class('muted'),
                             value=named['volume-value'].get_text(),
                             output=named['custom-audio-output'].get_text(),
                             mute_calls=C.c_int.in_dll(modules[0][0], 'test_mute_calls').value)), flush=True)
        return True

    GLib.io_add_watch(sys.stdin, GLib.IO_IN, command)
    print('READY', flush=True)
    Gtk.main()


def integration():
    ARTIFACTS.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='waybar-volume-') as directory:
        runtime = Path(directory)
        state = runtime / 'outputs.json'
        state.write_text(json.dumps(dict(count=2, selected=1, calls=0)))
        helper = runtime / 'helper.py'
        helper.write_text('''import json, sys
from pathlib import Path
p = Path(__file__).with_name('outputs.json')
s = json.loads(p.read_text())
if '--next' in sys.argv and s['count'] > 1:
    s['selected'] = s['selected'] % s['count'] + 1
    s['calls'] += 1
    p.write_text(json.dumps(s))
print(f"Volume: 50%\\n{s['selected']}\\nactive\\n{s['count']}")
''')
        source = (ROOT / 'scripts/volume-control.c').read_text().replace(
            'g_build_filename(g_get_user_config_dir(), "waybar", "scripts", "audio-output.py", NULL)',
            f'g_build_filename({json.dumps(str(helper))}, NULL)')
        (runtime / 'volume.c').write_text(source)
        # Substitute only the audio server; all GTK layout and event code is production code.
        (runtime / 'audio.c').write_text('''#include "audio-client.h"
struct AudioClient { AudioState state; AudioChanged callback; void *data; };
static AudioClient mock = {{50, 0, 1}, 0, 0};
int test_mute_calls;
AudioClient *audio_client_new(const char *name, AudioChanged cb, void *data) {
    (void)name; mock.callback = cb; mock.data = data; return &mock;
}
void audio_client_retry(AudioClient *c) { c->callback(c->data, c->state); }
AudioState audio_client_state(AudioClient *c) { return c->state; }
int audio_client_toggle_mute(AudioClient *c) { c->state.muted = !c->state.muted; test_mute_calls++; return 1; }
int audio_client_step(AudioClient *c, double step) { c->state.percent += step; return 1; }
void audio_client_free(AudioClient *c) { (void)c; }
''')
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-Wl,-z,defs', '-I' + str(ROOT / 'scripts'), str(runtime / 'volume.c'),
                        str(runtime / 'audio.c'), '-o', str(runtime / 'volume.so'),
                        '-l:libgtk-3.so.0', '-l:libgdk-3.so.0', '-l:libgio-2.0.so.0',
                        '-l:libglib-2.0.so.0', '-l:libgobject-2.0.so.0'], check=True)
        (runtime / 'sway.conf').write_text('xwayland disable\nswaybg_command -\noutput * mode 1280x720\ndefault_border none\nfocus_follows_mouse no\n')
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), WLR_BACKENDS='headless',
                   WLR_RENDERER='pixman', WLR_HEADLESS_OUTPUTS='1', GDK_BACKEND='wayland', NO_AT_BRIDGE='1')
        for key in ('SWAYSOCK', 'WAYLAND_DISPLAY', 'DISPLAY', 'WAYLAND_DEBUG'):
            env.pop(key, None)
        log = (ARTIFACTS / 'sway.log').open('w')
        sway = subprocess.Popen(['sway', '-c', str(runtime / 'sway.conf')], env=env, stdout=log, stderr=log)
        app = mouse = None
        try:
            for _ in range(100):
                sockets = list(runtime.glob('sway-ipc.*.sock'))
                displays = [p for p in runtime.glob('wayland-*') if not p.name.endswith('.lock')]
                if sockets and displays:
                    break
                assert sway.poll() is None, (ARTIFACTS / 'sway.log').read_text()
                time.sleep(.05)
            env.update(SWAYSOCK=str(sockets[0]), WAYLAND_DISPLAY=displays[0].name)
            mouse = subprocess.Popen([str(ROOT.parent / 'anyrun-backdrop-continuity/virtual-pointer'), '1280', '720'],
                                     env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
            assert mouse.stdout.readline().strip() == 'READY'
            app = subprocess.Popen([sys.executable, __file__, '--fixture', directory], env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=(ARTIFACTS / 'fixture.log').open('w'), text=True)
            assert app.stdout.readline().strip() == 'READY', (ARTIFACTS / 'fixture.log').read_text()

            def sample(command='sample'):
                app.stdin.write(command + '\n'); app.stdin.flush()
                return json.loads(app.stdout.readline())

            def move(x, y):
                mouse.stdin.write(f'm {x} {y}\n'); mouse.stdin.flush()
                assert mouse.stdout.readline().strip() == 'OK'
                time.sleep(.015)

            def click(x, y):
                move(x, y)
                for pressed in (1, 0):
                    mouse.stdin.write(f'b 272 {pressed}\n'); mouse.stdin.flush()
                    assert mouse.stdout.readline().strip() == 'OK'
                time.sleep(.12)
                return sample()

            time.sleep(.6)
            initial = sample()
            assert initial['geometry']['volume-status'][3] == 30, initial
            assert initial['output'] == '1', initial
            for key in ('volume-toggle', 'audio-output-toggle'):
                x, y, w, h, visible = initial['geometry'][key]
                assert visible
                before = json.loads(state.read_text())
                mute_before = sample()['mute_calls']
                for last_corner in (False, True):
                    # Muting changes 50% to 0%, so resolve the current hit area.
                    x, y, w, h, _ = sample()['geometry'][key]
                    px, py = (x + w - 1, y + h - 1) if last_corner else (x, y)
                    move(px, py)
                    assert sample()['pointer'], (key, px, py, sample())
                    click(px, py)
                after = json.loads(state.read_text())
                if key == 'volume-toggle':
                    assert sample()['mute_calls'] == mute_before + 2
                    assert after == before
                else:
                    assert sample()['mute_calls'] == mute_before
                    assert after['calls'] == before['calls'] + 2
                print('PASS independent action and full padding:', key, flush=True)
            x, y, w, h, _ = initial['geometry']['volume-toggle']
            assert click(x + w // 2, y + h // 2)['muted']
            assert not click(x + w // 2, y + h // 2)['muted']
            assert sample('scroll')['value'] == '52%'
            before = sample()['mute_calls'], json.loads(state.read_text())['calls']
            for key in ('volume-status', 'audio-output-divider'):
                x, y, w, h, _ = initial['geometry'][key]
                points = ([(x + w // 2, y + 1), (x + w // 2, y + h - 2), (x + w - 2, y + h // 2)]
                          if key == 'volume-status' else [(x + w // 2, y + h // 2)])
                for px, py in points:
                    move(px, py)
                    assert not sample()['pointer'], (key, sample())
                    click(px, py)
            assert before == (sample()['mute_calls'], json.loads(state.read_text())['calls'])
            subprocess.run(['grim', '-g', '0,0 1280x40', str(ARTIFACTS / 'two-outputs.png')], env=env, check=True)
            optional_width = sum(initial['geometry'][key][2] for key in ('audio-output-toggle', 'audio-output-divider'))
            for count in (1, 0, 3):
                s = json.loads(state.read_text()); s['count'] = count; state.write_text(json.dumps(s))
                sample('refresh'); time.sleep(.25)
                result = sample('show-all')
                assert result['geometry']['audio-output-toggle'][4] == (count > 1), result
                assert result['geometry']['audio-output-divider'][4] == (count > 1), result
                expected = initial['geometry']['volume-status'][2] - (optional_width if count < 2 else 0)
                assert result['geometry']['volume-status'][2] == expected, result
                if count == 1:
                    subprocess.run(['grim', '-g', '0,0 1280x40', str(ARTIFACTS / 'one-output.png')], env=env, check=True)
            print('PASS mute feedback, scroll, passive borders/divider, hidden selector without leftover space, reconnect', flush=True)
            (ARTIFACTS / 'result.json').write_text(json.dumps(dict(initial=initial, reconnected=result), indent=2) + '\n')
        finally:
            if app is not None and app.poll() is None:
                app.stdin.write('quit\n'); app.stdin.flush(); app.wait(timeout=5)
            if mouse is not None and mouse.poll() is None:
                mouse.stdin.close(); mouse.wait(timeout=5)
            if sway.poll() is None:
                sway.terminate(); sway.wait(timeout=5)
        assert app.returncode == 0, (ARTIFACTS / 'fixture.log').read_text()


if __name__ == '__main__':
    if '--fixture' in sys.argv:
        fixture(Path(sys.argv[-1]))
    else:
        integration()

import ctypes as C
import json
import hashlib
import os
from pathlib import Path
import sys
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib

ROOT = Path(__file__).resolve().parents[1]
css = Gtk.CssProvider()
css.load_from_path(str(ROOT/'style.css'))
Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 800)
window = Gtk.Window(title='Waybar hit areas fixture')
window.set_name('waybar')
window.set_default_size(1100, 100)
row = Gtk.Box()
row.set_valign(Gtk.Align.START)
window.add(row)
named = {}
counts = dict(workspace=0, network=0, volume=0, scroll=0)

def name(widget, text):
    widget.set_name(text)
    named[text] = widget
    return widget

def action(widget, event, key):
    if event.button == 1: counts[key] += 1
    return True

def event_box(child, key=None):
    box = Gtk.EventBox()
    box.add(child)
    if key:
        box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        box.connect('button-press-event', action, key)
    return box

workspaces = name(Gtk.Box(), 'workspaces')
row.pack_start(workspaces, False, False, 0)
for number in range(1, 3):
    button = name(Gtk.Button(label=str(number)), f'workspace-{number}')
    button.connect('clicked', lambda *_: counts.__setitem__('workspace', counts['workspace']+1))
    workspaces.pack_start(button, False, False, 0)

for frame_name, text, key in [('network', 'Wi-Fi: 80%', 'network'), ('volume-status', 'Volume: 50%', 'volume')]:
    frame = name(Gtk.Box(), frame_name)
    label = Gtk.Label(label=text)
    if key == 'volume': label.set_name('pulseaudio')
    frame.pack_start(label, False, False, 0)
    target = event_box(frame, key)
    if key == 'volume':
        target.add_events(Gdk.EventMask.SCROLL_MASK)
        target.connect('scroll-event', lambda *_: counts.__setitem__('scroll', counts['scroll']+1) or True)
        target = event_box(target)
    row.pack_start(target, False, False, 0)

GET = C.CFUNCTYPE(C.c_void_p, C.c_void_p)
QUEUE = C.CFUNCTYPE(None, C.c_void_p)
class Info(C.Structure):
    _fields_ = [('obj', C.c_void_p), ('version', C.c_char_p), ('get', GET), ('queue', QUEUE)]
ptr = C.pythonapi.PyCapsule_GetPointer
ptr.argtypes = [C.py_object, C.c_char_p]
ptr.restype = C.c_void_p
modules = []
def module(path):
    root = Gtk.EventBox()
    row.pack_start(root, False, False, 0)
    get = GET(lambda obj: ptr(root.__gpointer__, None))
    queue = QUEUE(lambda obj: None)
    info = Info(None, b'1', get, queue)
    lib = C.CDLL(str(path))
    lib.wbcffi_init.argtypes = [C.POINTER(Info), C.c_void_p, C.c_size_t]
    lib.wbcffi_init.restype = C.c_void_p
    lib.wbcffi_refresh.argtypes = [C.c_void_p, C.c_int]
    lib.wbcffi_deinit.argtypes = [C.c_void_p]
    instance = lib.wbcffi_init(C.byref(info), None, 0)
    modules.append((lib, instance, get, queue))

module(ROOT/('scripts/battery-control.so' if os.environ.get('REAL_BATTERY') else 'validation/battery-fixture.so'))
module(ROOT/'scripts/optical-spacing.so')
module(ROOT/'scripts/hit-areas.so')
window.connect('destroy', Gtk.main_quit)
window.show_all()

def collect(widget):
    if widget.get_name() in ('battery-control', 'battery-profile', 'battery-charge-toggle', 'battery-full', 'battery-charge-divider', 'battery-value'):
        named[widget.get_name()] = widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children(): collect(child)

def command(channel, condition):
    line = channel.readline().strip()
    if line == 'quit':
        for lib, instance, *_ in reversed(modules): lib.wbcffi_deinit(instance)
        window.destroy()
        return False
    if line == 'refresh':
        for lib, instance, *_ in modules: lib.wbcffi_refresh(instance, 0)
    collect(window)
    geometry = {}
    for key, widget in named.items():
        a = widget.get_allocation()
        position = widget.translate_coordinates(window, 0, 0)
        if position is None:
            x = y = -1
        else:
            x, y = position
        geometry[key] = [x, y, a.width, a.height, widget.get_visible()]
    pointer = Gdk.Display.get_default().get_default_seat().get_pointer()
    hovered, x, y = pointer.get_window_at_position()
    hover_info = []
    def windows(widget):
        if widget.get_realized():
            candidate = widget.get_event_window() if isinstance(widget,Gtk.Button) else widget.get_window()
            if candidate == hovered: hover_info.append(widget.get_name())
        if isinstance(widget,Gtk.Container):
            for child in widget.get_children(): windows(child)
    windows(window)
    cursor = hovered.get_cursor() if hovered else None
    while hovered and cursor is None:
        hovered = hovered.get_parent()
        cursor = hovered.get_cursor() if hovered else None
    hand = Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.HAND2)
    def signature(c):
        if c is None: return None
        pixbuf = c.get_image()
        return hashlib.sha256(pixbuf.get_pixels()).hexdigest() if pixbuf else str(c.get_cursor_type())
    output = dict(counts=counts, geometry=geometry, pointer=cursor == hand, cursor=str(cursor), hand=str(hand), hovered=str(hovered), hover_info=hover_info)
    if 'battery-value' in named:
        value=named['battery-value']
        output['battery']=dict(markup=value.get_label(),syncing=value.get_style_context().has_class('syncing'),full=named['battery-full'].get_style_context().has_class('enabled'))
    if line.startswith('image '):
        Gdk.pixbuf_get_from_window(window.get_window(), 0, 0, window.get_allocated_width(), 40).savev(line[6:], 'png', [], [])
    print(json.dumps(output), flush=True)
    return True

GLib.io_add_watch(sys.stdin, GLib.IO_IN, command)
print('READY', flush=True)
Gtk.main()

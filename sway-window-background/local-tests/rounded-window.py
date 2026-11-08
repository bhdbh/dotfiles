import math
import cairo
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk

GLib.set_prgname('sway-background-fixture')
window = Gtk.Window(title='Sway background verification')
window.set_decorated(False)
window.set_app_paintable(True)
window.set_visual(window.get_screen().get_rgba_visual())


def draw(widget, cr):
    width, height = widget.get_allocated_width(), widget.get_allocated_height()
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_source_rgba(0.9, 0.2, 0.3, 1)
    radius = 28
    for x, y, angle in [(width-radius, radius, -90),
                         (width-radius, height-radius, 0),
                         (radius, height-radius, 90), (radius, radius, 180)]:
        cr.arc(x, y, radius, math.radians(angle), math.radians(angle+90))
    cr.close_path()
    cr.fill()
    # A transparent hole verifies the entire rectangle is filled, not corners.
    cr.set_source_rgba(0, 0, 0, 0)
    cr.rectangle(width//2-10, height//2-10, 20, 20)
    cr.fill()
    return True


window.connect('draw', draw)
window.connect('destroy', Gtk.main_quit)
window.show_all()
Gtk.main()

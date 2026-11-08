"""Battery rendering, power profiles and the Acer 80%/100% charge limit."""

from pathlib import Path
import subprocess
import sys
from gi.repository import Gio, GLib

HEALTH_MODE = Path('/sys/bus/wmi/drivers/acer-wmi-battery/health_mode')
POWER_SUPPLY = Path('/sys/class/power_supply')
CHARGE_HELPER = '/usr/local/libexec/waybar-acer-charge'
COLORS = {'normal': '#f4f4f5', 'saving': '#f97316', 'charging': '#22c55e', 'low': '#ef4444'}
PROFILE_SERVICE = 'org.freedesktop.UPower.PowerProfiles'
PROFILE_PATH = '/org/freedesktop/UPower/PowerProfiles'


def read(path, default=''):
    try:
        return path.read_text().strip()
    except OSError:
        return default


def profile_call(method, signature, arguments):
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    return bus.call_sync(
        PROFILE_SERVICE, PROFILE_PATH, 'org.freedesktop.DBus.Properties', method,
        GLib.Variant(signature, arguments), None, Gio.DBusCallFlags.NONE, 3000, None,
    )


def profile():
    try:
        return profile_call('Get', '(ss)', (PROFILE_SERVICE, 'ActiveProfile')).unpack()[0]
    except GLib.Error:
        return None


def format_battery(capacity, status, power_profile, limit=100, *, cells=False):
    base = COLORS['saving'] if power_profile == 'power-saver' else COLORS['normal']
    full = status == 'Full' or capacity >= limit
    percentage = base
    if not full and status == 'Charging':
        percentage = COLORS['charging']
    elif not full and capacity <= 20:
        percentage = COLORS['low']
    label = f'<span foreground="{base}">Battery:</span>'
    value = f'<span foreground="{percentage}">{capacity}%</span>'
    return label + ('\t' if cells else ' ') + value


def plugged_in():
    return any(read(p / 'type') != 'Battery' and read(p / 'online') == '1'
               for p in POWER_SUPPLY.iterdir())


def full_charge_enabled():
    return read(HEALTH_MODE) == '0'


def battery_markup(power_profile=None, *, cells=False, widget=False):
    battery = next((p for p in POWER_SUPPLY.iterdir() if read(p / 'type') == 'Battery'), None)
    if battery is None:
        if widget:
            return 'Battery:\t--%\t0\t0\tUnknown\t-1'
        return 'Battery:\t--%' if cells else 'Battery: --%'
    capacity = int(read(battery / 'capacity', '0'))
    limit = 80 if read(HEALTH_MODE) == '1' else int(read(battery / 'charge_control_end_threshold', '100'))
    status = read(battery / 'status', 'Unknown')
    markup = format_battery(capacity, status, power_profile if power_profile is not None else profile(), limit, cells=cells or widget)
    if widget:
        markup += f'\t{int(plugged_in())}\t{int(full_charge_enabled())}\t{status}\t{capacity}'
    return markup


def notify(message):
    subprocess.run(['notify-send', 'Waybar — bateria', message], check=False)


def toggle_profile():
    current = profile()
    if current is None:
        raise RuntimeError('O serviço de planos de energia ainda precisa ser instalado. Execute ~/.config/waybar/power/setup-system.sh em um terminal.')
    target = 'balanced' if current == 'power-saver' else 'power-saver'
    profile_call('Set', '(ssv)', (PROFILE_SERVICE, 'ActiveProfile', GLib.Variant('s', target)))
    return target


def toggle_charge():
    # An unplug that races a click must not change the persisted charge mode.
    if not plugged_in():
        return
    current = read(HEALTH_MODE)
    if current not in ('0', '1'):
        raise RuntimeError('O controle de limite de carga Acer não está disponível.')
    result = subprocess.run(['pkexec', '--disable-internal-agent', CHARGE_HELPER],
                            check=True, capture_output=True, text=True, timeout=10)
    # The persistence service can restore the mode between our read and click.
    target = {'80': '1', '100': '0'}.get(result.stdout.strip())
    if target is None or read(HEALTH_MODE) != target:
        raise RuntimeError('O firmware não confirmou a alteração do limite de carga.')


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    cells = '--cells' in sys.argv[2:]
    widget = '--widget' in sys.argv[2:]
    try:
        power_profile = None
        if action == 'toggle-profile':
            power_profile = toggle_profile()
        elif action == 'toggle-charge':
            toggle_charge()
        elif action != 'status':
            raise ValueError('Unknown battery action')
        print(battery_markup(power_profile, cells=cells, widget=widget))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, GLib.Error) as error:
        if action == 'status':
            print('Battery:\t--%\t0\t0\tUnknown\t-1' if widget else 'Battery:\t--%' if cells else 'Battery: --%')
        else:
            notify(str(error))
            raise SystemExit(1)


if __name__ == '__main__':
    main()

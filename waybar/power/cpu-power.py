#!/usr/bin/python3
"""Give power-profiles-daemon's placeholder real ACPI CPU power savings."""

import json
import logging
from pathlib import Path
import re
import signal

from gi.repository import Gio, GLib

SERVICE = 'org.freedesktop.UPower.PowerProfiles'
OBJECT = '/org/freedesktop/UPower/PowerProfiles'
CPUFREQ = Path('/sys/devices/system/cpu/cpufreq')
STATE = Path('/run/waybar-cpu-power/original.json')
LOG = logging.getLogger('waybar-cpu-power')


def read(path):
    return path.read_text().strip()


def write(path, value):
    if read(path) != value:
        path.write_text(value + '\n')
    if read(path) != value:
        raise RuntimeError(f'{path} did not accept {value}')


class CpuPower:
    def __init__(self, root=CPUFREQ, state=STATE):
        self.root = root
        self.state = state

    def policies(self):
        return sorted(p for p in self.root.glob('policy[0-9]*')
                      if re.fullmatch(r'policy\d+', p.name))

    def supported(self):
        policies = self.policies()
        return (bool(policies) and (self.root / 'boost').exists()
                and all(read(p / 'scaling_driver') == 'acpi-cpufreq'
                        and 'conservative' in read(p / 'scaling_available_governors').split()
                        for p in policies))

    def save_original(self):
        original = json.loads(self.state.read_text()) if self.state.exists() else {
            'boost': read(self.root / 'boost'), 'governors': {},
        }
        for p in self.policies():
            original['governors'].setdefault(p.name, read(p / 'scaling_governor'))
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.state.with_suffix('.tmp')
        temporary.write_text(json.dumps(original) + '\n')
        temporary.replace(self.state)

    def restore(self):
        if not self.state.exists():
            return
        original = json.loads(self.state.read_text())
        # The snapshot is root-owned. Still restrict writes to CPU policy files.
        for name, governor in original['governors'].items():
            if not re.fullmatch(r'policy\d+', name):
                raise ValueError('Invalid CPU policy in snapshot')
            p = self.root / name
            if not p.exists():
                continue
            if governor not in read(p / 'scaling_available_governors').split():
                raise ValueError(f'Unavailable original governor: {governor}')
            write(p / 'scaling_governor', governor)
        if original['boost'] not in ('0', '1'):
            raise ValueError('Invalid boost setting in snapshot')
        write(self.root / 'boost', original['boost'])
        self.state.unlink()
        LOG.info('Restored original CPU governors and boost')

    def apply(self, profile, profiles):
        # Hand control back if a future kernel/firmware gains native support.
        placeholder = bool(profiles) and all(
            p.get('Driver') == 'placeholder'
            and not p.get('CpuDriver') for p in profiles)
        if profile != 'power-saver' or not placeholder or not self.supported():
            self.restore()
            return
        self.save_original()
        try:
            write(self.root / 'boost', '0')
            for p in self.policies():
                write(p / 'scaling_governor', 'conservative')
        except Exception:
            self.restore()
            raise
        LOG.info('power-saver: boost disabled, conservative governor on %d policies',
                 len(self.policies()))


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    controller = CpuPower()
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    proxy = Gio.DBusProxy.new_sync(
        bus, Gio.DBusProxyFlags.NONE, None, SERVICE, OBJECT, SERVICE, None)
    failed = False

    def update(*_args):
        nonlocal failed
        # Losing the owner must release the fallback until the service returns.
        profile = proxy.get_cached_property('ActiveProfile') if proxy.get_name_owner() else None
        profiles = proxy.get_cached_property('Profiles') if profile is not None else None
        try:
            controller.apply(profile.unpack() if profile is not None else None,
                             profiles.unpack() if profiles is not None else [])
        except Exception:
            LOG.exception('Could not apply CPU power policy')
            failed = True
            loop.quit()

    def resumed(_bus, _sender, _path, _interface, _signal, parameters, _data):
        if not parameters.unpack()[0]:
            update()

    def stop():
        loop.quit()
        return GLib.SOURCE_REMOVE

    proxy.connect('g-properties-changed', update)
    proxy.connect('notify::g-name-owner', update)
    bus.signal_subscribe('org.freedesktop.login1', 'org.freedesktop.login1.Manager',
                         'PrepareForSleep', '/org/freedesktop/login1', None,
                         Gio.DBusSignalFlags.NONE, resumed, None)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    try:
        update()
        if not failed:
            loop.run()
    finally:
        controller.restore()
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

#!/usr/bin/bash
set -euo pipefail

setup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if (( EUID != 0 )); then
    exec sudo -- /usr/bin/bash "$0" "$@"
fi

/usr/bin/python3 -I -c 'from gi.repository import Gio, GLib'
test -r /sys/devices/system/cpu/cpufreq/boost
test "$(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_driver)" = acpi-cpufreq

install -d -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$setup_dir/cpu-power.py" /usr/local/libexec/waybar-cpu-power
install -o root -g root -m 0644 "$setup_dir/waybar-cpu-power.service" /etc/systemd/system/waybar-cpu-power.service
if command -v restorecon >/dev/null; then
    restorecon /usr/local/libexec/waybar-cpu-power /etc/systemd/system/waybar-cpu-power.service
fi
systemctl daemon-reload
systemctl enable power-profiles-daemon.service
systemctl enable waybar-cpu-power.service
systemctl restart waybar-cpu-power.service
systemctl is-active --quiet waybar-cpu-power.service
/usr/bin/python3 "$setup_dir/verify-controls.py"
printf '%s\n' 'Pronto: o modo economia da Waybar agora desliga o turbo e usa o governor conservative.'
printf '%s\n' 'Ao voltar ao equilibrado, turbo e governor anteriores são restaurados.'

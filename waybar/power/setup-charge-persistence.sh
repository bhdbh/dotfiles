#!/usr/bin/bash
set -euo pipefail

setup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if (( EUID != 0 )); then
    exec sudo -- /usr/bin/bash "$0" "$@"
fi
test "$#" -eq 0
/usr/bin/python3 -I -c 'from gi.repository import Gio, GLib'
test -r /sys/bus/wmi/drivers/acer-wmi-battery/health_mode

backup_dir=$(mktemp -d /var/backups/waybar-acer-charge.XXXXXXXX)
for target in /usr/local/libexec/waybar-acer-charge \
              /etc/systemd/system/waybar-acer-charge.service \
              /etc/modprobe.d/waybar-acer-charge.conf; do
    if [[ -e "$target" ]]; then
        cp -a --parents "$target" "$backup_dir/"
    fi
done

install -d -m 0755 /usr/local/libexec
# Replace the executable atomically: clicks may run during installation.
temporary=$(mktemp /usr/local/libexec/.waybar-acer-charge.XXXXXXXX)
trap 'rm -f -- "$temporary"' EXIT
install -o root -g root -m 0755 "$setup_dir/acer-charge-helper" "$temporary"
mv -fT "$temporary" /usr/local/libexec/waybar-acer-charge
install -o root -g root -m 0644 "$setup_dir/waybar-acer-charge.service" /etc/systemd/system/waybar-acer-charge.service
if command -v restorecon >/dev/null; then
    restorecon /usr/local/libexec/waybar-acer-charge /etc/systemd/system/waybar-acer-charge.service
fi
# Migrate the existing modprobe setting; never infer a new preference on resume.
/usr/bin/python3 -I /usr/local/libexec/waybar-acer-charge --restore
systemctl daemon-reload
systemctl enable waybar-acer-charge.service
systemctl restart waybar-acer-charge.service
systemctl is-active --quiet waybar-acer-charge.service
printf '%s\n' 'Pronto: o limite escolhido será restaurado no boot e após a suspensão.'
printf 'Backup: %s\n' "$backup_dir"

#!/usr/bin/env bash
# Install the system dependencies for Quickshell's battery controls on Fedora.
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if (( EUID != 0 )); then
    exec sudo -- bash "$repo/scripts/setup-quickshell-power.sh" "$(id -un)"
fi

desktop_user=${1:-${SUDO_USER:-}}
if [[ ! $desktop_user =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || ! id "$desktop_user" >/dev/null 2>&1; then
    printf 'Informe o usuário da sessão: sudo bash %s USUARIO\n' "$0" >&2
    exit 1
fi

shopt -s nullglob
wmi_devices=(/sys/bus/wmi/devices/79772EC5-04B1-4BFD-843C-61E7F77B6CC9-*)
if (( ${#wmi_devices[@]} == 0 )); then
    printf 'A interface WMI de bateria Acer não foi encontrada.\n' >&2
    exit 1
fi

kernel_release=$(uname -r)
# TuneD also handles this Acer's acpi-cpufreq CPU driver, for which the
# standalone power-profiles-daemon exposes only a placeholder backend.
if rpm -q power-profiles-daemon >/dev/null 2>&1; then
    dnf swap -y --setopt=install_weak_deps=False power-profiles-daemon tuned-ppd
else
    dnf install -y --setopt=install_weak_deps=False tuned-ppd
fi
dnf install -y --setopt=install_weak_deps=False \
    gcc make dkms "kernel-devel-$kernel_release"
systemctl enable --now tuned.service tuned-ppd.service
bash "$repo/scripts/setup-quickshell-power-profiles.sh"

module_name=acer-wmi-battery
module_version=9f90d75.1
source_dir=/usr/src/$module_name-$module_version
install -d -m 0755 "$source_dir"
for name in acer-wmi-battery.c Makefile LICENSE dkms.conf; do
    install -m 0644 "$repo/quickshell/vendor/acer-wmi-battery/$name" "$source_dir/$name"
done
if [[ ! -d /var/lib/dkms/$module_name/$module_version ]]; then
    dkms add -m "$module_name" -v "$module_version"
fi
if [[ $(dkms status -m "$module_name" -v "$module_version" -k "$kernel_release") != *': installed' ]]; then
    dkms install -m "$module_name" -v "$module_version" -k "$kernel_release"
fi
modprobe "$module_name"

health_file=/sys/bus/wmi/drivers/acer-wmi-battery/health_mode
if [[ ! -r $health_file ]] || ! read -r health_mode < "$health_file" || [[ ! $health_mode =~ ^[01]$ ]]; then
    printf 'O driver foi carregado, mas o firmware não disponibilizou health_mode.\n' >&2
    exit 1
fi

install -d -m 0755 /usr/local/libexec
install -m 0755 "$repo/quickshell/scripts/battery-charge.sh" /usr/local/libexec/quickshell-battery-charge
install -m 0644 "$repo/quickshell/system/acer-wmi-battery.conf" /etc/modules-load.d/acer-wmi-battery.conf
install -m 0644 "$repo/quickshell/system/quickshell-battery-charge.service" /etc/systemd/system/quickshell-battery-charge.service

sudoers_temp=$(mktemp)
trap 'rm -f -- "$sudoers_temp"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/quickshell-battery-charge toggle\n' "$desktop_user" > "$sudoers_temp"
visudo -cf "$sudoers_temp"
install -m 0440 "$sudoers_temp" /etc/sudoers.d/quickshell-battery-charge
restorecon -F /usr/local/libexec/quickshell-battery-charge /etc/sudoers.d/quickshell-battery-charge

systemctl daemon-reload
systemctl enable quickshell-battery-charge.service
systemctl restart quickshell-battery-charge.service
bash "$repo/scripts/setup-quickshell-battery-cache.sh"

printf '\nControles de bateria instalados.\n'
tuned-adm active
printf 'Health saver (1 = 80%%, 0 = full charge): '
cat "$health_file"

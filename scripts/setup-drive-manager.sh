#!/usr/bin/env bash
# Run as the desktop user; UDisks handles mounts through the system bus.
set -euo pipefail

if (( EUID == 0 )); then
    printf 'Execute como seu usuário: bash %s\n' "$0" >&2
    exit 1
fi
activate=true
for option in "$@"; do
    case $option in
        --no-activate) activate=false ;;
        --help) printf 'Uso: %s [--no-activate]\n' "$0"; exit 0 ;;
        *) printf 'Opção inválida: %s\n' "$option" >&2; exit 2 ;;
    esac
done

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
backup_suffix=$(date +%Y%m%d-%H%M%S)-$$

if ! rpm -q udisks2 python3-dbus python3-gobject >/dev/null 2>&1; then
    sudo dnf install -y --setopt=install_weak_deps=False udisks2 python3-dbus python3-gobject
fi
python3 -c 'import dbus; from gi.repository import GLib, GLibUnix'

source "$repo/scripts/lib/setup.sh"

link_file "$repo/drive-manager/drive-manager" "$HOME/.local/libexec/drive-manager"
link_file "$repo/drive-manager/drive-manager.service" "$config_dir/systemd/user/drive-manager.service"
link_file "$repo/drive-manager/drive-manager.service" "$config_dir/systemd/user/default.target.wants/drive-manager.service"
if $activate; then
    systemctl --user daemon-reload
    systemctl --user restart drive-manager.service
    systemctl --user --no-pager status drive-manager.service
fi
printf 'Drive-manager instalado. Ativação solicitada: %s.\n' "$activate"

#!/usr/bin/env bash
# Run as the desktop user; only missing fonts require sudo.
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

if [[ $(fc-match --format '%{family[0]}' 'Red Hat Text') != 'Red Hat Text' \
    || $(fc-match --format '%{family[0]}' 'Red Hat Mono') != 'Red Hat Mono' ]]; then
    sudo dnf install -y --setopt=install_weak_deps=False redhat-text-fonts redhat-mono-fonts
fi

bash "$repo/scripts/setup-font-fallbacks.sh"

source "$repo/scripts/lib/setup.sh"

link_file "$repo/fontconfig/conf.d/60-redhat.conf" "$config_dir/fontconfig/conf.d/60-redhat.conf"
install_cursor_settings
link_file "$repo/foot/foot.ini" "$config_dir/foot/foot.ini"
link_file "$repo/sway/config.d/50-fonts.conf" "$config_dir/sway/config.d/50-fonts.conf"

sway_config="$config_dir/sway/config"
ensure_sway_include

if $activate; then
    gsettings set org.gnome.desktop.interface font-name 'Red Hat Text 11'
    gsettings set org.gnome.desktop.interface document-font-name 'Red Hat Text 12'
    gsettings set org.gnome.desktop.interface monospace-font-name 'Red Hat Mono 11'
    gsettings set org.gnome.desktop.wm.preferences titlebar-font 'Red Hat Text Bold 11'
fi
fc-cache

if $activate && [[ -n ${SWAYSOCK-} ]]; then
    sway --validate --config "$sway_config"
    swaymsg reload
fi

printf 'Fontes configuradas: Red Hat Text, Red Hat Mono, Nerd Symbols e fallbacks Noto/DejaVu.\n'

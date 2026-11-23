#!/usr/bin/env bash
# Install the complete Sway lifecycle without changing a running session by default.
set -euo pipefail
if (( EUID == 0 )); then
    printf 'Execute como seu usuário: bash %s\n' "$0" >&2
    exit 1
fi
activate=false
replace_base=false
lifecycle_only=false
for option in "$@"; do
    case $option in
        --activate) activate=true ;;
        --no-activate) activate=false ;;
        --replace-base) replace_base=true ;;
        --lifecycle-only) lifecycle_only=true ;;
        --help) printf 'Uso: %s [--activate|--no-activate] [--replace-base]\n' "$0"; exit 0 ;;
        *) printf 'Opção inválida: %s\n' "$option" >&2; exit 2 ;;
    esac
done
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
backup_suffix=$(date +%Y%m%d-%H%M%S)-$$
source "$repo/scripts/lib/setup.sh"

# Keep local base bindings unless replacement was explicitly selected.
if $replace_base || [[ ! -e $config_dir/sway/config ]]; then
    link_file "$repo/sway/config" "$config_dir/sway/config"
fi
if ! $lifecycle_only; then
    for source in "$repo"/sway/config.d/*.conf; do
        link_file "$source" "$config_dir/sway/config.d/$(basename -- "$source")"
    done
    link_file "$repo/sway/wallpapers" "$config_dir/sway/wallpapers"
    link_file "$repo/sway/swaynag.conf" "$config_dir/swaynag/config"
    link_file "$repo/input/sway.conf" "$config_dir/sway/config.d/90-input.conf"
    install_cursor_settings
fi
link_file "$repo/sway/config.d/90-session.conf" "$config_dir/sway/config.d/90-session.conf"
for source in "$repo"/sway/scripts/*.sh "$repo/sway/scripts/idle.py" \
    "$repo/sway/scripts/outputs.py" "$repo/sway/scripts/polkit-agent.py"; do
    link_file "$source" "$config_dir/sway/scripts/$(basename -- "$source")"
done
link_file "$repo/input/start-ibus.sh" "$config_dir/sway/scripts/start-ibus.sh"
for name in sway-session sway-logout sway-idle sway-outputs; do
    link_file "$repo/sway/system/$name.service" "$config_dir/systemd/user/$name.service"
done
link_file "$repo/input/ibus-sway.service" "$config_dir/systemd/user/ibus-sway.service"
link_file "$repo/quickshell/system/quickshell.service" "$config_dir/systemd/user/quickshell.service"
link_file "$repo/quickshell" "$config_dir/quickshell"
link_file "$repo/anyrun/system/anyrun.service" "$config_dir/systemd/user/anyrun.service"
link_file "$repo/anyrun/system/kidex.service" "$config_dir/systemd/user/kidex.service"
link_file "$repo/anyrun/scripts/start-anyrun.sh" "$config_dir/anyrun/start-anyrun.sh"
ensure_sway_include

if $activate; then
    sway --validate --config "$config_dir/sway/config"
    systemctl --user daemon-reload
    if [[ -n ${SWAYSOCK:-} ]]; then
        swaymsg reload
        "$config_dir/sway/scripts/start-session.sh"
    fi
fi
printf 'Arquivos da sessão instalados. Ativação: %s.\n' "$activate"

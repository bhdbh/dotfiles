#!/usr/bin/env bash
# Run as the desktop user; only missing packages require sudo.
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

if ! rpm -q ibus ibus-panel ibus-gtk3 ibus-gtk4 >/dev/null 2>&1; then
    sudo dnf install -y --setopt=install_weak_deps=False ibus ibus-panel ibus-gtk3 ibus-gtk4
fi

source "$repo/scripts/lib/setup.sh"

link_file "$repo/input/XCompose" "$HOME/.XCompose"
link_file "$repo/input/XCompose" "$config_dir/ibus/Compose"
link_file "$repo/input/environment.conf" "$config_dir/environment.d/90-input.conf"
link_file "$repo/input/00-input.fish" "$config_dir/fish/conf.d/00-input.fish"
link_file "$repo/input/xkb/symbols/us" "$config_dir/xkb/symbols/us"
link_file "$repo/input/sway.conf" "$config_dir/sway/config.d/90-input.conf"
link_file "$repo/input/start-ibus.sh" "$config_dir/sway/scripts/start-ibus.sh"
link_file "$repo/input/ibus-sway.service" "$config_dir/systemd/user/ibus-sway.service"

session_options=(--no-activate --lifecycle-only)
bash "$repo/scripts/setup-session.sh" "${session_options[@]}"
sway_config="$config_dir/sway/config"
ensure_sway_include

if $activate; then
    dconf load /desktop/ibus/ < "$repo/input/ibus.dconf"
    systemctl --user daemon-reload
fi

if $activate && [[ -n ${SWAYSOCK-} ]]; then
    sway --validate --config "$sway_config"
    # Reload compose rules, and migrate an unsupervised daemon if one is running.
    systemctl --user stop ibus-sway.service
    if ibus engine >/dev/null 2>&1; then
        ibus exit
    fi
    swaymsg reload
    "$repo/input/start-ibus.sh"
    for (( attempt=0; attempt<30; attempt++ )); do
        if ibus engine xkb:us:intl:eng 2>/dev/null; then
            printf 'IBus ativo e persistido: US International; Super+Espaço alterna para ABNT2.\n'
            exit 0
        fi
        sleep 0.5
    done
    printf 'Não foi possível confirmar o engine. Consulte journalctl --user -u ibus-sway.service.\n' >&2
    exit 1
fi

if $activate; then
    printf 'Configuração instalada. Entre novamente no Sway para ativar o IBus.\n'
else
    printf 'Arquivos instalados. Execute novamente sem --no-activate para aplicar ibus.dconf e ativar IBus.\n'
fi

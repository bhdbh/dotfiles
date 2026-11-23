#!/usr/bin/env bash
# Install the portal in ~/.local; Fedora 1.22+ discovers user portal definitions.
set -euo pipefail

if (( EUID == 0 )); then
    printf 'Execute como seu usuário: bash %s\n' "$0" >&2
    exit 1
fi
skip_build=false
from_build=false
activate=true
for option in "$@"; do
    case $option in
        --skip-build) skip_build=true ;;
        --from-build) from_build=true; skip_build=true ;;
        --no-activate) activate=false ;;
        --help) printf 'Uso: %s [--skip-build|--from-build] [--no-activate]\n' "$0"; exit 0 ;;
        *) printf 'Opção inválida: %s\n' "$option" >&2; exit 2 ;;
    esac
done

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
data_dir=${XDG_DATA_HOME:-$HOME/.local/share}
build_dir=${XDG_CACHE_HOME:-$HOME/.cache}/termfilechooser-build
prefix=$HOME/.local
commit=ed89c164c28f353d636a8ac68c7e35ce37f882bd
backup_suffix=$(date +%Y%m%d-%H%M%S)-$$

for command in yazi foot python3 pkg-config; do
    command -v "$command" >/dev/null
done

if [[ $skip_build == false ]]; then
    bash "$repo/scripts/build-filechooser.sh"
    from_build=true
fi
if $from_build; then
    "$build_dir/venv/bin/meson" install --no-rebuild -C "$build_dir/portal-build"
    source_dir=$build_dir/source-$commit
    install -m 644 "$source_dir/LICENSE" \
        "$data_dir/xdg-desktop-portal-termfilechooser/LICENSE"
    install -m 644 "$build_dir/inih-r62/LICENSE.txt" \
        "$data_dir/xdg-desktop-portal-termfilechooser/LICENSE.inih"
fi

test -x "$prefix/libexec/xdg-desktop-portal-termfilechooser"
test -x "$data_dir/xdg-desktop-portal-termfilechooser/yazi-wrapper.sh"

source "$repo/scripts/lib/setup.sh"

link_file "$repo/xdg-desktop-portal/portals.conf" "$config_dir/xdg-desktop-portal/portals.conf"
link_file "$repo/xdg-desktop-portal-termfilechooser/config" \
    "$config_dir/xdg-desktop-portal-termfilechooser/config"
link_file "$data_dir/xdg-desktop-portal-termfilechooser/yazi-wrapper.sh" \
    "$config_dir/xdg-desktop-portal-termfilechooser/yazi-wrapper.sh"
# The upstream systemd pkg-config prefix installs here, outside the user search path.
unit_dir=$(pkg-config --variable=systemduserunitdir --define-variable="prefix=$prefix" systemd)
test -f "$unit_dir/xdg-desktop-portal-termfilechooser.service"
link_file "$unit_dir/xdg-desktop-portal-termfilechooser.service" \
    "$config_dir/systemd/user/xdg-desktop-portal-termfilechooser.service"
link_file "$repo/sway/system/sway-session.service" "$config_dir/systemd/user/sway-session.service"
link_file "$repo/sway/scripts/start-portals.sh" "$config_dir/sway/scripts/start-portals.sh"
link_file "$repo/sway/config.d/91-portals.conf" "$config_dir/sway/config.d/91-portals.conf"

bash "$repo/scripts/setup-session.sh" --no-activate --lifecycle-only
sway_config="$config_dir/sway/config"
ensure_sway_include

if $activate; then
    systemctl --user daemon-reload
fi
if $activate && [[ -n ${WAYLAND_DISPLAY-} ]]; then
    "$repo/sway/scripts/start-portals.sh"
    systemctl --user restart xdg-desktop-portal-termfilechooser.service
    systemctl --user restart xdg-desktop-portal.service
fi
printf 'Portal instalado: seleção de arquivos e pastas pelo Yazi no Foot.\n'

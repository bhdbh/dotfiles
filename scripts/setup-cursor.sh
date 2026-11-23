#!/usr/bin/env bash
# Run as the desktop user. --skip-build reapplies an installed theme.
set -euo pipefail

if (( EUID == 0 )); then
    printf 'Execute como seu usuário: bash %s\n' "$0" >&2
    exit 1
fi
theme=Bibata-Modern-Zinc
skip_build=false
from_build=false
activate=true
while (( $# )); do
    case $1 in
        --skip-build) skip_build=true; shift ;;
        --from-build) from_build=true; skip_build=true; shift ;;
        --no-activate) activate=false; shift ;;
        --help) printf 'Uso: %s [--skip-build|--from-build] [--theme NOME] [--no-activate]\n' "$0"; exit 0 ;;
        --theme)
            if (( $# < 2 )); then
                printf 'Falta o nome após --theme.\n' >&2
                exit 2
            fi
            theme=$2
            shift 2
            ;;
        *)
            printf 'Uso: %s [--skip-build] [--theme Pixelarticons-Light|Pixelarticons-Dark|Bibata-Modern-Zinc]\n' "$0" >&2
            exit 2
            ;;
    esac
done
case $theme in
    Pixelarticons-Light|Pixelarticons-Dark) size=32 ;;
    Bibata-Modern-Zinc) size=24 ;;
    *) printf 'Tema não suportado: %s\n' "$theme" >&2; exit 2 ;;
esac

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
data_dir=${XDG_DATA_HOME:-$HOME/.local/share}
cache_dir=${XDG_CACHE_HOME:-$HOME/.cache}
backup_suffix=$(date +%Y%m%d-%H%M%S)-$$
theme_dir=$data_dir/icons/$theme

if [[ $skip_build == false ]]; then
    bash "$repo/scripts/build-cursor.sh" --theme "$theme"
    from_build=true
fi
if $from_build; then
    variants=("$theme")
    if [[ $theme == Pixelarticons-* ]]; then
        variants=(Pixelarticons-Light Pixelarticons-Dark)
    fi
    mkdir -p "$data_dir/icons"
    staging_dir=$(mktemp -d "$data_dir/icons/.cursor-install.XXXXXX")
    trap 'rm -rf -- "$staging_dir"' EXIT
    for variant in "${variants[@]}"; do
        case $variant in
            Pixelarticons-*) artifact=$cache_dir/cursor-build/$variant ;;
            *) artifact=$cache_dir/bibata-zinc/themes/$variant ;;
        esac
        for cursor in left_ptr hand2 xterm left_ptr_watch watch; do
            test -f "$artifact/cursors/$cursor"
        done
        cp -a "$artifact" "$staging_dir/$variant"
    done
    for variant in "${variants[@]}"; do
        target=$data_dir/icons/$variant
        if [[ -e $target || -L $target ]]; then
            mv -- "$target" "$target.bak.$backup_suffix"
        fi
        mv -- "$staging_dir/$variant" "$target"
    done
fi

for cursor in left_ptr hand2 xterm left_ptr_watch watch; do
    if [[ ! -f $theme_dir/cursors/$cursor ]]; then
        printf 'Cursor ausente: %s/cursors/%s\n' "$theme_dir" "$cursor" >&2
        exit 1
    fi
done

source "$repo/scripts/lib/setup.sh"
install_cursor_settings "$theme" "$size"
link_file "$theme_dir" "$HOME/.icons/$theme"

if $activate; then
    gsettings set org.gnome.desktop.interface cursor-theme "$theme"
    gsettings set org.gnome.desktop.interface cursor-size "$size"
    export XCURSOR_THEME=$theme XCURSOR_SIZE=$size
    systemctl --user daemon-reload
    systemctl --user import-environment XCURSOR_THEME XCURSOR_SIZE
    if command -v dbus-update-activation-environment >/dev/null; then
        dbus-update-activation-environment --systemd XCURSOR_THEME XCURSOR_SIZE
    fi
    if [[ -n ${SWAYSOCK-} ]]; then
        sway --validate --config "$config_dir/sway/config"
        # Sway caches cursors while name and size are unchanged, even after a rebuild.
        swaymsg seat '*' xcursor_theme Adwaita 24
        swaymsg reload
    fi
    if systemctl --user is-active --quiet quickshell.service; then
        systemctl --user restart quickshell.service
    fi
fi
printf '%s instalado, em %s px. Ativação solicitada: %s.\n' "$theme" "$size" "$activate"

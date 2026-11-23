#!/usr/bin/env bash
# Run as the desktop user. --skip-build reapplies an existing build.
set -euo pipefail

if (( EUID == 0 )); then
    printf 'Execute como seu usuário: bash %s\n' "$0" >&2
    exit 1
fi
skip_build=false
activate=true
for option in "$@"; do
    case $option in
        --skip-build) skip_build=true ;;
        --no-activate) activate=false ;;
        --help) printf 'Uso: %s [--skip-build] [--no-activate]\n' "$0"; exit 0 ;;
        *) printf 'Opção inválida: %s\n' "$option" >&2; exit 2 ;;
    esac
done

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
target_dir=${CARGO_TARGET_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/anyrun-build}
export CARGO_TARGET_DIR=$target_dir
backup_suffix=$(date +%Y%m%d-%H%M%S)-$$
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

if [[ $skip_build == false ]]; then
    if [[ -z ${ANYRUN_SYSROOT-} ]] && { ! command -v cargo >/dev/null || ! pkg-config --exists gtk4 gtk4-layer-shell-0; }; then
        sudo dnf install -y --setopt=install_weak_deps=False \
            cargo rust gcc pkgconf-pkg-config gtk4-devel gtk4-layer-shell-devel wl-clipboard
    fi
    bash "$repo/scripts/build-anyrun.sh"
fi

# Check every artifact before changing the installed setup.
for artifact in anyrun anyrun-provider kidex kidex-client \
    libapplications.so librink.so libshell.so libactions.so libkidex.so; do
    if [[ ! -f $target_dir/release/$artifact ]]; then
        printf 'Build ausente: %s\n' "$target_dir/release/$artifact" >&2
        exit 1
    fi
done

source "$repo/scripts/lib/setup.sh"

# Paths in RON are rendered once; templates stay portable in the dotfiles.
render_config() {
    local source=$1 target=$2
    mkdir -p -- "$(dirname -- "$target")"
    python3 - "$source" "$target" "$config_dir" "$backup_suffix" <<'PY'
import json
import os
from pathlib import Path
import shutil
import sys

source, target = map(Path, sys.argv[1:3])
# Escape path contents for RON's quoted strings, including unusual home names.
text = source.read_text()
for token, value in {'@HOME@': os.path.expanduser('~'), '@CONFIG_HOME@': sys.argv[3]}.items():
    text = text.replace(token, json.dumps(value, ensure_ascii=False)[1:-1])
if target.exists() and target.read_text() == text:
    sys.exit(0)
if target.exists() or target.is_symlink():
    shutil.copy2(target, str(target) + '.bak.' + sys.argv[4])
    target.unlink()
target.write_text(text)
PY
}

mkdir -p "$HOME/.local/bin" "$HOME/.local/libexec" "$config_dir/anyrun/plugins"
for binary in anyrun kidex kidex-client; do
    install -m 755 "$target_dir/release/$binary" "$HOME/.local/bin/$binary.new"
    mv -f -- "$HOME/.local/bin/$binary.new" "$HOME/.local/bin/$binary"
done
install -m 755 "$target_dir/release/anyrun-provider" "$HOME/.local/libexec/anyrun-prefix-provider.new"
mv -f -- "$HOME/.local/libexec/anyrun-prefix-provider.new" "$HOME/.local/libexec/anyrun-prefix-provider"
for plugin in applications rink shell actions kidex; do
    install -m 755 "$target_dir/release/lib$plugin.so" "$config_dir/anyrun/plugins/lib$plugin.so.new"
    mv -f -- "$config_dir/anyrun/plugins/lib$plugin.so.new" "$config_dir/anyrun/plugins/lib$plugin.so"
done

if [[ -n ${ANYRUN_SYSROOT-} ]]; then
    # Only the missing layer-shell runtime is installed; GTK comes from Fedora.
    mkdir -p "$HOME/.local/lib/anyrun"
    install -m 755 "$ANYRUN_SYSROOT/usr/lib64/libgtk4-layer-shell.so.0" \
        "$HOME/.local/lib/anyrun/libgtk4-layer-shell.so.0"
fi

render_config "$repo/anyrun/config/config.ron" "$config_dir/anyrun/config.ron"
render_config "$repo/anyrun/config/kidex-indexer.ron" "$config_dir/kidex.ron"
for name in actions.ron applications.ron kidex.ron rink.ron shell.ron search.ignore style.css; do
    link_file "$repo/anyrun/config/$name" "$config_dir/anyrun/$name"
done
link_file "$repo/anyrun/scripts/start-anyrun.sh" "$config_dir/anyrun/start-anyrun.sh"
link_file "$repo/anyrun/system/anyrun.service" "$config_dir/systemd/user/anyrun.service"
link_file "$repo/anyrun/system/kidex.service" "$config_dir/systemd/user/kidex.service"
link_file "$repo/sway/config.d/75-anyrun.conf" "$config_dir/sway/config.d/75-anyrun.conf"

bash "$repo/scripts/setup-session.sh" --no-activate --lifecycle-only
sway_config="$config_dir/sway/config"
ensure_sway_include

link_file "$repo/anyrun/system/kidex.service" "$config_dir/systemd/user/default.target.wants/kidex.service"
if $activate; then
    systemctl --user daemon-reload
fi
if $activate && [[ -n ${SWAYSOCK-} ]]; then
    sway --validate --config "$sway_config"
    "$repo/anyrun/scripts/start-anyrun.sh"
    systemctl --user restart kidex.service anyrun.service
    swaymsg reload
fi
printf 'Anyrun instalado. Win+D abre o launcher; ? mostra os prefixos.\n'

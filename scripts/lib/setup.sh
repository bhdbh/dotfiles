# Shared file operations for user installers. Source after setting repo/config_dir.
# These helpers never activate services or modify the source tree.
link_file() {
    local source=$1 target=$2
    [[ -e $source ]] || { printf 'Fonte ausente: %s\n' "$source" >&2; return 1; }
    mkdir -p -- "$(dirname -- "$target")"
    if [[ -L $target && $(readlink -- "$target") == "$source" ]]; then
        return
    fi
    if [[ -e $target || -L $target ]]; then
        local backup=$target.bak.$backup_suffix number=0
        while [[ -e $backup || -L $backup ]]; do
            number=$((number + 1))
            backup=$target.bak.$backup_suffix.$number
        done
        mv -- "$target" "$backup"
    fi
    ln -s -- "$source" "$target"
}

write_config() {
    python3 -B "$repo/scripts/lib/config-files.py" write "$1" "$backup_suffix"
}

ensure_sway_include() {
    local target=$config_dir/sway/config
    python3 -B "$repo/scripts/lib/config-files.py" include "$target" "$config_dir/sway/config.d" "$backup_suffix"
}

install_cursor_settings() {
    python3 -B "$repo/scripts/lib/config-files.py" cursor "$repo" "$config_dir" \
        "${XDG_DATA_HOME:-$HOME/.local/share}" "$HOME" "$backup_suffix" "$@"
}

#!/usr/bin/env bash
# Installed root-owned; sudo permits only the fixed "toggle" command.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly health_file=/sys/bus/wmi/drivers/acer-wmi-battery/health_mode
readonly state_dir=/var/lib/quickshell-battery
readonly state_file=$state_dir/health_mode

read_mode() {
    local mode
    if [[ ! -r $health_file ]]; then
        printf 'O driver acer-wmi-battery não está carregado.\n' >&2
        return 1
    fi
    read -r mode < "$health_file"
    case $mode in
        0|1) printf '%s\n' "$mode" ;;
        *) printf 'Health mode não é suportado neste notebook.\n' >&2; return 1 ;;
    esac
}

write_mode() {
    local mode=$1
    case $mode in 0|1) ;; *) return 1 ;; esac
    printf '%s\n' "$mode" > "$health_file"
    if [[ $(read_mode) != "$mode" ]]; then
        printf 'O firmware não confirmou a alteração do limite de carga.\n' >&2
        return 1
    fi
    install -d -m 0755 "$state_dir"
    printf '%s\n' "$mode" > "$state_file"
    chmod 0644 "$state_file"
    printf '%s\n' "$mode"
}

case ${1-} in
    toggle)
        [[ $# == 1 ]] || exit 2
        current=$(read_mode)
        write_mode "$((1 - current))"
        ;;
    restore)
        [[ $# == 1 ]] || exit 2
        if [[ -f $state_file ]]; then
            read -r saved < "$state_file"
            write_mode "$saved"
        else
            write_mode "$(read_mode)"
        fi
        ;;
    *) printf 'Uso: %s {toggle|restore}\n' "$0" >&2; exit 2 ;;
esac

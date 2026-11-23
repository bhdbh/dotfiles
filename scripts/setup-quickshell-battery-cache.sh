#!/usr/bin/env bash
# Reduce the ACPI battery status cache and persist it across boots.
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if (( EUID != 0 )); then
    exec sudo -- bash "$repo/scripts/setup-quickshell-battery-cache.sh"
fi

cache_file=/sys/module/battery/parameters/cache_time
if [[ ! -r $cache_file ]]; then
    printf 'O parâmetro de cache da bateria ACPI não está disponível.\n' >&2
    exit 1
fi

source_file=$repo/quickshell/system/quickshell-battery-cache.conf
target_file=/etc/tmpfiles.d/quickshell-battery-cache.conf
if [[ -e $target_file ]] && ! cmp -s -- "$source_file" "$target_file"; then
    cp -a -- "$target_file" "$target_file.bak.$(date +%Y%m%d-%H%M%S)-$$"
fi
install -m 0644 "$source_file" "$target_file"
restorecon -F "$target_file"
systemd-tmpfiles --create "$target_file"
[[ $(cat "$cache_file") == 100 ]]
printf 'Cache ACPI da bateria: 100 ms, aplicado agora e nos próximos boots.\n'

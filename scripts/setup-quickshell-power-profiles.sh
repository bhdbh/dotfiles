#!/usr/bin/env bash
# Preserve manual display settings when changing a TuneD power profile.
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if (( EUID != 0 )); then
    exec sudo -- bash "$repo/scripts/setup-quickshell-power-profiles.sh"
fi

if ! rpm -q tuned tuned-ppd >/dev/null 2>&1; then
    printf 'Instale os controles de energia primeiro: bash %s/scripts/setup-quickshell-power.sh\n' "$repo" >&2
    exit 1
fi

backup_suffix=$(date +%Y%m%d-%H%M%S)-$$
for profile in powersave balanced balanced-battery throughput-performance; do
    source_file=$repo/quickshell/tuned/$profile/tuned.conf
    target_dir=/etc/tuned/profiles/$profile
    target_file=$target_dir/tuned.conf
    install -d -m 0755 "$target_dir"
    if [[ -e $target_file ]] && ! cmp -s -- "$source_file" "$target_file"; then
        cp -a -- "$target_file" "$target_file.bak.$backup_suffix"
    fi
    install -m 0644 "$source_file" "$target_file"
    restorecon -RF "$target_dir"
done

systemctl restart tuned.service tuned-ppd.service
printf 'Perfis de energia atualizados: ajustes de vídeo desativados.\n'
tuned-adm active

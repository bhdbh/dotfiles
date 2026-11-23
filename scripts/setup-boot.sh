#!/usr/bin/env bash
# Fedora: independent GRUB timeout and explicitly selected kernel maintenance.
set -euo pipefail
export LC_ALL=C

usage() {
    printf 'Uso: %s timeout | remove-kernel VERSAO | VERSAO (modo combinado legado)\n' "$0"
}
valid_version() { [[ $1 =~ ^[0-9][0-9A-Za-z._+-]+$ ]]; }
if [[ ${1-} == --help ]]; then usage; exit 0; fi
mode=
old_kernel=
case ${1-} in
    timeout) [[ $# == 1 ]] || { usage >&2; exit 2; }; mode=timeout ;;
    remove-kernel)
        [[ $# == 2 ]] && valid_version "$2" || { usage >&2; exit 2; }
        mode=remove; old_kernel=$2
        ;;
    *)
        [[ $# == 1 ]] && valid_version "$1" || { usage >&2; exit 2; }
        mode=legacy; old_kernel=$1
        ;;
esac
if (( EUID != 0 )); then exec sudo -- bash "$0" "$@"; fi

current_kernel=$(uname -r)
if [[ $mode != timeout ]]; then
    if [[ $old_kernel == "$current_kernel" ]]; then
        printf 'Recusando remover o kernel em uso: %s\n' "$current_kernel" >&2
        exit 1
    fi
    rpm -q "kernel-core-$current_kernel" >/dev/null
    [[ -s /boot/vmlinuz-$current_kernel && -s /boot/initramfs-$current_kernel.img ]]
fi
[[ -s /etc/default/grub && -s /boot/grub2/grub.cfg ]]
backup=$(mktemp -d /var/backups/grub-maintenance.XXXXXXXX)
cp -a -- /etc/default/grub "$backup/default-grub"
cp -a -- /boot/grub2/grub.cfg "$backup/grub.cfg"
cp -aL -- /boot/grub2/grubenv "$backup/grubenv"
rpm -qa 'kernel*' | sort > "$backup/kernels-before.txt"
printf 'Backup do GRUB: %s\n' "$backup"

if [[ $mode != timeout ]]; then
    packages=()
    while IFS=' ' read -r name version; do
        if [[ $version == "$old_kernel" ]]; then packages+=("$name-$version"); fi
    done < <(rpm -qa 'kernel*' --qf '%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\n')
    if (( ${#packages[@]} )); then
        dnf --disable-repo='*' remove --no-autoremove -y "${packages[@]}"
    else
        printf 'Kernel %s já está ausente.\n' "$old_kernel"
    fi
    rpm -q "kernel-core-$current_kernel" >/dev/null
    [[ -s /boot/vmlinuz-$current_kernel && -s /boot/initramfs-$current_kernel.img ]]
fi

if [[ $mode != remove ]]; then
    python3 - <<'PY'
from pathlib import Path
import re
path = Path('/etc/default/grub')
text = path.read_text()
for name, value in {'GRUB_TIMEOUT': '0', 'GRUB_TIMEOUT_STYLE': 'hidden'}.items():
    pattern = rf'(?m)^{name}=.*$'
    if re.search(pattern, text):
        text = re.sub(pattern, f'{name}={value}', text)
    else:
        text = text.rstrip('\n') + f'\n{name}={value}\n'
path.write_text(text)
PY
    grub2-editenv - unset menu_auto_hide menu_show_once
    # Retain the old combined command's behavior; timeout alone never changes
    # which kernel boots (a newer installed kernel may be waiting for reboot).
    if [[ $mode == legacy ]]; then grubby --set-default "/boot/vmlinuz-$current_kernel"; fi
    grub2-mkconfig -o /boot/grub2/grub.cfg
    grub2-script-check /boot/grub2/grub.cfg
    restorecon -F /etc/default/grub /boot/grub2/grub.cfg /boot/grub2/grubenv
fi
printf 'Concluído: %s. Backup de configurações não reinstala pacotes removidos.\n' "$mode"

#!/bin/bash
# Apply matching TTY visuals to kmscon and Plymouth on Fedora.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $EUID -ne 0 ]]; then
    exec sudo -- bash "$repo/scripts/setup-boot-console.sh" "$@"
fi
[[ $# == 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

# Do not restart kmscon: it may own the logged-in desktop session.
dnf install -y --setopt=install_weak_deps=False \
    kmscon kbd-misc plymouth-plugin-script plymouth-plugin-label \
    plymouth-scripts dejavu-sans-mono-fonts

work=$(mktemp -d /var/tmp/console-rice.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
font=/usr/lib/kbd/consolefonts/eurlatgr.psfu.gz
theme=/usr/share/plymouth/themes/tty-rice
python3 "$repo/boot-console/render-prompt.py" --font "$font" --output-dir "$work/theme"
install -m 0644 "$repo/boot-console/plymouth/"* "$work/theme/"
install -m 0644 /usr/share/licenses/kbd/COPYING "$work/theme/COPYING.font"
install -m 0644 /usr/lib/kbd/consolefonts/README.eurlatgr "$work/theme/README.font"

versions=()
for modules in /usr/lib/modules/*; do
    version=${modules##*/}
    if [[ -f /boot/vmlinuz-$version && -f /boot/initramfs-$version.img ]]; then
        versions+=("$version")
    fi
done
[[ ${#versions[@]} -gt 0 ]] || { printf 'No regular Fedora kernel images found.\n' >&2; exit 1; }

# Keep a complete restore script, including the original initramfs images.
install -d -m 0755 /var/backups
backup=$(mktemp -d /var/backups/console-rice.XXXXXXXX)
chmod 0700 "$backup"
mkdir "$backup/files"
: >"$backup/paths"
save_path() {
    printf '%s\n' "$1" >>"$backup/paths"
    if [[ -e $1 || -L $1 ]]; then
        cp -a --parents -- "$1" "$backup/files/"
    fi
}
save_path /etc/kmscon/kmscon.conf
save_path /etc/plymouth/plymouthd.conf
save_path /etc/dracut.conf.d/60-console-rice.conf
save_path "$theme"
for version in "${versions[@]}"; do
    save_path "/boot/initramfs-$version.img"
done
cat >"$backup/restore.sh" <<'RESTORE'
#!/bin/bash
set -euo pipefail
[[ $EUID == 0 ]] || { printf 'Run this restore script with sudo.\n' >&2; exit 1; }
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
backup=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
while IFS= read -r path; do
    saved=$backup/files$path
    if [[ -e $saved || -L $saved ]]; then
        mkdir -p -- "$(dirname -- "$path")"
        if [[ -d $saved && ! -L $saved ]]; then
            rm -rf -- "$path"
            cp -a -- "$saved" "$path"
        else
            # Rename within the destination filesystem, including /boot.
            temporary=$(mktemp "$(dirname -- "$path")/.console-rice-restore.XXXXXXXX")
            rm -f -- "$temporary"
            cp -a -- "$saved" "$temporary"
            mv -fT -- "$temporary" "$path"
        fi
        restorecon -RF -- "$path"
    else
        rm -rf -- "$path"
    fi
done <"$backup/paths"
printf 'Previous console configuration and initramfs restored. Reboot to load them.\n'
RESTORE
chmod 0700 "$backup/restore.sh"

rollback() {
    result=$?
    trap - EXIT
    if [[ $result != 0 ]]; then
        printf 'Installation failed; restoring previous files from %s\n' "$backup" >&2
        bash "$backup/restore.sh" || printf 'Automatic restore failed: run %s\n' "$backup/restore.sh" >&2
    fi
    rm -rf -- "$work"
    exit "$result"
}
trap rollback EXIT
printf 'Backup and restore script: %s\n' "$backup"

install -Dm0644 "$repo/boot-console/kmscon.conf" /etc/kmscon/kmscon.conf
install -d -m 0755 "$theme"
install -m 0644 "$work/theme/"* "$theme/"
install -d -m 0755 /etc/dracut.conf.d
printf 'add_dracutmodules+=" plymouth "\n' >/etc/dracut.conf.d/60-console-rice.conf
plymouth-set-default-theme tty-rice
restorecon -RF /etc/kmscon/kmscon.conf /etc/plymouth/plymouthd.conf \
    /etc/dracut.conf.d/60-console-rice.conf "$theme"

# Build and inspect every regular kernel before publishing any of the images.
for version in "${versions[@]}"; do
    image=$work/initramfs-$version.img
    printf 'Building initramfs for %s\n' "$version"
    dracut --force --kver "$version" "$image"
    lsinitrd "$image" >"$work/contents"
    for required in \
        usr/share/plymouth/themes/tty-rice/tty-rice.plymouth \
        usr/share/plymouth/themes/tty-rice/tty-rice.script \
        usr/share/plymouth/themes/tty-rice/disk-password.png \
        usr/share/plymouth/themes/tty-rice/cursor.png \
        usr/lib64/plymouth/script.so \
        usr/lib64/plymouth/label-freetype.so \
        usr/bin/systemd-cryptsetup; do
        if ! grep -Fq -- "$required" "$work/contents"; then
            printf 'Required initramfs file missing: %s\n' "$required" >&2
            exit 1
        fi
    done
    cmp "$theme/tty-rice.script" \
        <(lsinitrd -f usr/share/plymouth/themes/tty-rice/tty-rice.script "$image")
    lsinitrd -f etc/plymouth/plymouthd.conf "$image" >"$work/plymouthd.conf"
    grep -qx 'Theme=tty-rice' "$work/plymouthd.conf"
done
for version in "${versions[@]}"; do
    image=/boot/initramfs-$version.img
    temporary=$(mktemp /boot/.console-rice.XXXXXXXX)
    if ! install -m 0600 "$work/initramfs-$version.img" "$temporary"; then
        rm -f -- "$temporary"
        exit 1
    fi
    mv -fT -- "$temporary" "$image"
    restorecon -F "$image"
done

printf '\nInstalled: VGA 8x16 kmscon and Plymouth prompt "Disk Password: " (input hidden).\n'
printf 'The changes take effect on the next boot. No services were restarted.\n'
printf 'Restore: sudo bash %s/restore.sh\n' "$backup"

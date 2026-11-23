#!/bin/bash
# Install the real-console locker and the Sway idle integration on Fedora.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
activate=true
desktop_user=
while (( $# )); do
    case $1 in
        --no-activate) activate=false; shift ;;
        --user) desktop_user=${2:?Missing username}; shift 2 ;;
        --help) printf 'Usage: %s [--no-activate] [--user USER]\n' "$0"; exit 0 ;;
        --*) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
        *) [[ -z $desktop_user ]] || exit 2; desktop_user=$1; shift ;;
    esac
done
if [[ $EUID -ne 0 ]]; then
    arguments=(--user "${desktop_user:-$(id -un)}")
    $activate || arguments+=(--no-activate)
    exec sudo -- bash "$repo/scripts/setup-sway-idle.sh" "${arguments[@]}"
fi
desktop_user=${desktop_user:?Pass the desktop username with --user}
[[ $desktop_user =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || exit 2
id "$desktop_user" >/dev/null
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
setup_log=/var/log/sway-tty-lock-setup.log
touch "$setup_log"
chmod 0644 "$setup_log"
exec > >(tee -a "$setup_log") 2>&1

dnf install -y --setopt=install_weak_deps=False swayidle gcc make pam-devel systemd-devel
build_directory=$(mktemp -d /var/tmp/sway-tty-lock-build.XXXXXX)
sudoers_file=$(mktemp)
trap 'rm -rf -- "$build_directory"; rm -f -- "$sudoers_file"' EXIT
for source_file in Makefile config.def.h version.h physlock.h main.c options.c session.c util.c vt.c console.c; do
    cp "$repo/sway/vendor/physlock/$source_file" "$build_directory/"
done
make -C "$build_directory" HAVE_SYSTEMD=1 HAVE_ELOGIND=0 \
    CFLAGS='-O2 -std=gnu11 -D_FORTIFY_SOURCE=3 -fstack-protector-strong -fPIE' \
    LDFLAGS='-Wl,-z,relro,-z,now -pie'
install -m 0755 "$build_directory/physlock" /usr/local/bin/physlock-sway
install -Dm0644 "$repo/sway/vendor/physlock/LICENSE" /usr/local/share/licenses/physlock-sway/LICENSE
install -m 0755 "$repo/sway/scripts/tty-lock-root.sh" /usr/local/libexec/sway-tty-lock
install -m 0644 "$repo/sway/system/sway-tty-lock.pam" /etc/pam.d/sway-tty-lock
install -m 0644 "$repo/sway/system/sway-tty-lock.service" /etc/systemd/system/sway-tty-lock.service
for action in lock idle wake suspend; do
    printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/sway-tty-lock %s\n' "$desktop_user" "$action" >>"$sudoers_file"
done
visudo -cf "$sudoers_file"
install -m 0440 "$sudoers_file" /etc/sudoers.d/sway-tty-lock
restorecon -F /usr/local/bin/physlock-sway /usr/local/libexec/sway-tty-lock \
    /etc/pam.d/sway-tty-lock /etc/systemd/system/sway-tty-lock.service /etc/sudoers.d/sway-tty-lock
if $activate; then systemctl daemon-reload; fi
printf 'Installed swayidle and the TTY locker. No session was locked during installation.\n'
printf 'Install the user-session links as the desktop user with scripts/setup-session.sh.\n'

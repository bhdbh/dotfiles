#!/bin/bash
# Installed root-owned; sudoers permits only the four literal commands below.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LANG=C LC_ALL=C

[[ $(id -u) == 0 ]] || { printf 'Root privileges are required.\n' >&2; exit 1; }
case ${1-} in lock|idle|wake|suspend) mode=$1 ;; *) exit 2 ;; esac
[[ $# == 1 ]] || exit 2

install -d -m 0700 /run/sway-tty-lock
exec 9>/run/sway-tty-lock/start.lock
flock --wait 1 9

suspend_now() {
    # The before-sleep callback also uses this helper. Release the startup lock
    # before requesting suspend so that callback can confirm the existing lock.
    flock -u 9
    exec 9>&-
    exec systemctl suspend
}

if systemctl is-active --quiet sway-tty-lock.service; then
    if [[ $mode == wake ]]; then
        systemctl kill --kill-whom=main --signal=SIGUSR1 sway-tty-lock.service
    elif [[ $mode == suspend ]]; then
        suspend_now
    fi
    exit 0
fi

# A caller may lock only their own active local session.
session=$(loginctl show-seat seat0 -p ActiveSession --value)
[[ -n $session ]] || { printf 'No active local session found.\n' >&2; exit 1; }
owner=$(loginctl show-session "$session" -p User --value)
[[ ${SUDO_UID-0} == 0 || ${SUDO_UID-} == "$owner" ]] || {
    printf 'The active session belongs to another user.\n' >&2
    exit 1
}
blank=
[[ $mode != idle && $mode != suspend ]] || blank=-b
printf 'BLANK_OPTION=%s\n' "$blank" >/run/sway-tty-lock/environment
chmod 0600 /run/sway-tty-lock/environment
# Type=notify returns only after the console is secured and its prompt is ready.
systemctl start sway-tty-lock.service
if [[ $mode == suspend ]]; then
    suspend_now
fi

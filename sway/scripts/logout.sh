#!/usr/bin/env bash
set -euo pipefail

# A Sway reload must not start graphical services again during logout.
exec 9>"${XDG_RUNTIME_DIR:?}/sway-session-start.lock"
flock 9
[[ -S ${SWAYSOCK:-} ]] || { printf 'No live Sway IPC socket.\n' >&2; exit 1; }

ipc=(swaymsg --raw --socket "$SWAYSOCK")
windows_open() {
    "${ipc[@]}" --type get_tree | jq -r 'any(.. | objects; .shell != null)'
}

# Sway's kill command asks Wayland and X11 windows to close normally.
open=$(windows_open)
if [[ $open == true ]]; then
    if ! close_reply=$("${ipc[@]}" '[all] kill'); then
        # The last window may have closed between get_tree and the request.
        if [[ $(windows_open) != false ]]; then
            printf 'Could not close Sway windows: %s\n' "$close_reply" >&2
            exit 1
        fi
    fi
    deadline=$((SECONDS + 30))
    while [[ $open == true && $SECONDS -lt $deadline ]]; do
        sleep 0.25
        open=$(windows_open)
    done
    if [[ $open == true ]]; then
        message='Logout cancelado. Feche as janelas que ainda estão abertas e tente novamente.'
        printf '%s\n' "$message" >&2
        flock -u 9
        timeout 10s swaynag --type warning --message "$message" || true
        exit 1
    fi
fi

# This stops the Sway guard and its services as well as the portal backends.
# The logout service itself lives outside that target and can finish the exit.
systemctl --user stop graphical-session.target

# Sway can dismantle the IPC connection before sending the reply to exit.
# Check for removal of this instance's socket to distinguish that from failure.
exit_reply=$("${ipc[@]}" exit 2>&1) || true
deadline=$((SECONDS + 5))
while [[ -S $SWAYSOCK && $SECONDS -lt $deadline ]]; do
    sleep 0.1
done
if [[ -S $SWAYSOCK ]]; then
    printf 'Sway did not exit: %s\n' "$exit_reply" >&2
    if "${ipc[@]}" --type get_version >/dev/null; then
        systemctl --user start sway-session.service
    fi
    exit 1
fi

# dbus-broker also forwards these empty values to the systemd manager.
busctl --user call org.freedesktop.DBus /org/freedesktop/DBus \
    org.freedesktop.DBus UpdateActivationEnvironment 'a{ss}' \
    3 SWAYSOCK '' WAYLAND_DISPLAY '' DISPLAY ''
systemctl --user unset-environment SWAYSOCK WAYLAND_DISPLAY DISPLAY

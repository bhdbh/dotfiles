#!/usr/bin/env bash
# The only entry point from Sway into the user service manager.
set -euo pipefail
exec 9>"${XDG_RUNTIME_DIR:?}/sway-session-start.lock"
flock 9
[[ -S ${SWAYSOCK:-} ]] || { printf 'No live Sway IPC socket.\n' >&2; exit 1; }
[[ -n ${WAYLAND_DISPLAY:-} ]] || { printf 'No Wayland display.\n' >&2; exit 1; }
export XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
export XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland
export XMODIFIERS=@im=ibus QT_IM_MODULES='wayland;ibus'
variables=()
absent=()
activation=()
for name in XDG_CONFIG_HOME SWAYSOCK WAYLAND_DISPLAY DISPLAY XDG_SESSION_ID XDG_SEAT XDG_VTNR \
        XDG_SESSION_TYPE XDG_CURRENT_DESKTOP XMODIFIERS QT_IM_MODULES; do
    if [[ -n ${!name-} ]]; then
        variables+=("$name")
    else
        absent+=("$name")
    fi
    activation+=("$name" "${!name-}")
done
# A later compositor may not have XWayland; do not retain the previous DISPLAY.
if (( ${#absent[@]} )); then systemctl --user unset-environment "${absent[@]}"; fi
systemctl --user import-environment "${variables[@]}"
busctl --user call org.freedesktop.DBus /org/freedesktop/DBus \
    org.freedesktop.DBus UpdateActivationEnvironment 'a{ss}' \
    "$(( ${#activation[@]} / 2 ))" "${activation[@]}"
# start is idempotent. Reload must not reset idle timers or monitor preference.
systemctl --user start sway-session.service

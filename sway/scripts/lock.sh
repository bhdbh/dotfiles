#!/bin/sh
set -eu

case "${1:-lock}" in
    lock|idle|wake|suspend) mode=${1:-lock} ;;
    *) printf 'Usage: %s [lock|idle|wake|suspend]\n' "$0" >&2; exit 2 ;;
esac

# Explicit suspend is secured by the root helper before it asks logind to sleep.
# Its mutex is bounded to 1 s and service readiness to 2 s. For before-sleep,
# leave at least 1 s of the usual 5 s inhibitor budget to the fallback.
if [ "$mode" = suspend ]; then
    if sudo -n /usr/local/libexec/sway-tty-lock suspend; then exit 0; fi
elif timeout --foreground 3s sudo -n /usr/local/libexec/sway-tty-lock "$mode"; then
    exit 0
fi

printf 'The TTY locker could not start; using swaylock.\n' >&2
runtime="${XDG_RUNTIME_DIR:?}/sway-idle"
mkdir -p "$runtime"
# Serialize only fallback startup. Never hold this mutex while suspending:
# before-sleep re-enters lock.sh and must be able to confirm the existing lock.
exec 9>"$runtime/fallback.lock"
flock --nonblock 9 || exit 1
ready_pid=
[ ! -r "$runtime/fallback-ready" ] || IFS= read -r ready_pid < "$runtime/fallback-ready"
current_pid=$(pgrep -n -u "$(id -u)" -x swaylock || true)
if [ -z "$ready_pid" ] || [ "$ready_pid" != "$current_pid" ]; then
    # --daemonize returns only after acquiring the Wayland session lock.
    # A process merely existing is not a readiness guarantee.
    timeout --foreground 1s swaylock --daemonize --color 09090b 9>&- || exit 1
    current_pid=$(pgrep -n -u "$(id -u)" -x swaylock) || exit 1
    printf '%s\n' "$current_pid" > "$runtime/fallback-ready"
fi
case "$mode" in
    idle)
        swaymsg 'output * power off'
        touch "$runtime/fallback-displays-off"
        ;;
    wake)
        # Restore only after the locker has confirmed readiness above.
        swaymsg 'output * power on'
        rm -f "$runtime/fallback-displays-off"
        ;;
    suspend)
        flock -u 9
        exec 9>&-
        exec systemctl suspend
        ;;
esac

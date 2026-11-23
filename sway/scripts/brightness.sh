#!/bin/sh
set -eu

case "${1:-}" in
    up|down) direction=$1 ;;
    *) printf 'Usage: %s up|down\n' "$0" >&2; exit 2 ;;
esac

# Serialize key repeats so each press uses the latest hardware brightness.
exec 9>"${XDG_RUNTIME_DIR:?}/sway-brightness.lock"
flock 9

backlight=
for candidate in /sys/class/backlight/*; do
    [ -r "$candidate/brightness" ] || continue
    backlight=$candidate
    break
done
if [ -z "$backlight" ]; then
    printf 'No screen backlight found.\n' >&2
    exit 1
fi

read -r maximum < "$backlight/max_brightness"
read -r current < "$backlight/brightness"
step=$(( (maximum * 5 + 99) / 100 ))
minimum=$(( (maximum + 99) / 100 ))
case "$direction" in
    up) target=$(( current + step )) ;;
    down) target=$(( current - step )) ;;
esac
[ "$target" -ge "$minimum" ] || target=$minimum
[ "$target" -le "$maximum" ] || target=$maximum

# logind authorizes the active local session; no root helper or udev rule needed.
exec busctl --system call org.freedesktop.login1 \
    /org/freedesktop/login1/session/auto org.freedesktop.login1.Session \
    SetBrightness ssu backlight "${backlight##*/}" "$target"

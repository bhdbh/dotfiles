#!/bin/sh
set -eu

# Keep repeated keys and overlapping shortcuts from stacking selectors.
exec 9>"${XDG_RUNTIME_DIR:?}/sway-screenshot.lock"
flock -n 9 || exit 0

case "${1:-all}" in
    all) set -- ;;
    output)
        # Sway's default focus_follows_mouse keeps this on the cursor's screen.
        output=$(swaymsg -r -t get_outputs | jq -er '.[] | select(.active and .focused) | .name')
        set -- -o "$output"
        ;;
    region)
        selector="$HOME/.local/libexec/sway-screenshot/slurp"
        [ -x "$selector" ] || selector=slurp
        # Match Anyrun's backdrop; leave the selected area fully transparent.
        geometry=$("$selector" -b '#09090bbf' -c '#52525bff' -s '#09090b00' -B '#27272a80' -w 3) || exit 0
        [ -n "$geometry" ] || exit 0
        set -- -g "$geometry"
        ;;
    *) printf 'Usage: %s all|output|region\n' "$0" >&2; exit 2 ;;
esac

directory="$HOME/Images/Screenshots"
mkdir -p "$directory"
destination="$directory/$(date +%Y-%m-%d_%H-%M-%S-%N).png"
grim "$@" "$destination"
# wl-copy stays in the background to own the clipboard; release its lock fd.
wl-copy --type image/png 9>&- < "$destination"

#!/bin/sh
set -eu

unit=sway-screen-recording.service
state_file=${XDG_RUNTIME_DIR:?}/sway-screen-recording
exec 9>"$state_file.lock"
flock -n 9 || exit 0

if systemctl --user is-active --quiet "$unit"; then
    exec systemctl --user stop "$unit"
fi

recorder=$(command -v wf-recorder || printf '%s\n' "$HOME/.local/bin/wf-recorder")
if [ ! -x "$recorder" ]; then
    printf 'Instale o pacote wf-recorder para gravar a tela.\n' >&2
    exit 1
fi

# The session uses Sway's default focus_follows_mouse and output warping.
output=$(swaymsg -r -t get_outputs | jq -er '.[] | select(.active and .focused) | .name')
directory="$HOME/Videos/Records"
mkdir -p "$directory"
destination="$directory/$(date +%Y-%m-%d_%H-%M-%S-%N).mp4"

# Stop indicating capture at shutdown while SIGINT lets wf-recorder finalize the MP4.
exec systemd-run --user --quiet --collect --unit="$unit" --expand-environment=no \
    --setenv="WAYLAND_DISPLAY=${WAYLAND_DISPLAY:?}" \
    --property=PartOf=sway-session.service --property=After=sway-session.service \
    --property=Type=exec --property=KillSignal=SIGINT --property=TimeoutStopSec=15s \
    --property="ExecStop=/usr/bin/rm -f '$state_file'" \
    --property="ExecStopPost=/usr/bin/rm -f '$state_file'" \
    -- sh -ec '
        printf "%s\n" "$1" > "$2"
        shift 2
        exec "$@"
    ' sh "$destination" "$state_file" \
    "$recorder" --output="$output" --audio-backend=pulse --audio=@DEFAULT_MONITOR@ \
    --no-damage --framerate=30 \
    --codec=libopenh264 --pixel-format=yuv420p --codec-param=b=6000000 --audio-codec=aac \
    --filter='pad=ceil(iw/2)*2:ceil(ih/2)*2' \
    --file="$destination"

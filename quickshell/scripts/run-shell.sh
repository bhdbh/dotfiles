#!/bin/sh
set -eu
# sway-outputs.service signals READY only after publishing primary-output.
primary_file="${XDG_RUNTIME_DIR:?}/sway-outputs/primary-output"
[ -S "${SWAYSOCK:?}" ] && [ -s "$primary_file" ] || exit 1
exec /usr/bin/quickshell --path "${XDG_CONFIG_HOME:-$HOME/.config}/quickshell" --no-duplicate

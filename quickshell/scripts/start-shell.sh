#!/bin/sh
# Compatibility entry for existing links. Environment and service startup are
# owned by the Sway session entry point.
set -eu
exec "${XDG_CONFIG_HOME:-$HOME/.config}/sway/scripts/start-session.sh"

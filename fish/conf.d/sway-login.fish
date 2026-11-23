# Start the desktop only from the interactive kmscon login on VT1.
# VS Code also runs fish -i -l to resolve its environment.
status is-login; and status is-interactive; or return
test "$XDG_VTNR" = 1; and test "$TERM" = kmscon; or return
test -t 0; or return

# Graphical applications inherit TERM=kmscon from the original login shell.
test -z "$WAYLAND_DISPLAY"; and test -z "$DISPLAY"; and test -z "$SWAYSOCK"; or return
set -q VSCODE_RESOLVING_ENVIRONMENT; and return

env XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland kmscon-launch-gui sway

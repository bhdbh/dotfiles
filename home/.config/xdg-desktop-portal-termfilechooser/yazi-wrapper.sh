#!/usr/bin/env sh
# Preserve termfilechooser's wrapper protocol; use the shared dialog launcher.
exec @HOME@/.local/libexec/yazi-dialog filechooser "$@"

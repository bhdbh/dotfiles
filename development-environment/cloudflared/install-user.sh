#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $EUID != 0 ]]
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -d -m 0700 "$HOME/.ssh" "$HOME/.ssh/config.d" "$HOME/.cloudflared"
if [[ -e $HOME/.ssh/config ]]; then
    cp -a "$HOME/.ssh/config" "$script_dir/ssh-config.before-$(date +%Y%m%dT%H%M%S)"
fi
if [[ -e $HOME/.ssh/config.d/10-example.conf ]]; then
    cp -a "$HOME/.ssh/config.d/10-example.conf" "$script_dir/ssh-example.before-$(date +%Y%m%dT%H%M%S)"
fi
install -m 0600 "$script_dir/ssh-example.conf" "$HOME/.ssh/config.d/10-example.conf"
python3 - <<'PY'
from pathlib import Path
filename = Path.home() / '.ssh/config'
contents = filename.read_text() if filename.exists() else ''
include = 'Include ~/.ssh/config.d/*.conf'
if include not in contents.splitlines():
    filename.write_text(include + '\n\n' + contents)
filename.chmod(0o600)
PY
restorecon -R "$HOME/.ssh" "$HOME/.cloudflared"
for host in example ssh.example.com; do
    settings=$(ssh -G "$host" 2>/dev/null)
    [[ $settings == *'user user'* ]]
    [[ $settings == *'hostname ssh.example.com'* ]]
    [[ $settings == *'proxycommand /usr/bin/cloudflared access ssh --hostname %h'* ]]
    printf '%s\n' "$settings" | sed -n '/^user /p; /^hostname /p; /^proxycommand /p'
done
cloudflared access ssh --help >/dev/null
cloudflared tunnel run --help >/dev/null
printf 'CLOUDFLARED_SSH_READY\n'

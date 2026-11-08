#!/usr/bin/env bash
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077
(( EUID == 0 )) || { printf 'Execute com sudo.\n' >&2; exit 1; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
backup_dir=/var/lib/local-development/cloudflared/$(date +%Y%m%dT%H%M%S)
install -d -m 0700 "$backup_dir"
if [[ -e /etc/yum.repos.d/cloudflared.repo ]]; then
    cp -a /etc/yum.repos.d/cloudflared.repo "$backup_dir/cloudflared.repo.before"
fi
install -m 0644 "$script_dir/cloudflared.repo" /etc/yum.repos.d/cloudflared.repo
restorecon /etc/yum.repos.d/cloudflared.repo
dnf5 --assumeyes --refresh install cloudflared
[[ -x /usr/bin/cloudflared ]]
/usr/bin/cloudflared --version
rpm -q cloudflared
printf 'CLOUDFLARED_SYSTEM_READY\nBACKUP_DIR=%s\n' "$backup_dir"

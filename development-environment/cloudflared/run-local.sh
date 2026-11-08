#!/usr/bin/env bash
set -uo pipefail
umask 077
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
printf 'Cloudflared — instalação e proxy SSH\n\n'
printf 'Instala o pacote estável assinado da Cloudflare e configura:\n'
printf 'ssh example -> user@ssh.example.com via Cloudflare Access.\n'
printf 'Digite sua senha somente no prompt do sudo abaixo.\n\n'
sudo -- /usr/bin/bash "$script_dir/install-system.sh" 2>&1 | tee "$script_dir/system-install.log"
result=${PIPESTATUS[0]}
if (( result == 0 )); then
    /usr/bin/bash "$script_dir/install-user.sh" 2>&1 | tee "$script_dir/user-install.log"
    result=${PIPESTATUS[0]}
fi
printf '%s\n' "$result" > "$script_dir/last-run.exit"
if (( result == 0 )); then
    printf '\nCloudflared instalado. Acesso ao servidor: ssh example\n'
else
    printf '\nExecução interrompida (código %s). Registros em %s.\n' "$result" "$script_dir"
fi
read -r -p 'Pressione Enter para fechar este terminal.' _
exit "$result"

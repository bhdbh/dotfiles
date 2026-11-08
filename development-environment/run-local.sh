#!/usr/bin/env bash
set -uo pipefail
umask 077
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
printf 'Ambiente local: Node.js, React/Vite, Hono/Vite e PostgreSQL\n\n'
printf 'Pacotes do Fedora: GitHub CLI, compiladores e PostgreSQL.\n'
printf 'O banco será inicializado apenas para conexões locais.\n'
printf 'Digite sua senha somente no prompt do sudo abaixo.\n\n'
sudo -- /usr/bin/bash "$script_dir/install-system.sh" 2>&1 | tee "$script_dir/system-install.log"
result=${PIPESTATUS[0]}
if (( result == 0 )); then
    /usr/bin/bash "$script_dir/install-user.sh" 2>&1 | tee "$script_dir/user-install.log"
    result=${PIPESTATUS[0]}
fi
printf '%s\n' "$result" > "$script_dir/last-run.exit"
if (( result == 0 )); then
    printf '\nAmbiente instalado e verificado. Abra um novo terminal Fish no VS Code.\n'
else
    printf '\nExecução interrompida (código %s). Registros em %s.\n' "$result" "$script_dir"
fi
read -r -p 'Pressione Enter para fechar este terminal.' _
exit "$result"

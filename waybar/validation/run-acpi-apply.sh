#!/usr/bin/bash
set -uo pipefail
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
printf '%s\n' 'Aplicar o cache ACPI de 100ms, que reduziu a espera por Charging nos testes.' 'A configuração será mantida após reiniciar o computador.'
sudo -- /usr/bin/python3 -I "$task_dir/apply-acpi-cache.py" 2>&1 | tee "$task_dir/acpi-cache-apply.log"
result=${PIPESTATUS[0]}
printf '%s\n' "$result" > "$task_dir/acpi-cache-apply.exit"
if (( result != 0 )); then
    printf '\n%s\n' 'O ajuste não concluiu. Pressione Enter para fechar.'
    read -r
else
    printf '\n%s\n' 'Ajuste concluído; a janela será fechada.'
    sleep 2
fi
exit "$result"

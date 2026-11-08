#!/usr/bin/bash
set -uo pipefail
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
printf '%s\n' 'Medição temporária do cache ACPI: compara 1000ms e 100ms.' 'Ao terminar, o cache e o limite de carga originais serão restaurados.'
sudo -- /usr/bin/python3 -I "$task_dir/measure-acpi-cache.py" 2>&1 | tee "$task_dir/acpi-cache-benchmark.log"
result=${PIPESTATUS[0]}
printf '%s\n' "$result" > "$task_dir/acpi-cache-benchmark.exit"
if (( result != 0 )); then
    printf '\n%s\n' 'O teste não concluiu. Pressione Enter para fechar.'
    read -r
else
    printf '\n%s\n' 'Teste concluído; a janela será fechada.'
    sleep 2
fi
exit "$result"

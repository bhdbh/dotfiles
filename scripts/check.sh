#!/usr/bin/env bash
# Local regression checks; no installer, service action, real mount or TTY lock.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
rust=false
qml=false
for option in "$@"; do
    case $option in
        --rust) rust=true ;;
        --qml) qml=true ;;
        --help) printf 'Uso: %s [--rust] [--qml]\n' "$0"; exit 0 ;;
        *) printf 'Opção inválida: %s\n' "$option" >&2; exit 2 ;;
    esac
done
python3 -B - "$repo" <<'PY'
import ast
from pathlib import Path
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
repo = Path(sys.argv[1])
for path in repo.rglob('*.sh'):
    subprocess.run(['bash', '-n', str(path)], check=True)
for path in repo.rglob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
for path in repo.rglob('*.toml'):
    tomllib.loads(path.read_text())
for relative in ['fontconfig/conf.d/60-redhat.conf', 'yazi/zinc.tmTheme']:
    ET.fromstring((repo / relative).read_text())
for path in repo.rglob('*.desktop'):
    subprocess.run(['desktop-file-validate', str(path)], check=True)
print('PASS: syntax and static configuration formats')
PY
python3 -B -m unittest discover -s "$repo/scripts/tests" -v
python3 -B -m unittest discover -s "$repo/sway/scripts" -p 'test_*.py' -v
python3 -B -m unittest discover -s "$repo/fish/tests" -v
python3 -B "$repo/drive-manager/test_drive_manager.py"
python3 -B "$repo/quickshell/tests/test-battery-driver.py"
if $qml; then
    # The fixture only imports the pure charge-sync component. Separate runtime
    # directory and invalid bus prevent contact with the logged-in shell.
    test_runtime=$(mktemp -d)
    trap 'rm -rf -- "$test_runtime"' EXIT
    XDG_RUNTIME_DIR=$test_runtime DBUS_SESSION_BUS_ADDRESS="unix:path=$test_runtime/no-session" \
        python3 -B "$repo/quickshell/tests/test-battery-charge-sync.py"
fi
if $rust; then
    export PATH="$HOME/.cargo/bin:$PATH"
    export CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/dotfiles-check}
    cargo test --offline --locked --manifest-path "$repo/anyrun-prefix-provider/Cargo.toml" --workspace
    cargo test --offline --locked --manifest-path "$repo/kidex/Cargo.toml" --workspace
    cargo test --offline --locked --manifest-path "$repo/anyrun/Cargo.toml" -p applications -p rink -p anyrun
fi

#!/usr/bin/env bash
# Apply Zinc to the TUIs and install the Yazi file chooser portal.
set -euo pipefail

if (( EUID == 0 )); then
    printf 'Execute como seu usuário: bash %s\n' "$0" >&2
    exit 1
fi
activate=true
for option in "$@"; do
    case $option in
        --skip-build|--from-build) ;;
        --no-activate) activate=false ;;
        --help) printf 'Uso: %s [--skip-build|--from-build] [--no-activate]\n' "$0"; exit 0 ;;
        *) printf 'Opção inválida: %s\n' "$option" >&2; exit 2 ;;
    esac
done

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
data_dir=${XDG_DATA_HOME:-$HOME/.local/share}
backup_suffix=$(date +%Y%m%d-%H%M%S)-$$

source "$repo/scripts/lib/setup.sh"

link_file "$repo/yazi/theme.toml" "$config_dir/yazi/theme.toml"
link_file "$repo/yazi/zinc.tmTheme" "$config_dir/yazi/zinc.tmTheme"
link_file "$repo/btop/themes/zinc.theme" "$config_dir/btop/themes/zinc.theme"
link_file "$repo/foot/foot.ini" "$config_dir/foot/foot.ini"
link_file "$repo/fish/conf.d/20-zinc.fish" "$config_dir/fish/conf.d/20-zinc.fish"
link_file "$repo/yazi/yazi.desktop" "$data_dir/applications/yazi.desktop"
foot --check-config

# btop saves its config on exit; keep it writable and preserve unrelated settings.
python3 - "$config_dir/btop/btop.conf" "$backup_suffix" <<'PY'
from pathlib import Path
import re
import shutil
import sys

path = Path(sys.argv[1])
old = path.read_text() if path.exists() else ""
new = old
for key, value in {"color_theme": '"zinc"', "theme_background": "true", "truecolor": "true"}.items():
    pattern = rf"(?m)^{key}\s*=.*$"
    line = f"{key} = {value}"
    new = re.sub(pattern, line, new) if re.search(pattern, new) else new.rstrip() + "\n" + line + "\n"
if new != old:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak." + sys.argv[2]))
    path.write_text(new)
PY

desktop-file-validate "$data_dir/applications/yazi.desktop"
update-desktop-database "$data_dir/applications"
xdg-mime default yazi.desktop inode/directory
association_options=()
$activate || association_options+=(--no-activate)
bash "$repo/scripts/setup-file-associations.sh" "${association_options[@]}"

bash "$repo/scripts/setup-filechooser.sh" "$@"
printf 'Zinc aplicado no Yazi, nas prévias de texto, no btop++, no Foot e no Fish.\n'

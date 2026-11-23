#!/usr/bin/env bash
# Shared XDG associations for Anyrun, plus the matching Yazi openers.
set -euo pipefail

if (( EUID == 0 )); then
    printf 'Execute como seu usuário: bash %s\n' "$0" >&2
    exit 1
fi

activate=true
for option in "$@"; do
    case $option in
        --no-activate) activate=false ;;
        --help) printf 'Uso: %s [--no-activate]\n' "$0"; exit 0 ;;
        *) printf 'Opção inválida: %s\n' "$option" >&2; exit 2 ;;
    esac
done

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
data_dir=${XDG_DATA_HOME:-$HOME/.local/share}
backup_suffix=$(date +%Y%m%d-%H%M%S)-$$

if ! rpm -q micro mpv swayimg >/dev/null 2>&1; then
    sudo dnf install -y --setopt=install_weak_deps=False micro mpv swayimg
fi
for command in micro mpv swayimg code foot; do
    command -v "$command" >/dev/null
done

source "$repo/scripts/lib/setup.sh"

link_file "$repo/yazi/yazi.toml" "$config_dir/yazi/yazi.toml"
link_file "$repo/file-associations/micro-foot.desktop" "$data_dir/applications/micro-foot.desktop"
link_file "$repo/file-associations/open-text.sh" "$HOME/.local/bin/dotfiles-open-text"
python3 - "$repo/file-associations/text-editor.desktop.in" \
    "$data_dir/applications/dotfiles-text-editor.desktop" "$HOME/.local/bin/dotfiles-open-text" \
    "$backup_suffix" <<'PY'
from pathlib import Path
import re
import shutil
import sys

source, target = map(Path, sys.argv[1:3])
# Desktop Entry Exec quoting, including its additional backslash escaping layer.
executable = sys.argv[3].replace("\\", "\\\\\\\\")
for char in ('"', '`', '$'):
    executable = executable.replace(char, "\\\\" + char)
if not re.fullmatch(r"[A-Za-z0-9_./-]+", executable):
    executable = '"' + executable + '"'
new = source.read_text().replace("@TEXT_OPENER@", executable)
old = target.read_text() if target.exists() else ""
if new != old:
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak." + sys.argv[4]))
    target.write_text(new)
PY
desktop-file-validate "$data_dir/applications/micro-foot.desktop"
desktop-file-validate "$data_dir/applications/dotfiles-text-editor.desktop"
update-desktop-database "$data_dir/applications"

python3 - "$config_dir/mimeapps.list" "$backup_suffix" <<'PY'
import configparser
import io
from pathlib import Path
import shutil
import sys

import gi
gi.require_version("GioUnix", "2.0")
from gi.repository import Gio, GioUnix

path = Path(sys.argv[1])
config = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=False)
config.optionxform = str
old = path.read_text() if path.exists() else ""
config.read_string(old)
for section in ("Default Applications", "Added Associations"):
    if not config.has_section(section):
        config.add_section(section)

# Include the player's container and playlist types as well as audio/video/*.
mpv = GioUnix.DesktopAppInfo.new("mpv.desktop")
assert mpv is not None, "mpv.desktop não encontrado"
media = set(mpv.get_supported_types() or [])
empty = {"application/x-zerosize", "inode/x-empty", "inode/empty"}
types = set(Gio.content_types_get_registered()) | media | empty
associations = {}
for mime in sorted(types):
    if mime.startswith("image/"):
        app = "swayimg.desktop"
    elif mime.startswith(("audio/", "video/")) or mime in media:
        app = "mpv.desktop"
    elif Gio.content_type_is_a(mime, "text/plain") or mime.startswith("text/") or mime in empty:
        app = "dotfiles-text-editor.desktop"
    else:
        continue
    associations[mime] = app
    config.set("Default Applications", mime, app + ";")
    previous = config.get("Added Associations", mime, fallback="").split(";")
    config.set("Added Associations", mime, ";".join([app] + [value for value in previous if value and value != app]) + ";")
    if config.has_option("Removed Associations", mime):
        removed = [value for value in config.get("Removed Associations", mime).split(";") if value and value != app]
        if removed:
            config.set("Removed Associations", mime, ";".join(removed) + ";")
        else:
            config.remove_option("Removed Associations", mime)

assert associations.get("text/plain") == "dotfiles-text-editor.desktop"
output = io.StringIO()
config.write(output, space_around_delimiters=False)
new = output.getvalue()
if new != old:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak." + sys.argv[2]))
    path.write_text(new)
print(f"Associações XDG aplicadas a {len(associations)} tipos de arquivo.")
PY

printf '.txt → Micro; outros textos → VS Code; áudio/vídeo → mpv; imagens → swayimg.\n'

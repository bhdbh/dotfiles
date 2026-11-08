#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $EUID != 0 ]]
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PATH="$HOME/.local/bin:$PATH"
download_dir=$(mktemp -d /tmp/fedora-fnm.XXXXXXXX)
trap 'rm -rf -- "$download_dir"' EXIT

# Artefato oficial fixado; SHA-256 publicado nos metadados da release GitHub.
curl --fail --silent --show-error --location \
    https://github.com/Schniz/fnm/releases/download/v1.39.0/fnm-linux.zip \
    --output "$download_dir/fnm-linux.zip"
printf '%s  %s\n' \
    7807664f39d39fc518da1c35ba0181e4b3267603c4b1dedeb4b5fc6ae440a224 \
    "$download_dir/fnm-linux.zip" | sha256sum --check
python3 - "$download_dir" <<'PY'
from pathlib import Path
import sys
import zipfile
directory = Path(sys.argv[1])
with zipfile.ZipFile(directory / 'fnm-linux.zip') as archive:
    (directory / 'fnm').write_bytes(archive.read('fnm'))
PY
install -d -m 0755 "$HOME/.local/bin"
install -m 0755 "$download_dir/fnm" "$HOME/.local/bin/fnm"
eval "$(fnm env --shell bash)"
fnm install 26
fnm default 26
fnm use 26
npm install --global pnpm@latest

# Expõe a versão padrão também aos processos do VS Code e a outros shells.
for tool in node npm npx pnpm pnpx; do
    destination="$HOME/.local/bin/$tool"
    target="$FNM_DIR/aliases/default/bin/$tool"
    if [[ ! -e $destination && ! -L $destination ]]; then
        ln -s -- "$target" "$destination"
    fi
done

install -d -m 0755 "$HOME/.config/fish/conf.d"
if [[ -e $HOME/.config/fish/conf.d/fnm.fish ]]; then
    cp -a "$HOME/.config/fish/conf.d/fnm.fish" "$script_dir/fnm.fish.before-$(date +%Y%m%dT%H%M%S)"
fi
cat > "$HOME/.config/fish/conf.d/fnm.fish" <<'FISH'
if test -x "$HOME/.local/bin/fnm"
    fish_add_path --path "$HOME/.local/bin"
    "$HOME/.local/bin/fnm" env --use-on-cd --version-file-strategy=recursive --shell fish | source
end
FISH
chmod 0644 "$HOME/.config/fish/conf.d/fnm.fish"

fnm --version
node --version
npm --version
pnpm --version
git --version
gh --version
psql --version
pg_config --version
pkg-config --modversion libpq openssl
fish -c 'node --version; npm --version; pnpm --version; fnm current'
psql -X -v ON_ERROR_STOP=1 -c 'SELECT current_user, current_database();'
node "$script_dir/verify.mjs"
printf 'USER_DEVELOPMENT_READY\n'

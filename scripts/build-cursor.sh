#!/usr/bin/env bash
# Render cursor artifacts in cache, without installing or selecting a theme.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cache_dir=${XDG_CACHE_HOME:-$HOME/.cache}
theme=Bibata-Modern-Zinc
if [[ ${1-} == --help ]]; then
    printf 'Uso: %s [--theme NOME]\n' "$0"
    exit 0
fi
if (( $# )); then
    [[ $# == 2 && $1 == --theme ]] || exit 2
    theme=$2
fi
case $theme in
    Pixelarticons-Light|Pixelarticons-Dark)
        python3 -B "$repo/cursors/build-pixelarticons.py" "$cache_dir/pixelarticons-cursors" "$cache_dir/cursor-build"
        ;;
    Bibata-Modern-Zinc)
        build_dir=$cache_dir/bibata-zinc
        commit=ddb09273cde49faeb6a32282660ff4293f52d6e0
        # Rendering uses Fedora's existing librsvg bindings. Build tools stay in a venv.
        python3 -c 'import gi, cairo; gi.require_version("Rsvg", "2.0"); from gi.repository import Rsvg'
        mkdir -p "$build_dir"
        curl -fL --retry 3 \
            "https://codeload.github.com/ful1e5/Bibata_Cursor/tar.gz/$commit" \
            -o "$build_dir/upstream.tar.gz"
        printf '%s  %s\n' \
            882741ad4c877bf557ae806e860224bc865f7460f4d9b99ec3790347c6923fc1 \
            "$build_dir/upstream.tar.gz" | sha256sum --check --status
        source_dir=$build_dir/source-$commit
        mkdir -p "$source_dir"
        tar -xzf "$build_dir/upstream.tar.gz" --strip-components=1 -C "$source_dir"
        python3 -m venv --system-site-packages "$build_dir/venv"
        "$build_dir/venv/bin/python" -m pip install --disable-pip-version-check 'clickgen==2.2.5'
        rm -rf -- "$build_dir/bitmaps" "$build_dir/themes/$theme"
        "$build_dir/venv/bin/python" "$repo/cursors/render-bibata.py" \
            "$source_dir/svg/modern" "$build_dir/bitmaps"
        "$build_dir/venv/bin/ctgen" "$source_dir/configs/normal/x.build.toml" \
            -p x11 -d "$build_dir/bitmaps" -o "$build_dir/themes" \
            -n "$theme" -c 'Bibata Modern: zinc-400 outline, zinc-600 fill.' \
            -s 16 20 24 28 32 40 48 64
        # ctgen 2.2.5 can return success after a manifest error; packaging happens last.
        test -f "$build_dir/themes/$theme/index.theme"
        cp "$source_dir/LICENSE" "$build_dir/themes/$theme/LICENSE"
        ;;
    *) printf 'Tema não suportado: %s\n' "$theme" >&2; exit 2 ;;
esac

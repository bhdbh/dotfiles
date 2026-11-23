#!/usr/bin/env bash
# Build the portal in cache; no files are installed into the desktop profile.
set -euo pipefail
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
data_dir=${XDG_DATA_HOME:-$HOME/.local/share}
build_dir=${XDG_CACHE_HOME:-$HOME/.cache}/termfilechooser-build
prefix=$HOME/.local
commit=ed89c164c28f353d636a8ac68c7e35ce37f882bd
if (( $# )); then
    [[ $# == 1 && $1 == --help ]] || exit 2
    printf 'Uso: %s\nBuild: %s/portal-build\n' "$0" "$build_dir"
    exit 0
fi
# Meson/Ninja and a static inih stay in the build cache; no sudo is needed.
pkg-config --exists libsystemd systemd
mkdir -p "$build_dir"
curl -fL --retry 3 \
    "https://codeload.github.com/hunkyburrito/xdg-desktop-portal-termfilechooser/tar.gz/$commit" \
    -o "$build_dir/termfilechooser.tar.gz"
curl -fL --retry 3 \
    https://codeload.github.com/benhoyt/inih/tar.gz/refs/tags/r62 \
    -o "$build_dir/inih-r62.tar.gz"
printf '%s  %s\n' \
    bb28e3e37d393db1863339ecef991c96a805aaf432b5462b5514f3effc3f9039 \
    "$build_dir/termfilechooser.tar.gz" \
    9c15fa751bb8093d042dae1b9f125eb45198c32c6704cd5481ccde460d4f8151 \
    "$build_dir/inih-r62.tar.gz" | sha256sum --check --status
source_dir=$build_dir/source-$commit
mkdir -p "$source_dir"
tar -xzf "$build_dir/termfilechooser.tar.gz" --strip-components=1 -C "$source_dir"
tar -xzf "$build_dir/inih-r62.tar.gz" -C "$build_dir"
python3 -m venv "$build_dir/venv"
"$build_dir/venv/bin/python" -m pip install --disable-pip-version-check \
    'meson==1.12.1' 'ninja==1.13.2'
export PATH="$build_dir/venv/bin:$PATH"
meson setup --wipe "$build_dir/inih-build" "$build_dir/inih-r62" \
    --prefix="$build_dir/deps" --libdir=lib --default-library=static \
    -Dwith_INIReader=false -Dtests=false
meson compile -C "$build_dir/inih-build"
meson install -C "$build_dir/inih-build"
export PKG_CONFIG_PATH="$build_dir/deps/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
meson setup --wipe "$build_dir/portal-build" "$source_dir" \
    --prefix="$prefix" --datadir="$data_dir" --libexecdir=libexec \
    --sysconfdir=/etc --buildtype=release \
    -Dsd-bus-provider=libsystemd -Dsystemd=enabled -Dman-pages=disabled
meson compile -C "$build_dir/portal-build"

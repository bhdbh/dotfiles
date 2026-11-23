#!/usr/bin/env bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cache=${XDG_CACHE_HOME:-$HOME/.cache}/screenshot-build
destination=$HOME/.local/libexec/sway-screenshot
archive=$cache/slurp-1.5.0.tar.gz

mkdir -p "$cache"
if [[ ! -f $archive ]]; then
    curl --fail --location --retry 3 \
        https://github.com/emersion/slurp/releases/download/v1.5.0/slurp-1.5.0.tar.gz \
        --output "$archive"
fi
printf '%s  %s\n' \
    eeb282b2adc8db5614b852596340b69da6f3954cf6cfbdc4392da509c934208a \
    "$archive" | sha256sum --check --status

build=$(mktemp -d "$cache/build.XXXXXX")
trap 'rm -rf -- "$build"' EXIT
tar -xzf "$archive" -C "$build"
source_dir=$build/slurp-1.5.0
patch --directory "$source_dir" --strip 1 < "$repo/sway/vendor/slurp/keyboard-init.patch"

if command -v wayland-scanner >/dev/null && \
        pkg-config --exists wayland-client wayland-cursor cairo xkbcommon wayland-protocols; then
    scanner=$(command -v wayland-scanner)
    protocol_dir=$(pkg-config --variable=pkgdatadir wayland-protocols)
    read -r -a compile_flags <<< "$(pkg-config --cflags wayland-client wayland-cursor cairo xkbcommon)"
    read -r -a link_flags <<< "$(pkg-config --libs wayland-client wayland-cursor cairo xkbcommon)"
else
    # Use signed Fedora headers and the installed runtime libraries, without sudo.
    architecture=$(uname -m)
    mkdir -p "$cache/rpms" "$build/headers"
    dnf download --destdir "$cache/rpms" \
        "wayland-devel.$architecture" "cairo-devel.$architecture" \
        "libxkbcommon-devel.$architecture" wayland-protocols-devel.noarch
    for package in "$cache/rpms/"*."$architecture".rpm "$cache/rpms/"*.noarch.rpm; do
        rpmkeys --checksig "$package"
        (cd "$build/headers" && rpm2cpio "$package" | \
            cpio --extract --make-directories --quiet --no-absolute-filenames)
    done
    scanner=$build/headers/usr/bin/wayland-scanner
    protocol_dir=$build/headers/usr/share/wayland-protocols
    compile_flags=(-I"$build/headers/usr/include" -I"$build/headers/usr/include/cairo")
    link_flags=(-l:libwayland-client.so.0 -l:libwayland-cursor.so.0 \
        -l:libcairo.so.2 -l:libxkbcommon.so.0)
fi

protocols=(
    "$protocol_dir/stable/xdg-shell/xdg-shell.xml"
    "$protocol_dir/staging/cursor-shape/cursor-shape-v1.xml"
    "$protocol_dir/unstable/tablet/tablet-unstable-v2.xml"
    "$protocol_dir/unstable/xdg-output/xdg-output-unstable-v1.xml"
    "$source_dir/protocol/wlr-layer-shell-unstable-v1.xml"
)
protocol_sources=()
for protocol in "${protocols[@]}"; do
    name=$(basename "$protocol" .xml)
    "$scanner" client-header "$protocol" "$build/$name-client-protocol.h"
    "$scanner" private-code "$protocol" "$build/$name-protocol.c"
    protocol_sources+=("$build/$name-protocol.c")
done

cc -std=c11 -O2 -Wall -Wextra -Wno-unused-parameter \
    "${compile_flags[@]}" -I"$source_dir/include" -I"$build" \
    "$source_dir/main.c" "$source_dir/pool-buffer.c" "$source_dir/render.c" \
    "${protocol_sources[@]}" "${link_flags[@]}" -lrt -o "$build/slurp"
"$build/slurp" -h >/dev/null
mkdir -p "$destination" "$HOME/Images/Screenshots"
install -m 755 "$build/slurp" "$destination/slurp"
install -m 644 "$source_dir/LICENSE" "$destination/LICENSE"
printf 'Seletor de captura corrigido instalado em %s.\n' "$destination/slurp"

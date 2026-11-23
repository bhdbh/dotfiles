#!/usr/bin/env bash
# Compile the selected launcher components; never install or activate them.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
target_dir=${CARGO_TARGET_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/anyrun-build}
if (( $# )); then
    [[ $# == 1 && $1 == --help ]] || exit 2
    printf 'Uso: %s\nArtifacts: %s/release\n' "$0" "$target_dir"
    exit 0
fi
command -v cargo >/dev/null || { printf 'Rust/Cargo necessário para compilar.\n' >&2; exit 1; }
if [[ -n ${ANYRUN_SYSROOT-} ]]; then
    export PKG_CONFIG_PATH="$ANYRUN_SYSROOT/usr/lib64/pkgconfig:$ANYRUN_SYSROOT/usr/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    export PKG_CONFIG_SYSROOT_DIR=${PKG_CONFIG_SYSROOT_DIR:-$ANYRUN_SYSROOT}
    export RUSTFLAGS="${RUSTFLAGS:+$RUSTFLAGS }-L native=$ANYRUN_SYSROOT/usr/lib64"
fi
pkg-config --exists gtk4 gtk4-layer-shell-0 || {
    printf 'Dependências de compilação ausentes: gtk4-devel gtk4-layer-shell-devel.\n' >&2
    exit 1
}
export RUSTFLAGS="${RUSTFLAGS:+$RUSTFLAGS }-C link-arg=-Wl,-rpath,\$ORIGIN/../lib/anyrun"
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-3}
export CARGO_PROFILE_RELEASE_STRIP=symbols
cargo build --locked --release --target-dir "$target_dir" \
    --manifest-path "$repo/anyrun/Cargo.toml" \
    -p anyrun -p applications -p rink -p shell -p actions -p kidex
cargo build --locked --release --target-dir "$target_dir" \
    --manifest-path "$repo/anyrun-prefix-provider/Cargo.toml"
cargo build --locked --release --target-dir "$target_dir" \
    --manifest-path "$repo/kidex/Cargo.toml" -p kidex -p kidex-client

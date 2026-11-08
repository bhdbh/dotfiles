#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
build_root=${BUILD_ROOT:-"$root/.build"}
jobs=${JOBS:-2}
component=${1:-help}

case "$component" in
    anyrun|provider|kidex)
        case "$component" in
            anyrun) source_dir="$root/anyrun"; packages=(-p anyrun -p applications -p shell -p rink -p kidex -p actions) ;;
            provider) source_dir="$root/anyrun-prefix-provider"; packages=(-p anyrun-provider) ;;
            kidex) source_dir="$root/kidex"; packages=(-p kidex -p kidex-client) ;;
        esac
        cargo build --locked --release --manifest-path "$source_dir/Cargo.toml" \
            --target-dir "$build_root/cargo" --jobs "$jobs" "${packages[@]}"
        ;;
    sway)
        export PKG_CONFIG_PATH="$root/sway-window-background/.build-deps/usr/lib64/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
        if [[ ! -f "$build_root/sway/build.ninja" ]]; then
            meson setup "$build_root/sway" "$root/sway-window-background" \
                --prefix=/usr --sysconfdir=/etc --buildtype=debugoptimized \
                -Dswaybar=false -Dswaynag=false -Dtray=disabled -Dman-pages=disabled
        fi
        ninja -C "$build_root/sway" -j "$jobs" sway/sway
        ;;
    waybar)
        mkdir -p "$build_root/waybar"
        cp -p "$root/waybar/scripts/Makefile" \
            "$root/waybar/scripts/"*.c \
            "$root/waybar/scripts/"*.h "$build_root/waybar/"
        make -C "$build_root/waybar" -j "$jobs"
        ;;
    portal)
        if [[ ! -f "$build_root/portal/build.ninja" ]]; then
            meson setup "$build_root/portal" "$root/xdg-desktop-portal-termfilechooser" \
                -Dman-pages=disabled
        fi
        ninja -C "$build_root/portal" -j "$jobs"
        ;;
    idle)
        pam_include=/usr/include
        if [[ ! -f "$pam_include/security/pam_appl.h" ]]; then
            pam_include="$root/sway-idle/.build-deps/usr/include"
        fi
        if [[ ! -f "$pam_include/security/pam_appl.h" ]]; then
            printf '%s\n' 'Falta pam-devel. Instale esse pacote antes de compilar o bloqueador TTY.' >&2
            exit 1
        fi
        drm_include=/usr/include
        if [[ ! -f "$drm_include/xf86drmMode.h" ]]; then
            drm_include="$root/sway-window-background/.build-deps/usr/include"
        fi
        if [[ ! -f "$drm_include/xf86drmMode.h" ]]; then
            printf '%s\n' 'Falta libdrm-devel para compilar o controle de energia dos monitores.' >&2
            exit 1
        fi
        mkdir -p "$build_root/idle"
        cp -p "$root/sway-idle/source/"* "$build_root/idle/"
        make -C "$build_root/idle" -j "$jobs" \
            CPPFLAGS="-I$pam_include -I$drm_include -I$drm_include/libdrm" \
            CFLAGS='-O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2 -fPIE' \
            LDFLAGS='-pie -Wl,-z,relro,-z,now' \
            ldlibs='-l:libpam.so.0 -l:libpam_misc.so.0 -l:libsystemd.so.0 -l:libdrm.so.2'
        ;;
    acer)
        mkdir -p "$build_root/acer"
        cp -p "$root/waybar/power/acer-wmi-battery/Makefile" \
            "$root/waybar/power/acer-wmi-battery/acer-wmi-battery.c" "$build_root/acer/"
        make -C "$build_root/acer" -j "$jobs"
        ;;
    help|-h|--help)
        printf '%s\n' 'Uso: ./build.sh {anyrun|provider|kidex|sway|waybar|portal|idle|acer}' \
            'Saída: .build/ (ou BUILD_ROOT). Paralelismo: JOBS, padrão 2.'
        ;;
    *) printf 'Componente desconhecido: %s\n' "$component" >&2; exit 2 ;;
esac

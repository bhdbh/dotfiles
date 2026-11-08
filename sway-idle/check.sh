#!/usr/bin/env bash
set -euo pipefail
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_root=${BUILD_ROOT:-"$root/.build"}
pam_include=/usr/include
if [[ ! -f "$pam_include/security/pam_appl.h" ]]; then
    pam_include="$root/.build/idle-deps/usr/include"
fi
"$root/build.sh" idle
cc -O2 -Wall -Wextra -D_XOPEN_SOURCE=600 -I"$pam_include" \
    "$root/sway-idle/tests/vt-transition.c" -lutil -o "$build_root/idle/vt-transition-test"
"$build_root/idle/vt-transition-test"
drm_include=/usr/include
if [[ ! -f "$drm_include/xf86drmMode.h" ]]; then
    drm_include="$root/sway-window-background/.build-deps/usr/include"
fi
cc -O2 -Wall -Wextra -I"$pam_include" -I"$drm_include" -I"$drm_include/libdrm" \
    "$root/sway-idle/tests/display-power.c" -o "$build_root/idle/display-power-test"
"$build_root/idle/display-power-test"
cc -O2 -Wall -Wextra -Wno-unused-parameter -D_XOPEN_SOURCE=600 \
    -I"$root/sway-idle/source" -I"$pam_include" \
    "$root/sway-idle/source/main.c" "$root/sway-idle/source/options.c" \
    "$root/sway-idle/source/util.c" "$root/sway-idle/tests/locker-stubs.c" \
    -Wl,--wrap=geteuid,--wrap=sleep,--wrap=setsid -o "$build_root/idle/locker-flow-test"
python3 -B "$root/sway-idle/tests/locker-flow.py" "$build_root/idle/locker-flow-test"
python3 -B "$root/sway-idle/tests/output-recovery.py"

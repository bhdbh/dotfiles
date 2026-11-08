#!/usr/bin/bash
set -euo pipefail

setup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if (( EUID != 0 )); then
    exec sudo -- "$0" "$@"
fi

kernel_release=$(uname -r)
source_dir="$setup_dir/acer-wmi-battery"
test "$(cat /sys/class/dmi/id/sys_vendor)" = Acer
compgen -G '/sys/bus/wmi/devices/79772EC5-04B1-4BFD-843C-61E7F77B6CC9-*' > /dev/null
test -f "$source_dir/acer-wmi-battery.c"

printf '%s\n' 'Installing the power profile service and Acer battery driver build tools.'
headers_package="$setup_dir/packages/kernel-devel-$kernel_release.rpm"
matched_package="$setup_dir/packages/kernel-devel-matched-$kernel_release.rpm"
if [[ -f "$headers_package" && -f "$matched_package" ]]; then
    dnf install --assumeyes --setopt=install_weak_deps=False power-profiles-daemon "$headers_package" "$matched_package" dkms gcc make
elif [[ -f "$headers_package" ]]; then
    dnf install --assumeyes --setopt=install_weak_deps=False power-profiles-daemon "$headers_package" "kernel-devel-matched-$kernel_release" dkms gcc make
else
    dnf install --assumeyes --setopt=install_weak_deps=False power-profiles-daemon "kernel-devel-$kernel_release" "kernel-devel-matched-$kernel_release" dkms gcc make
fi

driver_version="0.1.$(git -c safe.directory="$source_dir" -C "$source_dir" rev-parse --short HEAD)"
dkms_source="/usr/src/acer-wmi-battery-$driver_version"
install -d -m 0755 "$dkms_source"
install -m 0644 "$source_dir/acer-wmi-battery.c" "$dkms_source/acer-wmi-battery.c"
install -m 0644 "$source_dir/Makefile" "$dkms_source/Makefile"
cat > "$dkms_source/dkms.conf" <<EOF
PACKAGE_NAME="acer-wmi-battery"
PACKAGE_VERSION="$driver_version"
BUILT_MODULE_NAME[0]="acer-wmi-battery"
DEST_MODULE_LOCATION[0]="/extra"
AUTOINSTALL="yes"
MAKE[0]="make -C /lib/modules/\${kernelver}/build M=\${dkms_tree}/\${PACKAGE_NAME}/\${PACKAGE_VERSION}/build modules"
EOF

if ! dkms status -m acer-wmi-battery -v "$driver_version" | rg -q installed; then
    dkms add -m acer-wmi-battery -v "$driver_version" || test -d "/var/lib/dkms/acer-wmi-battery/$driver_version"
    dkms build -m acer-wmi-battery -v "$driver_version" -k "$kernel_release"
    dkms install -m acer-wmi-battery -v "$driver_version" -k "$kernel_release"
fi
modprobe acer-wmi-battery
test -r /sys/bus/wmi/drivers/acer-wmi-battery/health_mode
case "$(cat /sys/bus/wmi/drivers/acer-wmi-battery/health_mode)" in
    0|1) ;;
    *) printf '%s\n' 'The Acer firmware did not expose a supported charge-limit mode.' >&2; exit 1 ;;
esac

printf '%s\n' acer-wmi-battery > /etc/modules-load.d/waybar-acer-charge.conf
/usr/bin/bash "$setup_dir/setup-charge-persistence.sh"
systemctl enable --now power-profiles-daemon.service
printf '%s\n' 'Ready: click Battery to change the energy plan.'

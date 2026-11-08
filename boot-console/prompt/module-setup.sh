#!/usr/bin/bash
# Customize presentation only in the generated initramfs.
check() {
    require_binaries /usr/bin/python3 || return 1
    return 0
}

depends() {
    echo systemd-cryptsetup
}

install() {
    [[ -n ${disk_password_uuid:-} ]] || dfatal 'disk_password_uuid is not configured'
    /usr/bin/python3 -I "$moddir/customize.py" \
        --binary "$initdir/usr/bin/systemd-cryptsetup" \
        --crypttab "$initdir/etc/crypttab" --uuid "$disk_password_uuid" || return 1
    inst_simple "$moddir/10-disk-password.conf" \
        /etc/systemd/system/systemd-ask-password-console.service.d/10-disk-password.conf
}

"""Use Login: on virtual consoles, without the distribution/kernel banner."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CORE = runpy.run_path(str(ROOT / 'configure.py'))
FILES = runpy.run_path(str(ROOT / 'install-prompt.py'))
SECTIONS = runpy.run_path(str(ROOT / 'prompt/customize.py'))['sections']
BINARY = Path('/usr/bin/agetty')
NATIVE_LOGIN = Path('/usr/bin/login')
LOGIN_VENDOR_SHA256 = '5df1fbcc6199b605d71481a0b54797409b48ca93d0ba0d08ef11f23144c12822'
LOGIN_CUSTOM_SHA256 = 'eb63cca714599dfcede8e7e11868327e2fee728574c0a03dd2bdf6bbf814f6a1'
DROPIN = Path('/etc/systemd/system/getty@.service.d/10-login-prompt.conf')
BACKUPS = Path('/var/backups/console-login')
CONFIG = br'''[Service]
Environment=LC_ALL=C
ExecStart=
ExecStart=-/usr/sbin/agetty --noreset --noclear --noissue --nohostname --login-options "-H -- \\u" - ${TERM}
'''


def customize_binary(data):
    section = SECTIONS(data).get('.rodata')
    if not section or section['flags'] & 5:
        raise RuntimeError('Expected a non-executable, read-only string section.')
    original, replacement = b'login: \0', b'Login: \0'
    # Permit reinstallation after the banner configuration or package changes.
    if data.count(original) == 0 and data.count(replacement) == 1:
        original = replacement
    if data.count(original) != 1:
        raise RuntimeError('The agetty prompt changed; refusing to modify it.')
    offset = data.index(original)
    if not (section['offset'] <= offset and
            offset + len(original) <= section['offset'] + section['size']):
        raise RuntimeError('The login prompt is not entirely inside .rodata.')
    # Only the first letter changes. All executable code remains byte-identical.
    return data[:offset] + replacement + data[offset + len(original):]


def reload_getty():
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True)


def customize_login_binary(data):
    digest = hashlib.sha256(data).hexdigest()
    if digest == LOGIN_CUSTOM_SHA256:
        return data
    if digest != LOGIN_VENDOR_SHA256:
        raise RuntimeError('The login executable changed; review its strings before installing.')
    section = SECTIONS(data).get('.rodata')
    if not section or section['flags'] & 5:
        raise RuntimeError('Expected non-executable, read-only login string data.')
    patched = bytearray(data)
    for original, replacement in (
            (b'login: \0', b'Login: \0'),
            (b'Login incorrect\n\n\0', b'\n\0'),
            (b'Password incorrect\n\n\0', b'\n\0'),
            (b'\nLogin incorrect\n\0', b'\n\0')):
        if data.count(original) != 1:
            raise RuntimeError('The login message layout changed.')
        offset = data.index(original)
        if not (section['offset'] <= offset and
                offset + len(original) <= section['offset'] + section['size']):
            raise RuntimeError('A login message is outside .rodata.')
        # A newline keeps gettext from treating an empty msgid as catalog metadata.
        patched[offset:offset + len(original)] = replacement.ljust(len(original), b'\0')
    if hashlib.sha256(patched).hexdigest() != LOGIN_CUSTOM_SHA256:
        raise RuntimeError('The login presentation differs from the reviewed result.')
    return bytes(patched)


def apply():
    if os.geteuid() != 0:
        raise RuntimeError('Run with pkexec or sudo.')
    if any(path.is_symlink() for path in (BINARY, NATIVE_LOGIN, DROPIN)):
        raise RuntimeError('Unexpected target symlink.')
    payloads = {BINARY: customize_binary(BINARY.read_bytes()),
                NATIVE_LOGIN: customize_login_binary(NATIVE_LOGIN.read_bytes())}
    if (all(path.read_bytes() == data for path, data in payloads.items()) and
            DROPIN.exists() and DROPIN.read_bytes() == CONFIG):
        reload_getty()
        print('Console login customization is already installed.')
        return
    stamp = datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%dT%H%M%S%f%z')
    backup = BACKUPS / stamp
    backup.mkdir(parents=True, mode=0o700)
    BACKUPS.chmod(0o700)
    files = {str(path): FILES['save'](path, backup) for path in (*payloads, DROPIN)}
    record = dict(state='prepared', files=files)
    manifest = backup / 'manifest.json'
    manifest.write_text(json.dumps(record, indent=2) + '\n')
    try:
        for path, data in payloads.items():
            CORE['replace'](path, data, FILES['details'](path))
        DROPIN.parent.mkdir(parents=True, exist_ok=True)
        CORE['replace'](DROPIN, CONFIG, dict(uid=0, gid=0, mode=0o644))
        subprocess.run([str(BINARY), '--version'], check=True)
        subprocess.run([str(NATIVE_LOGIN), '--version'], check=True)
        subprocess.run(['/usr/bin/systemd-analyze', 'verify', 'getty@tty1.service'],
                       check=True)
        reload_getty()
        command = CORE['run']('/usr/bin/systemctl', 'show', 'getty@tty1.service',
                              '--property=ExecStart', '--value')
        if '--noissue --nohostname' not in command or '-H --' not in command:
            raise RuntimeError('The getty service did not pick up the configuration.')
        if (any(path.read_bytes() != data for path, data in payloads.items()) or
                DROPIN.read_bytes() != CONFIG):
            raise RuntimeError('The installed files differ from the prepared files.')
        record.update(state='installed',
                      after={name: CORE['digest'](Path(name)) for name in files})
        manifest.write_text(json.dumps(record, indent=2) + '\n')
    except BaseException:
        FILES['restore_files'](backup, files)
        reload_getty()
        record['state'] = 'rolled-back'
        manifest.write_text(json.dumps(record, indent=2) + '\n')
        raise
    print('Installed: Login: on every attempt, with silent authentication failures.')
    print('System backup:', backup)
    print('The next console login will use the prompt. The active session was preserved.')


def restore(backup):
    if os.geteuid() != 0:
        raise RuntimeError('Run restore with pkexec or sudo.')
    backup = backup.resolve()
    if backup.parent != BACKUPS:
        raise RuntimeError('Expected a console-login backup.')
    record = json.loads((backup / 'manifest.json').read_text())
    if set(record['files']) not in ({str(BINARY), str(DROPIN)},
                                    {str(BINARY), str(NATIVE_LOGIN), str(DROPIN)}):
        raise RuntimeError('Unexpected restore targets.')
    for name, metadata in record['files'].items():
        path = Path(name)
        if path.is_symlink() or CORE['digest'](path) != record.get('after', {}).get(name):
            raise RuntimeError('An installed file changed; review before restoring: ' + name)
        if metadata['existed'] and CORE['digest'](backup / path.relative_to('/')) != metadata['before']:
            raise RuntimeError('Backup hash mismatch: ' + name)
    FILES['restore_files'](backup, record['files'])
    reload_getty()
    print('Previous console login restored. The active session was preserved.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--restore', type=Path)
    mode.add_argument('--with-locker', action='store_true')
    args = parser.parse_args()
    if args.restore:
        restore(args.restore)
    else:
        if args.with_locker:
            customize_binary(BINARY.read_bytes())
            customize_login_binary(NATIVE_LOGIN.read_bytes())
            runpy.run_path(str(ROOT.parent / 'sway-idle/install-vt-recovery.py'))['main']()
        apply()

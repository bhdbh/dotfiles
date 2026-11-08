"""Use the native console disk-password prompt and disable boot animation."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
from zoneinfo import ZoneInfo

BOOT_ARGS = ('quiet', 'rd.plymouth=0', 'plymouth.enable=0', 'systemd.show_status=error')
PRESENTATION_KEYS = {'rhgb', *(item.split('=', 1)[0] for item in BOOT_ARGS)}
BACKUP_ROOT = Path('/var/backups/boot-console')


def run(*args):
    return subprocess.check_output(args, text=True, timeout=120)


def tokens(value):
    # The installed entries use plain arguments. Refuse shell expansion or
    # quoted values rather than risk changing their meaning during rewriting.
    if any(char in value for char in '\"\'`$\\\n\r'):
        raise RuntimeError('Unsupported quoting or expansion in boot arguments.')
    return value.split()


def protected_args(value):
    return [item for item in tokens(value) if item.split('=', 1)[0] not in PRESENTATION_KEYS]


def updated_args(value):
    return ' '.join([*protected_args(value), *BOOT_ARGS])


def field(text, name):
    values = re.findall(r'^' + re.escape(name) + r'[ \t]+(.+)$', text, re.M)
    if len(values) != 1:
        raise RuntimeError(f'Expected one {name} field in boot entry.')
    return values[0].strip()


def expanded_options(text, environment):
    value = field(text, 'options')
    if '$kernelopts' in value:
        if not environment.get('kernelopts'):
            raise RuntimeError('Boot entry references missing kernelopts.')
        value = value.replace('$kernelopts', environment['kernelopts'])
    tokens(value)
    return value


def updated_grub(text):
    pattern = r'^GRUB_CMDLINE_LINUX=(.*)$'
    values = re.findall(pattern, text, re.M)
    if len(values) != 1:
        raise RuntimeError('Expected one GRUB_CMDLINE_LINUX assignment.')
    parsed = shlex.split(values[0])
    if len(parsed) != 1:
        raise RuntimeError('Expected a quoted GRUB_CMDLINE_LINUX value.')
    value = updated_args(parsed[0])
    return re.sub(pattern, lambda match: 'GRUB_CMDLINE_LINUX="' + value + '"', text, flags=re.M)


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def replace(path, data, metadata):
    descriptor, temporary = tempfile.mkstemp(prefix='.boot-console-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
            os.fchown(output.fileno(), metadata['uid'], metadata['gid'])
            os.fchmod(output.fileno(), metadata['mode'])
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        subprocess.run(['/usr/sbin/restorecon', str(path)], check=True)
    finally:
        Path(temporary).unlink(missing_ok=True)


def inspect():
    if os.geteuid() != 0:
        raise RuntimeError('Run with pkexec or sudo to inspect and update boot entries.')
    entries = sorted(Path('/boot/loader/entries').glob('*.conf'))
    if not entries:
        raise RuntimeError('No Boot Loader Specification entries found.')
    environment = dict(line.split('=', 1) for line in
                       run('/usr/bin/grub2-editenv', '/boot/grub2/grubenv', 'list').splitlines()
                       if '=' in line)
    selected, images, plan = [], set(), {}
    for path in entries:
        text = path.read_text()
        kernel = field(text, 'linux')
        if 'rescue' in kernel:
            continue
        options = expanded_options(text, environment)
        if not any(item.startswith('rd.luks.uuid=') for item in tokens(options)):
            raise RuntimeError(f'No expected encrypted-root argument in {path.name}.')
        kernel_path = Path('/boot') / kernel.removeprefix('/boot/').lstrip('/')
        if not kernel_path.is_file():
            raise RuntimeError(f'Missing kernel: {kernel_path}')
        for image in field(text, 'initrd').split():
            image_path = Path('/boot') / image.removeprefix('/boot/').lstrip('/')
            if not image_path.is_file():
                raise RuntimeError(f'Missing initramfs: {image_path}')
            images.add(image_path)
        selected.append((path, kernel_path, options))
        plan[str(path)] = dict(before=options, after=updated_args(options))
    if not selected:
        raise RuntimeError('No regular kernel entries found.')
    # Check the existing initramfs instead of rebuilding it: its native password
    # agent must already be installed and activated without Plymouth.
    for image in sorted(images):
        listing = run('/usr/bin/lsinitrd', str(image))
        for required in ('usr/bin/systemd-tty-ask-password-agent',
                         'systemd/system/systemd-ask-password-console.service',
                         'systemd/system/systemd-ask-password-console.path',
                         'sysinit.target.wants/systemd-ask-password-console.path'):
            if required not in listing:
                raise RuntimeError(f'Console password agent missing from {image.name}: {required}')
        unit = run('/usr/bin/lsinitrd', '-f',
                   'usr/lib/systemd/system/plymouth-start.service', str(image))
        if 'ConditionKernelCommandLine=!plymouth.enable=0' not in unit:
            raise RuntimeError(f'Cannot verify Plymouth disable condition in {image.name}.')
    return entries, selected, images, plan


def apply():
    entries, selected, images, plan = inspect()
    defaults = Path('/etc/default/grub')
    cmdline = Path('/etc/kernel/cmdline')
    updates = {defaults: updated_grub(defaults.read_text()).encode(),
               cmdline: (updated_args(cmdline.read_text().strip()) + '\n').encode()}
    paths = [*entries, defaults, cmdline, Path('/boot/grub2/grubenv'), Path('/boot/grub2/grub.cfg')]
    original, metadata = {}, {}
    for path in paths:
        if path.is_symlink():
            raise RuntimeError(f'Unexpected symlink: {path}')
        details = path.stat()
        original[path] = path.read_bytes()
        metadata[str(path)] = dict(uid=details.st_uid, gid=details.st_gid,
                                   mode=stat.S_IMODE(details.st_mode), before=digest(path))
    image_hashes = {str(path): digest(path) for path in images}
    print('Prepared boot changes:', json.dumps(plan, indent=2), flush=True)
    stamp = datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%dT%H%M%S%f%z')
    backup = BACKUP_ROOT / stamp
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(BACKUP_ROOT, 0o700)
    for path, content in original.items():
        saved = backup / path.relative_to('/')
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(content)
        saved.chmod(0o600)
    manifest = dict(files=metadata, plan=plan, initramfs_sha256=image_hashes, state='prepared')
    record = backup / 'manifest.json'
    record.write_text(json.dumps(manifest, indent=2) + '\n')
    try:
        for path, kernel, before in selected:
            # This grubby release shifts twice for --no-etc-grub-update; place
            # that flag last so it does not consume the following option.
            subprocess.run(['/usr/bin/grubby',
                            '--update-kernel', str(kernel), '--remove-args', 'rhgb',
                            '--args', ' '.join(BOOT_ARGS), '--no-etc-grub-update'],
                           check=True, timeout=30)
            after = path.read_text()
            options = field(after, 'options')
            if protected_args(before) != protected_args(options):
                raise RuntimeError('Non-presentation boot arguments changed: ' + str(path))
            if tokens(options) != tokens(updated_args(before)):
                raise RuntimeError('Unexpected boot arguments: ' + str(path))
            before_other = re.sub(r'^options[ \t]+.*$', '', original[path].decode(), flags=re.M)
            after_other = re.sub(r'^options[ \t]+.*$', '', after, flags=re.M)
            if before_other != after_other:
                raise RuntimeError('Unexpected boot-entry change: ' + str(path))
        for path, data in updates.items():
            replace(path, data, metadata[str(path)])
        selected_paths = {item[0] for item in selected}
        for path in paths:
            if path not in selected_paths and path not in updates and path.read_bytes() != original[path]:
                raise RuntimeError('Unexpected change outside selected entries: ' + str(path))
        for path, expected in image_hashes.items():
            if digest(Path(path)) != expected:
                raise RuntimeError('An initramfs image changed unexpectedly: ' + path)
        subprocess.run(['/usr/bin/grub2-script-check', '/boot/grub2/grub.cfg'], check=True, timeout=30)
        manifest['state'] = 'installed'
        manifest['after'] = {str(path): digest(path) for path in paths}
        record.write_text(json.dumps(manifest, indent=2) + '\n')
    except BaseException:
        for path, content in original.items():
            replace(path, content, metadata[str(path)])
        manifest['state'] = 'rolled-back'
        record.write_text(json.dumps(manifest, indent=2) + '\n')
        raise
    print('Console disk-password prompt configured for the next boot.')
    print('No boot animation. Normal progress hidden; errors remain visible.')
    print('System backup:', backup)
    print('No reboot or disk-encryption change was performed.')


def restore(backup):
    if os.geteuid() != 0:
        raise RuntimeError('Run restore with pkexec or sudo.')
    backup = backup.resolve()
    if backup.parent != BACKUP_ROOT or backup.is_symlink():
        raise RuntimeError('Expected a backup directly under ' + str(BACKUP_ROOT))
    manifest = json.loads((backup / 'manifest.json').read_text())
    for name, metadata in manifest['files'].items():
        path = Path(name)
        if (path not in {Path('/etc/default/grub'), Path('/etc/kernel/cmdline'),
                         Path('/boot/grub2/grubenv'), Path('/boot/grub2/grub.cfg')} and
                not (path.parent == Path('/boot/loader/entries') and path.suffix == '.conf')):
            raise RuntimeError('Unexpected target in backup: ' + name)
        saved = backup / path.relative_to('/')
        if digest(saved) != metadata['before']:
            raise RuntimeError('Backup hash mismatch: ' + name)
        if manifest.get('after', {}).get(name) != digest(path):
            raise RuntimeError('Boot files changed since this backup; review before restoring: ' + name)
    for name, metadata in manifest['files'].items():
        path = Path(name)
        replace(path, (backup / path.relative_to('/')).read_bytes(), metadata)
    print('Previous boot presentation restored. No reboot was triggered.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', type=Path)
    arguments = parser.parse_args()
    if arguments.restore:
        restore(arguments.restore)
    else:
        apply()

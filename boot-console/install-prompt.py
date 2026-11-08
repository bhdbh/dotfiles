"""Install a short, silent disk prompt in the regular kernel initramfs images."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
BACKUPS = Path('/var/backups/boot-console-prompt')
MODULE = Path('/usr/lib/dracut/modules.d/95disk-password')
CONFIG = Path('/etc/dracut.conf.d/95-disk-password.conf')
CORE = runpy.run_path(str(ROOT / 'configure.py'))
CUSTOM = runpy.run_path(str(ROOT / 'prompt/customize.py'))


def details(path):
    value = path.stat()
    return dict(uid=value.st_uid, gid=value.st_gid, mode=stat.S_IMODE(value.st_mode))


def save(path, backup):
    if path.is_symlink():
        raise RuntimeError('Unexpected target symlink: ' + str(path))
    if not path.exists():
        return dict(existed=False)
    saved = backup / path.relative_to('/')
    saved.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, saved)
    saved.chmod(0o600)
    return dict(existed=True, before=CORE['digest'](path), **details(path))


def restore_files(backup, files):
    for name, metadata in files.items():
        path = Path(name)
        if metadata['existed']:
            CORE['replace'](path, (backup / path.relative_to('/')).read_bytes(), metadata)
        else:
            path.unlink(missing_ok=True)


def validate_image(image, uuid, vendor):
    binary = subprocess.check_output(['/usr/bin/lsinitrd', '-f',
                                      'usr/bin/systemd-cryptsetup', str(image)])
    expected = CUSTOM['customize_binary'](vendor)
    layout, expected_layout = CUSTOM['sections'](binary), CUSTOM['sections'](expected)
    for name, section in expected_layout.items():
        if name == '.rodata' or section['flags'] & 4:
            actual = layout.get(name)
            if not actual:
                raise RuntimeError('Required ELF section missing: ' + name)
            a = binary[actual['offset']:actual['offset'] + actual['size']]
            b = expected[section['offset']:section['offset'] + section['size']]
            if a != b:
                raise RuntimeError('Unexpected executable code or prompt data in ' + image.name)
    crypttab = CORE['run']('/usr/bin/lsinitrd', '-f', 'etc/crypttab', str(image))
    if CUSTOM['customize_crypttab'](crypttab, uuid) != crypttab:
        raise RuntimeError('The embedded root password echo is not disabled.')
    dropin = CORE['run']('/usr/bin/lsinitrd', '-f',
                        'etc/systemd/system/systemd-ask-password-console.service.d/10-disk-password.conf',
                        str(image))
    if dropin != (ROOT / 'prompt/10-disk-password.conf').read_text():
        raise RuntimeError('The console presentation override was not embedded.')


def apply():
    if os.geteuid() != 0:
        raise RuntimeError('Run with pkexec or sudo.')
    matches = re.findall(r'\brd.luks.uuid=(?:luks-)?([0-9a-fA-F-]{36})\b',
                         Path('/proc/cmdline').read_text())
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one encrypted root volume.')
    uuid = matches[0]
    entries, selected, images, unused = CORE['inspect']()
    versions = {}
    for entry, kernel, options in selected:
        names = CORE['field'](entry.read_text(), 'initrd').split()
        if len(names) != 1:
            raise RuntimeError('Expected one initramfs per regular kernel.')
        image = Path('/boot') / names[0].removeprefix('/boot/').lstrip('/')
        versions[image] = kernel.name.removeprefix('vmlinuz-')
    vendor = Path('/usr/bin/systemd-cryptsetup').read_bytes()
    CUSTOM['customize_binary'](vendor)
    CUSTOM['customize_crypttab'](Path('/etc/crypttab').read_text(), uuid)
    targets = {MODULE / name: ((ROOT / 'prompt' / name).read_bytes(),
                              0o755 if name.endswith('.sh') else 0o644)
               for name in ('module-setup.sh', 'customize.py', '10-disk-password.conf')}
    targets[CONFIG] = (('add_dracutmodules+=" disk-password "\n'
                       'disk_password_uuid="' + uuid + '"\n').encode(), 0o644)
    protected = [*entries, Path('/etc/default/grub'), Path('/etc/kernel/cmdline'),
                 Path('/boot/grub2/grubenv'), Path('/boot/grub2/grub.cfg'),
                 Path('/etc/crypttab'), Path('/usr/bin/systemd-cryptsetup')]
    protected_hashes = {str(path): CORE['digest'](path) for path in protected}
    stamp = datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%dT%H%M%S%f%z')
    backup = BACKUPS / stamp
    backup.mkdir(parents=True, mode=0o700)
    BACKUPS.chmod(0o700)
    files = {str(path): save(path, backup) for path in [*targets, *sorted(images)]}
    record = dict(state='prepared', uuid=uuid, files=files,
                  protected_sha256=protected_hashes, regular_kernels=list(versions.values()))
    manifest = backup / 'manifest.json'
    manifest.write_text(json.dumps(record, indent=2) + '\n')
    print('System backup:', backup, flush=True)
    try:
        for path, (data, mode) in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            CORE['replace'](path, data, dict(uid=0, gid=0, mode=mode))
        staged = {}
        for image, version in versions.items():
            stage = backup / ('new-initramfs-' + version + '.img')
            log = backup / ('build-' + version + '.log')
            print('Building and validating initramfs for', version, flush=True)
            with log.open('wb') as output:
                subprocess.run(['/usr/bin/dracut', '--force', '--kver', version, str(stage)],
                               stdout=output, stderr=subprocess.STDOUT, check=True, timeout=600)
            validate_image(stage, uuid, vendor)
            staged[image] = stage
        for path, expected in protected_hashes.items():
            if CORE['digest'](Path(path)) != expected:
                raise RuntimeError('A protected system file changed: ' + path)
        # Publish only after every image has passed verification.
        for image, stage in staged.items():
            CORE['replace'](image, stage.read_bytes(), files[str(image)])
        record['state'] = 'installed'
        record['after'] = {name: CORE['digest'](Path(name)) for name in files}
        manifest.write_text(json.dumps(record, indent=2) + '\n')
        for stage in staged.values():
            stage.unlink()
    except BaseException:
        restore_files(backup, files)
        record['state'] = 'rolled-back'
        manifest.write_text(json.dumps(record, indent=2) + '\n')
        print('Installation failed; previous files restored. Build logs:', backup, flush=True)
        raise
    print('Installed: Disk Password: with completely hidden input and no Tab hint.')
    print('The next boot will use the updated prompt. No reboot was triggered.')


def restore(backup):
    if os.geteuid() != 0:
        raise RuntimeError('Run restore with pkexec or sudo.')
    backup = backup.resolve()
    if backup.parent != BACKUPS:
        raise RuntimeError('Expected a boot-console-prompt backup.')
    record = json.loads((backup / 'manifest.json').read_text())
    for name, metadata in record['files'].items():
        path = Path(name)
        if path.parent != MODULE and path != CONFIG and not (
                path.parent == Path('/boot') and path.name.startswith('initramfs-') and
                path.suffix == '.img' and 'rescue' not in path.name):
            raise RuntimeError('Unexpected restore target: ' + name)
        if path.is_symlink() or CORE['digest'](path) != record.get('after', {}).get(name):
            raise RuntimeError('An installed file changed; review before restoring: ' + name)
        if metadata['existed'] and CORE['digest'](backup / path.relative_to('/')) != metadata['before']:
            raise RuntimeError('Backup hash mismatch: ' + name)
    restore_files(backup, record['files'])
    print('Previous prompt restored. No reboot was triggered.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', type=Path)
    args = parser.parse_args()
    if args.restore:
        restore(args.restore)
    else:
        apply()

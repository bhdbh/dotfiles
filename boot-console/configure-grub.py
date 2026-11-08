"""Configure immediate GRUB boot and remove only the reviewed previous kernel."""
from datetime import datetime
import itertools
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CORE = runpy.run_path(str(ROOT / 'configure.py'))
CURRENT = '7.2.15-200.fc44.x86_64'
OLD = '7.2.14-200.fc44.x86_64'
NAMES = ('kernel', 'kernel-core', 'kernel-devel', 'kernel-modules',
         'kernel-modules-core', 'kernel-modules-extra')
PACKAGES = {name + '-' + OLD for name in NAMES}
DEFAULTS = Path('/etc/default/grub')
CONFIG = Path('/boot/grub2/grub.cfg')
ENV = Path('/boot/grub2/grubenv')
DNF = ['/usr/bin/dnf5', '--assumeyes', '--disable-repo=*',
       '--setopt=clean_requirements_on_remove=False']


def run(*args):
    return subprocess.check_output(args, text=True, timeout=180)


def update_defaults(text):
    for key, value in (('GRUB_TIMEOUT', '0'), ('GRUB_TIMEOUT_STYLE', 'menu')):
        pattern = r'^' + key + r'=.*$'
        matches = re.findall(pattern, text, re.M)
        if len(matches) > 1:
            raise RuntimeError('Ambiguous GRUB assignment: ' + key)
        if matches:
            text = re.sub(pattern, key + '=' + value, text, flags=re.M)
        else:
            text = text.rstrip('\n') + '\n' + key + '=' + value + '\n'
    return text


def section(config, name):
    pattern = (r'^### BEGIN /etc/grub.d/' + re.escape(name) + r' ###\n'
               r'(.*?)^### END /etc/grub.d/' + re.escape(name) + r' ###$')
    found = re.findall(pattern, config, re.M | re.S)
    if len(found) != 1:
        raise RuntimeError('Missing or ambiguous GRUB section: ' + name)
    return found[0]


def check_timeouts(config):
    # Evaluate the generated timeout policy, translating GRUB's `set name=value`
    # into shell assignments and stubbing save_env. No boot code,
    # device access, or host environment-file writes are executed by the check.
    header = section(config, '00_header')
    marker = 'if [ x$feature_timeout_style = xy ] ; then\n'
    if header.count(marker) != 1:
        raise RuntimeError('Unexpected generated timeout policy.')
    policy = marker + header.split(marker, 1)[1]
    policy += ''.join(section(config, name) for name in
                      ('10_reset_boot_success', '12_menu_auto_hide', '14_menu_show_once'))
    policy = re.sub(r'(?m)^([ \t]*)set ([A-Za-z_][A-Za-z_0-9]*)=', r'\1\2=', policy)
    for feature, success, indeterminate, hide in itertools.product(
            ('', 'y'), ('0', '1'), ('0', '1'), ('', '1')):
        script = ('save_env() { :; }\n'
                  'feature_timeout_style=$1; boot_success=$2; boot_indeterminate=$3; '
                  'menu_auto_hide=$4; fastboot=1; env_block=; menu_show_once=; '
                  'menu_show_once_timeout=;\n' + policy + '\nprintf "%s" "$timeout"\n')
        value = run('/usr/bin/bash', '--noprofile', '--norc', '-c', script,
                    'grub-timeout-check', feature, success, indeterminate, hide)
        if value != '0':
            raise RuntimeError('GRUB would wait in a tested boot state: ' + value)
    print('Verified zero timeout in all 16 normal/failed-boot policy states.', flush=True)


def installed():
    return set(run('/usr/bin/rpm', '-qa', '--qf',
                   '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n').splitlines())


def verify_protected(hashes):
    for name, expected in hashes.items():
        if CORE['digest'](Path(name)) != expected:
            raise RuntimeError('Protected boot/prompt file changed: ' + name)


def apply():
    if os.geteuid() != 0:
        raise RuntimeError('Run with pkexec or sudo.')
    if os.uname().release != CURRENT:
        raise RuntimeError('The running kernel changed; review the removal plan again.')
    if run('/usr/bin/grubby', '--default-kernel').strip() != '/boot/vmlinuz-' + CURRENT:
        raise RuntimeError('The current kernel is not the selected default.')
    entries = sorted(Path('/boot/loader/entries').glob('*.conf'))
    kept_entries = [path for path in entries if path.name.endswith('-' + CURRENT + '.conf')]
    old_entries = [path for path in entries if path.name.endswith('-' + OLD + '.conf')]
    if len(kept_entries) != 1 or len(old_entries) != 1:
        raise RuntimeError('Expected exactly one current and one old kernel entry.')
    kernel = Path('/boot/vmlinuz-' + CURRENT)
    image = Path('/boot/initramfs-' + CURRENT + '.img')
    protected = [kernel, image, *[p for p in entries if p not in old_entries],
                 Path('/etc/crypttab'), Path('/etc/kernel/cmdline'),
                 Path('/usr/bin/systemd-cryptsetup'),
                 Path('/etc/dracut.conf.d/95-disk-password.conf')]
    protected += sorted(Path('/usr/lib/dracut/modules.d/95disk-password').glob('*'))
    protected += sorted(Path('/boot').glob('*-0-rescue-*'))
    hashes = {str(path): CORE['digest'](path) for path in protected if path.is_file()}
    before_packages = installed()
    expected_removed = {name + '-0:' + OLD for name in NAMES}
    if not expected_removed <= before_packages:
        raise RuntimeError('The old-kernel package set changed; review before removing.')
    stamp = datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%dT%H%M%S%f%z')
    backup = Path('/var/backups/boot-console-grub') / stamp
    backup.mkdir(parents=True, mode=0o700)
    backup.parent.chmod(0o700)
    original, metadata = {}, {}
    for path in (DEFAULTS, CONFIG, ENV, *entries):
        if path.is_symlink():
            raise RuntimeError('Unexpected symlink: ' + str(path))
        details = path.stat()
        metadata[str(path)] = dict(uid=details.st_uid, gid=details.st_gid,
                                   mode=stat.S_IMODE(details.st_mode))
        original[path] = path.read_bytes()
        saved = backup / path.relative_to('/')
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(original[path])
        saved.chmod(0o600)
    record = dict(state='prepared', current=CURRENT, remove=sorted(PACKAGES),
                  protected_sha256=hashes, metadata=metadata)
    manifest = backup / 'manifest.json'

    def save_record(state):
        record['state'] = state
        manifest.write_text(json.dumps(record, indent=2) + '\n')

    save_record('prepared')
    print('System backup:', backup, flush=True)
    # Store and inspect the resolver result before changing packages or GRUB.
    transaction_dir = backup / 'remove-old-kernel'
    subprocess.run([*DNF, 'remove', '--store=' + str(transaction_dir),
                    *sorted(PACKAGES)], check=True, timeout=180)
    transaction = json.loads((transaction_dir / 'transaction.json').read_text())
    rpms = transaction.get('rpms', [])
    # DNF omits a zero epoch in its stored NEVRA strings; RPM's query above
    # includes it. Accept either representation of these exact packages.
    planned = {item.get('nevra', '').replace('-0:', '-') for item in rpms}
    if (len(rpms) != len(expected_removed) or planned != PACKAGES or
            any(item.get('action') not in ('Remove', 'Removed') for item in rpms) or
            transaction.get('groups') or transaction.get('environments')):
        print(json.dumps(transaction, indent=2), flush=True)
        raise RuntimeError('Refusing an unexpected package transaction.')
    try:
        CORE['replace'](DEFAULTS, update_defaults(DEFAULTS.read_text()).encode(),
                        metadata[str(DEFAULTS)])
        staged = backup / 'new-grub.cfg'
        subprocess.run(['/usr/bin/grub2-mkconfig', '--no-grubenv-update', '-o', str(staged)],
                       check=True, timeout=180)
        subprocess.run(['/usr/bin/grub2-script-check', str(staged)], check=True, timeout=30)
        check_timeouts(staged.read_text())
        verify_protected(hashes)
        CORE['replace'](CONFIG, staged.read_bytes(), metadata[str(CONFIG)])
        subprocess.run(['/usr/bin/grub2-editenv', str(ENV), 'set',
                        'fastboot=1', 'menu_auto_hide=1'], check=True, timeout=30)
        subprocess.run(['/usr/bin/grub2-editenv', str(ENV), 'unset',
                        'menu_show_once', 'menu_show_once_timeout'], check=True, timeout=30)
        save_record('grub-installed')
    except BaseException:
        for path in (DEFAULTS, CONFIG, ENV):
            CORE['replace'](path, original[path], metadata[str(path)])
        save_record('grub-rolled-back')
        raise
    try:
        subprocess.run([*DNF, 'replay', str(transaction_dir)], check=True, timeout=600)
        verify_protected(hashes)
        after_packages = installed()
        if before_packages - after_packages != expected_removed or after_packages - before_packages:
            raise RuntimeError('Unexpected installed-package changes; inspect the DNF log.')
        remaining = sorted(Path('/boot/loader/entries').glob('*.conf'))
        regular = [p for p in remaining if '-0-rescue.' not in p.name]
        if regular != kept_entries or any(path.exists() for path in old_entries):
            raise RuntimeError('The regular boot-entry set differs from the expected current kernel.')
        if run('/usr/bin/grubby', '--default-kernel').strip() != str(kernel):
            raise RuntimeError('The selected default kernel changed unexpectedly.')
        subprocess.run(['/usr/bin/grub2-script-check', str(CONFIG)], check=True, timeout=30)
        check_timeouts(CONFIG.read_text())
        environment = dict(line.split('=', 1) for line in
                           run('/usr/bin/grub2-editenv', str(ENV), 'list').splitlines() if '=' in line)
        if environment.get('fastboot') != '1' or environment.get('menu_auto_hide') != '1':
            raise RuntimeError('Immediate-boot environment settings changed unexpectedly.')
        record['after'] = {str(p): CORE['digest'](p) for p in (DEFAULTS, CONFIG, ENV)}
        save_record('installed')
    except BaseException:
        # Package removal is not undone by restoring stale BLS files. The current
        # kernel and the already-validated immediate-boot configuration stay usable.
        save_record('kernel-removal-needs-review')
        raise
    print('Installed: immediate GRUB boot; kept running kernel ' + CURRENT + '.', flush=True)
    print('Removed only the six packages of ' + OLD + '; rescue entry preserved.', flush=True)
    print('Current kernel, initramfs, disk prompt and encryption configuration unchanged.', flush=True)
    print('No reboot was triggered.', flush=True)


if __name__ == '__main__':
    apply()

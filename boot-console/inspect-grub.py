"""Read the installed boot policy and preview removal of the old kernel."""
from pathlib import Path
import os
import subprocess


if os.geteuid() != 0:
    raise SystemExit('Administrator authentication is required.')

for name in ('/etc/default/grub', '/boot/grub2/grub.cfg',
             '/etc/grub.d/00_header', '/etc/grub.d/08_fallback_counting',
             '/etc/grub.d/10_reset_boot_success', '/etc/grub.d/12_menu_auto_hide',
             '/etc/grub.d/14_menu_show_once', '/etc/grub.d/40_custom',
             '/etc/grub.d/41_custom', '/boot/grub2/custom.cfg'):
    path = Path(name)
    if path.exists():
        print('\nFILE:', name, flush=True)
        print(path.read_text(), flush=True)
for path in sorted(Path('/boot/loader/entries').glob('*.conf')):
    print('\nENTRY:', path.name, flush=True)
    print(path.read_text(), flush=True)
for command in (
    ['/usr/bin/grubby', '--info=ALL'],
    ['/usr/bin/grubby', '--default-kernel'],
    ['/usr/bin/grub2-editenv', '/boot/grub2/grubenv', 'list'],
    ['/usr/bin/dnf5', '--assumeno', '--disable-repo=*',
     '--setopt=clean_requirements_on_remove=False', 'remove',
     'kernel-7.2.14-200.fc44.x86_64', 'kernel-core-7.2.14-200.fc44.x86_64',
     'kernel-devel-7.2.14-200.fc44.x86_64', 'kernel-modules-7.2.14-200.fc44.x86_64',
     'kernel-modules-core-7.2.14-200.fc44.x86_64',
     'kernel-modules-extra-7.2.14-200.fc44.x86_64'],
):
    print('\nCOMMAND:', ' '.join(command), flush=True)
    result = subprocess.run(command, check=False)
    print('EXIT:', result.returncode, flush=True)

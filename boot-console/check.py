"""Validate boot argument preservation using disposable BLS entries."""
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

settings = runpy.run_path(str(Path(__file__).with_name('configure.py')))
BASE = ('root=/dev/mapper/fedora-root ro rd.lvm.lv=fedora/root '
        'rd.luks.uuid=luks-00000000-0000-4000-8000-000000000001 rhgb quiet')


class BootConsoleTests(unittest.TestCase):
    def test_storage_arguments_and_order_are_preserved(self):
        before = BASE + ' rd.luks.options=discard resume=UUID=abc loglevel=3'
        after = settings['updated_args'](before)
        self.assertEqual(settings['protected_args'](before), settings['protected_args'](after))
        self.assertIn('rd.luks.options=discard resume=UUID=abc loglevel=3', after)
        self.assertNotIn('rhgb', after.split())
        self.assertTrue(all(item in after.split() for item in settings['BOOT_ARGS']))

    def test_repeated_application_replaces_old_values(self):
        before = BASE + ' rd.plymouth=1 plymouth.enable=1 systemd.show_status=auto'
        after = settings['updated_args'](before)
        self.assertEqual(after, settings['updated_args'](after))
        self.assertEqual(after.split().count('quiet'), 1)
        self.assertNotIn('systemd.show_status=auto', after)

    def test_grub_defaults_only_changes_the_argument_assignment(self):
        before = ('GRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR="$(sed /etc/system-release)"\n'
                  'GRUB_CMDLINE_LINUX="' + BASE + '"\nGRUB_ENABLE_BLSCFG=true\n')
        after = settings['updated_grub'](before)
        self.assertEqual(before.splitlines()[:2], after.splitlines()[:2])
        self.assertEqual(before.splitlines()[3:], after.splitlines()[3:])
        self.assertIn('rd.luks.uuid=luks-00000000', after)
        self.assertIn('plymouth.enable=0', after)

    def test_ambiguous_arguments_are_rejected(self):
        for value in ('root=x $unknown', 'root="a b"', 'root=x\ninit=/bin/sh'):
            with self.assertRaises(RuntimeError):
                settings['updated_args'](value)

    def test_duplicate_grub_assignment_is_rejected(self):
        with self.assertRaises(RuntimeError):
            settings['updated_grub']('GRUB_CMDLINE_LINUX="quiet"\n' * 2)

    def test_installed_grubby_updates_only_selected_fixture(self):
        # Per-kernel updates with custom BLS/env/config paths do not update
        # /etc/kernel/cmdline or /etc/default/grub. All writes stay in /tmp.
        with tempfile.TemporaryDirectory(prefix='boot-console-check-') as temporary:
            base = Path(temporary)
            entries = base / 'entries'
            entries.mkdir()
            env = base / 'grubenv'
            cfg = base / 'grub.cfg'
            cfg.write_text('# Disposable GRUB configuration\n')
            regular = entries / 'fixture-2.conf'
            rescue = entries / 'fixture-0-rescue.conf'
            template = ('title Test\nversion 2\nlinux /vmlinuz-test\n'
                        'initrd /initramfs-test.img\noptions ' + BASE + '\n')
            regular.write_text(template)
            rescue.write_text(template.replace('vmlinuz-test', 'vmlinuz-0-rescue'))
            unchanged = rescue.read_bytes()
            subprocess.run(['/usr/bin/grub2-editenv', str(env), 'create'], check=True)
            env_before = env.read_bytes()
            subprocess.run(['/usr/bin/grubby',
                            '--bls-directory', str(entries), '--env', str(env),
                            '--config-file', str(cfg), '--update-kernel', '/boot/vmlinuz-test',
                            '--remove-args', 'rhgb', '--args', ' '.join(settings['BOOT_ARGS']),
                            '--no-etc-grub-update'],
                           check=True, timeout=30)
            after = regular.read_text()
            self.assertEqual(settings['field'](after, 'options'), settings['updated_args'](BASE))
            self.assertEqual(rescue.read_bytes(), unchanged)
            self.assertEqual(env.read_bytes(), env_before)
            self.assertEqual(cfg.read_text(), '# Disposable GRUB configuration\n')


if __name__ == '__main__':
    unittest.main()

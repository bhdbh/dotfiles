"""Check prompt formatting, unchanged executable code and real native no-echo input."""
import ctypes
import os
from pathlib import Path
import pty
import runpy
import select
import signal
import shutil
import subprocess
import tempfile
import termios
import time
import unittest

custom = runpy.run_path(str(Path(__file__).parent / 'prompt/customize.py'))
UUID = '00000000-0000-4000-8000-000000000001'


class PromptTests(unittest.TestCase):
    def test_real_binary_changes_only_two_readonly_string_slots(self):
        original = Path('/usr/bin/systemd-cryptsetup').read_bytes()
        patched = custom['customize_binary'](original)
        allowed = set()
        for old in custom['ORIGINALS']:
            start = original.index(old)
            allowed.update(range(start, start + len(old)))
        changed = {index for index, pair in enumerate(zip(original, patched)) if pair[0] != pair[1]}
        self.assertTrue(changed)
        self.assertLessEqual(changed, allowed)
        self.assertEqual(len(original), len(patched))
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'systemd-cryptsetup'
            binary.write_bytes(patched)
            binary.chmod(0o755)
            subprocess.run([str(binary), '--help'], stdout=subprocess.DEVNULL, check=True)
            subprocess.run([str(binary), '--version'], stdout=subprocess.DEVNULL, check=True)

    def test_printf_discards_both_old_prompt_arguments(self):
        libc = ctypes.CDLL(None)
        output = ctypes.create_string_buffer(256)
        libc.snprintf(output, len(output), custom['PROMPT'],
                      ctypes.c_char_p(b'passphrase'), ctypes.c_char_p(b'old disk identifier'))
        self.assertEqual(output.value, b'Disk Password:')

    def test_unknown_vendor_format_is_rejected(self):
        original = Path('/usr/bin/systemd-cryptsetup').read_bytes()
        altered = original.replace(custom['ORIGINALS'][0], b'X' * len(custom['ORIGINALS'][0]))
        with self.assertRaisesRegex(RuntimeError, 'vendor prompt format changed'):
            custom['customize_binary'](altered)

    def test_crypttab_preserves_other_devices_and_storage_options(self):
        other = 'external UUID=other none discard\n'
        root = 'luks-' + UUID + ' UUID=' + UUID + ' none discard,x-initrd.attach,password-echo=masked\n'
        before = '# Original configuration\n' + root + other
        after = custom['customize_crypttab'](before, UUID)
        self.assertTrue(after.startswith('# Original configuration\n'))
        self.assertTrue(after.endswith(other))
        self.assertIn('discard,x-initrd.attach,password-echo=no', after)
        self.assertEqual(after, custom['customize_crypttab'](after, UUID))
        self.assertEqual(root.split()[:3], after.splitlines()[1].split()[:3])

    def test_missing_or_ambiguous_root_entry_is_rejected(self):
        root = 'root UUID=' + UUID + ' none\n'
        for value in ('external UUID=other none\n', root * 2,
                      'root UUID=' + UUID + ' /secret/key\n'):
            with self.assertRaises(RuntimeError):
                custom['customize_crypttab'](value, UUID)

    def test_replacing_hardlink_does_not_modify_vendor_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            vendor, copy = Path(temporary) / 'vendor', Path(temporary) / 'copy'
            vendor.write_bytes(b'original')
            os.link(vendor, copy)
            custom['replace'](copy, b'customized')
            self.assertEqual(vendor.read_bytes(), b'original')
            self.assertEqual(copy.read_bytes(), b'customized')

    def test_dracut_module_customizes_a_disposable_image_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            initdir = Path(temporary)
            binary = initdir / 'usr/bin/systemd-cryptsetup'
            binary.parent.mkdir(parents=True)
            shutil.copyfile('/usr/bin/systemd-cryptsetup', binary)
            crypttab = initdir / 'etc/crypttab'
            crypttab.parent.mkdir(parents=True)
            crypttab.write_text('luks-' + UUID + ' UUID=' + UUID + ' none discard\n')
            script = '''set -e
moddir=$1
initdir=$2
disk_password_uuid=$3
inst_simple() { /usr/bin/install -Dm644 "$1" "$initdir$2"; }
dfatal() { return 1; }
source "$moddir/module-setup.sh"
install
'''
            subprocess.run(['/usr/bin/bash', '-c', script, 'module-check',
                            str(Path(__file__).resolve().parent / 'prompt'), temporary, UUID],
                           check=True, stdout=subprocess.DEVNULL)
            self.assertIn(custom['PROMPT'], binary.read_bytes())
            self.assertIn('password-echo=no', crypttab.read_text())
            dropin = initdir / 'etc/systemd/system/systemd-ask-password-console.service.d/10-disk-password.conf'
            self.assertIn('SYSTEMD_EMOJI=0', dropin.read_text())

    def test_native_tty_hides_characters_and_tab_hint(self):
        # Use a dummy value on a disposable PTY, never a real disk password.
        read_pass, write_pass = os.pipe()
        pid, master = pty.fork()
        if pid == 0:
            os.close(read_pass)
            os.dup2(write_pass, 1)
            os.close(write_pass)
            os.execve('/usr/bin/systemd-ask-password',
                      ['systemd-ask-password', '--echo=no', '--emoji=no', '-n',
                       '--timeout=5', 'Disk Password:'],
                      dict(os.environ, SYSTEMD_COLORS='0', SYSTEMD_EMOJI='0'))
        os.close(write_pass)
        observed = b''
        try:
            deadline = time.monotonic() + 4
            while b'Disk Password: ' not in observed or termios.tcgetattr(master)[3] & termios.ECHO:
                self.assertLess(time.monotonic(), deadline)
                if select.select([master], [], [], 0.05)[0]:
                    observed += os.read(master, 4096)
            os.write(master, b'dummy-input-123')
            if select.select([master], [], [], 0.1)[0]:
                observed += os.read(master, 4096)
            self.assertEqual(observed, b'Disk Password: ')
            os.write(master, b'\n')
            self.assertTrue(select.select([read_pass], [], [], 2)[0])
            self.assertEqual(os.read(read_pass, 4096), b'dummy-input-123')
            _, status = os.waitpid(pid, 0)
            pid = 0
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        finally:
            if pid:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(master)
            os.close(read_pass)


if __name__ == '__main__':
    unittest.main()

"""Run agetty and native login on PTYs with test-only PAM that always rejects."""
import errno
import os
from pathlib import Path
import pty
import runpy
import select
import shlex
import signal
import subprocess
import tempfile
import time

if os.getuid() == 0 or os.geteuid() == 0:
    raise SystemExit('Run this test as the regular user; it never needs root.')

ROOT = Path(__file__).resolve().parent
LOGIN = runpy.run_path(str(ROOT / 'login-prompt.py'))
original = LOGIN['NATIVE_LOGIN'].read_bytes()
patched = LOGIN['customize_login_binary'](original)
assert len(original) == len(patched)
assert LOGIN['customize_login_binary'](patched) == patched
for section in LOGIN['SECTIONS'](original).values():
    if section['flags'] & 4:
        lo, hi = section['offset'], section['offset'] + section['size']
        assert original[lo:hi] == patched[lo:hi], 'Executable code changed.'

with tempfile.TemporaryDirectory(prefix='native-login-retry-test-') as temporary:
    base = Path(temporary)
    agetty, login, stub = (base / name for name in ('agetty', 'login', 'pam-stub.so'))
    agetty.write_bytes(LOGIN['customize_binary'](LOGIN['BINARY'].read_bytes()))
    login.write_bytes(patched)
    agetty.chmod(0o755)
    login.chmod(0o755)
    includes = Path('/usr/include')
    if not (includes / 'security/pam_appl.h').exists():
        includes = ROOT.parent / '.build/idle-deps/usr/include'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-O2', '-fPIC', '-shared',
                    '-I' + str(includes), str(ROOT / 'tests/login-pam-stub.c'),
                    '-o', str(stub)], check=True)
    options = shlex.split(LOGIN['CONFIG'].decode().split('ExecStart=-', 1)[1])
    for mode, attempts in (('wrong-password-and-user', 3), ('maxtries', 1)):
        events = base / (mode + '.events')
        env = dict(os.environ, LC_ALL='C', LD_PRELOAD=str(stub),
                   TEST_LOGIN_EVENTS=str(events))
        env.pop('CREDENTIALS_DIRECTORY', None)
        env.pop('TEST_LOGIN_MAXTRIES', None)
        if mode == 'maxtries':
            env['TEST_LOGIN_MAXTRIES'] = '1'
        pid, terminal = pty.fork()
        if pid == 0:
            os.execve(str(agetty), [str(agetty), *options[1:-2],
                                   '--login-program', str(login), '-', 'linux'], env)
        output = bytearray()
        reaped = False

        def read_chunk(deadline):
            remaining = deadline - time.monotonic()
            assert remaining > 0 and select.select([terminal], [], [], remaining)[0], repr(output)
            try:
                chunk = os.read(terminal, 4096)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                chunk = b''
            output.extend(chunk)
            return bool(chunk)

        def read_until(marker, start):
            deadline = time.monotonic() + 8
            while marker not in output[start:]:
                if not read_chunk(deadline):
                    observed = events.read_text() if events.exists() else '<no PAM events>'
                    raise AssertionError((repr(output), observed))
            return output.index(marker, start) + len(marker)

        try:
            cursor = read_until(b'Login: ', 0)
            assert bytes(output[:cursor]).strip(b'\r\n') == b'Login: ', repr(output)
            for attempt in range(attempts):
                os.write(terminal, b'console-test\n')
                cursor = read_until(b'Password: ', cursor)
                os.write(terminal, b'dummy-rejected-password\n')
                if attempt + 1 < attempts:
                    end = read_until(b'Login: ', cursor)
                    assert bytes(output[cursor:end]).strip(b'\r\n') == b'Login: ', repr(output)
                    cursor = end
            deadline = time.monotonic() + 8
            while read_chunk(deadline):
                pass
            assert bytes(output[cursor:]).strip(b'\r\n') == b'', repr(output)
            _, status = os.waitpid(pid, 0)
            reaped = True
            assert os.waitstatus_to_exitcode(status) == 0, (status, repr(output))
            assert output.count(b'Login: ') == attempts, repr(output)
            assert output.count(b'Password: ') == attempts, repr(output)
            assert b'login: ' not in output and b'incorrect' not in output.lower(), repr(output)
            assert b'dummy-rejected-password' not in output, 'Password was echoed.'
            observed = events.read_text().splitlines()
            assert observed == ['start', *(['rejected'] * attempts), 'end'], observed
        finally:
            if not reaped:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
            os.close(terminal)

print('Native login verified: repeated Login: prompts, hidden passwords, and silent rejections/retry exhaustion.')
print('Only test PAM was used; no real authentication or session was opened.')

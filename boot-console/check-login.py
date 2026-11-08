"""Check the initial agetty prompt/arguments, then native login retries on PTYs."""
import errno
import json
import os
from pathlib import Path
import pty
import runpy
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
LOGIN = runpy.run_path(str(ROOT / 'login-prompt.py'))
vendor = LOGIN['BINARY'].read_bytes()
patched = LOGIN['customize_binary'](vendor)
assert len(patched) == len(vendor)
assert sum(a != b for a, b in zip(vendor, patched)) in (0, 1)
assert LOGIN['customize_binary'](patched) == patched

with tempfile.TemporaryDirectory(prefix='console-login-test-') as temporary:
    base = Path(temporary)
    binary = base / 'agetty'
    binary.write_bytes(patched)
    binary.chmod(0o755)
    fake_login = base / 'login'
    fake_login.write_text('#!/usr/bin/python3\nimport json, sys\n'
                          'print("LOGIN_ARGS=" + json.dumps(sys.argv[1:]), flush=True)\n')
    fake_login.chmod(0o755)
    pid, terminal = pty.fork()
    if pid == 0:
        options = shlex.split(LOGIN['CONFIG'].decode().split('ExecStart=-', 1)[1])
        os.execve(str(binary), [str(binary), *options[1:-2],
                               '--login-program', str(fake_login), '-', 'linux'],
                  dict(os.environ, LC_ALL='C'))
    output = bytearray()

    def read_until(marker, start=0):
        deadline = time.monotonic() + 8
        while marker not in output[start:]:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([terminal], [], [], remaining)[0]:
                raise AssertionError('Timed out: ' + repr(output))
            try:
                chunk = os.read(terminal, 4096)
            except OSError as error:
                if error.errno == errno.EIO:
                    chunk = b''
                else:
                    raise
            assert chunk, repr(output)
            output.extend(chunk)

    try:
        read_until(b'Login: ')
        assert bytes(output).strip(b'\r\n') == b'Login: ', repr(output)
        os.write(terminal, b'console-test\n')
        read_until(b'LOGIN_ARGS=')
        read_until(b'\n', output.index(b'LOGIN_ARGS='))
        arguments = json.loads(output.split(b'LOGIN_ARGS=', 1)[1].strip())
        assert arguments == ['-H', '--', 'console-test'], arguments
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        os.close(terminal)

print('Login: prompt, absent banner/hostname, and native login arguments verified on a PTY.', flush=True)
print('No real console or authentication was accessed.', flush=True)
subprocess.run([sys.executable, '-B', str(ROOT / 'check-login-retries.py')], check=True)

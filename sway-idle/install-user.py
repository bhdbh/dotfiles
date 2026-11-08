"""Install the supervisor and source mirror, preserving user-file backups."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
USER_HOME = Path.home()
STAMP = datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%dT%H%M%S%z')
BACKUP = USER_HOME / '.local/state/sway-idle/backups' / STAMP


def replace(source, target, mode):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        saved = BACKUP / target.relative_to(USER_HOME)
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, saved)
    descriptor, temporary = tempfile.mkstemp(prefix='.idle-update-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(source.read_bytes())
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    if os.geteuid() == 0:
        raise SystemExit('Run this user installation as your regular account.')
    source = ROOT / 'sway-idle'
    legacy = USER_HOME / '.config/sway/idle'
    # Check session access before changing files or restarting the supervisor.
    subprocess.run(['swaymsg', '-r', '-t', 'get_version'], check=True,
                   stdout=subprocess.DEVNULL, timeout=3)
    target = USER_HOME / '.local/bin/sway-session-idle'
    replace(source / 'sway-session-idle', target, 0o755)
    for name in ('main.c', 'vt.c', 'display.c', 'physlock.h', 'Makefile'):
        replace(source / 'source' / name, legacy / 'source' / name, 0o644)
    for name in ('README.md', 'multi-output.patch', 'idle-powerdown.patch', 'drm-powerdown.patch'):
        replace(source / name, legacy / name, 0o644)
    replace(ROOT / '.build/idle/physlock', legacy / 'sway-tty-lock', 0o750)
    # Stop only the previous supervisor for this exact Sway socket. Its SIGTERM
    # handler also stops its swayidle child and releases the sleep inhibitor.
    identity = hashlib.sha256(os.environ['SWAYSOCK'].encode()).hexdigest()[:12]
    runtime = Path(os.environ['XDG_RUNTIME_DIR']) / ('sway-idle-' + identity)
    status = runtime / 'status.json'
    if status.exists():
        pid = json.loads(status.read_text()).get('pid')
        if pid:
            try:
                arguments = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
                environment = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
                if (str(target).encode() in arguments and
                        b'SWAYSOCK=' + os.environ['SWAYSOCK'].encode() in environment):
                    descriptor = os.pidfd_open(pid)
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                        if not select.select([descriptor], [], [], 5)[0]:
                            raise RuntimeError('The previous idle supervisor did not stop.')
                    finally:
                        os.close(descriptor)
            except (ProcessLookupError, FileNotFoundError):
                pass
    subprocess.run(['swaymsg', '-r', 'exec ' + str(target)], check=True, timeout=3)
    print('User update installed. Backup:', BACKUP)


if __name__ == '__main__':
    main()

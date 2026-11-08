"""Install the reviewed DRM power-down build, preserving a root backup."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import grp
import hashlib
import json
import os
import stat
import subprocess
import tempfile

EXPECTED = "4460c1117535b3f186a95083ebe350ced65596dd40182fa29e468bfa91de4ff8"
PREVIOUS = "7b912973960c22ddc9219c708e92787dafbb4ff49b228718a9d9aa50d2c9dcf2"
ROOT = Path(__file__).resolve().parent.parent
TARGET = Path("/usr/local/bin/sway-tty-lock")


def replace_binary(data, mode, uid, gid):
    descriptor, temporary = tempfile.mkstemp(prefix=".sway-tty-lock-", dir=TARGET.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fchown(output.fileno(), uid, gid)
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        os.replace(temporary, TARGET)
        subprocess.run(["/usr/sbin/restorecon", str(TARGET)], check=True)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run with pkexec or sudo to replace the system executable.")
    payload = (ROOT / ".build/idle/physlock").read_bytes()
    if hashlib.sha256(payload).hexdigest() != EXPECTED:
        raise SystemExit("The build differs from the reviewed version; installation cancelled.")
    if TARGET.is_symlink():
        raise SystemExit("The target is an unexpected symlink; installation cancelled.")
    previous = TARGET.read_bytes()
    previous_hash = hashlib.sha256(previous).hexdigest()
    if previous_hash == EXPECTED:
        print("The update is already installed.")
        return
    if previous_hash != PREVIOUS:
        raise SystemExit("The installed executable has changed; installation cancelled.")
    processes = subprocess.run(["/usr/bin/pgrep", "-x", "sway-tty-lock"],
                               capture_output=True, text=True)
    if processes.returncode != 1:
        raise SystemExit("A locker is active or the process check failed.")
    details = TARGET.stat()
    if details.st_uid != 0 or stat.S_IMODE(details.st_mode) != 0o4750:
        raise SystemExit("Unexpected target permissions; installation cancelled.")
    timestamp = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%dT%H%M%S%z")
    backup = Path("/var/backups/sway-tty-lock") / timestamp
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    (backup / "sway-tty-lock").write_bytes(previous)
    os.chmod(backup / "sway-tty-lock", 0o600)
    record = dict(target=str(TARGET), before=previous_hash, after=EXPECTED,
                  uid=details.st_uid, gid=details.st_gid, mode=stat.S_IMODE(details.st_mode))
    (backup / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
    try:
        replace_binary(payload, 0o4750, 0, details.st_gid)
        subprocess.run([str(TARGET), "-h"], check=True)
        subprocess.run([str(TARGET), "-v"], check=True)
        assert hashlib.sha256(TARGET.read_bytes()).hexdigest() == EXPECTED
    except BaseException:
        replace_binary(previous, stat.S_IMODE(details.st_mode), details.st_uid, details.st_gid)
        raise
    print("Update installed. No lock or suspend was triggered.")
    print("System backup:", backup)


if __name__ == "__main__":
    main()

"""Run the reviewed installer with the operating system's own authentication."""
from pathlib import Path
import json
import os
import subprocess
import time

root = Path(__file__).resolve().parent.parent
print("TTY locker update: power down idle monitors and wake them for password entry.", flush=True)
print("Enter your administrator password in this terminal when prompted.", flush=True)
print("The installer replaces the reviewed binary and saves a backup.", flush=True)
status = root / '.build/idle/install-status.json'
status.write_text(json.dumps({'state': 'authenticating', 'started': time.time()}) + '\n')
result = subprocess.run(["/usr/bin/pkexec", "/usr/bin/python3", "-I",
                         str(root / "sway-idle/install-vt-recovery.py")],
                        env=dict(os.environ, LC_ALL='C'))
status.write_text(json.dumps({'state': 'finished', 'exit_code': result.returncode,
                             'finished': time.time()}) + '\n')
if result.returncode:
    print("Installation did not complete. The previous executable was preserved.", flush=True)
    input("Press Enter to close this terminal.")
else:
    print("Done. This terminal will close in a few seconds.", flush=True)
    time.sleep(4)

"""Run the GRUB inspection or installer with OS authentication in a terminal."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--inspect', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parent
name = 'grub-inspect' if args.inspect else 'grub-install'
status = root / (name + '-status.json')
print('GRUB: immediate boot and removal of the previous kernel.', flush=True)
print('Enter your administrator password when the system asks.', flush=True)
status.write_text(json.dumps(dict(state='authenticating', started=time.time())) + '\n')
installer = 'inspect-grub.py' if args.inspect else 'configure-grub.py'
with (root / (name + '.log')).open('w') as log:
    process = subprocess.Popen(['/usr/bin/pkexec', '/usr/bin/python3', '-I',
                                str(root / installer)],
                               env=dict(os.environ, LC_ALL='C'),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    for line in process.stdout:
        print(line, end='', flush=True)
        log.write(line)
        log.flush()
    result = process.wait()
status.write_text(json.dumps(dict(state='finished', exit_code=result,
                                 finished=time.time())) + '\n')
if result:
    print('The operation did not complete; the log contains the details.', flush=True)
    input('Press Enter to close this terminal.')
else:
    print('Done. No reboot was triggered.', flush=True)
    time.sleep(4)

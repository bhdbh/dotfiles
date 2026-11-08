"""Authenticate with the OS and apply the prepared console-boot settings."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import time

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
mode = parser.add_mutually_exclusive_group()
mode.add_argument('--prompt', action='store_true')
mode.add_argument('--login', action='store_true')
args = parser.parse_args()
installer = 'login-prompt.py' if args.login else 'install-prompt.py' if args.prompt else 'configure.py'
status = root / ('login-install-status.json' if args.login else
                 'prompt-install-status.json' if args.prompt else 'install-status.json')
print('Quiet password retries and a plain Login: prompt.' if args.login else
      'Short disk password prompt with hidden input.' if args.prompt else
      'Console disk unlock and boot animation settings.', flush=True)
print('Enter your administrator password in this terminal when prompted.', flush=True)
print('The installer saves a system backup before updating the prompts.' if args.login else
      'The installer validates the boot images and saves a system backup.', flush=True)
print('The current session will stay open.' if args.login else
      'It will not reboot the computer or change the disk encryption.', flush=True)
status.write_text(json.dumps(dict(state='authenticating', started=time.time())) + '\n')
with status.with_suffix('.log').open('w') as log:
    process = subprocess.Popen(['/usr/bin/pkexec', '/usr/bin/python3', '-I',
                                str(root / installer), *(['--with-locker'] if args.login else [])],
                               env=dict(os.environ, LC_ALL='C'), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in process.stdout:
        print(line, end='', flush=True)
        log.write(line)
        log.flush()
    result = process.wait()
status.write_text(json.dumps(dict(state='finished', exit_code=result,
                                  finished=time.time())) + '\n')
if result:
    print('Installation did not complete. Review the message above.', flush=True)
    input('Press Enter to close this terminal.')
else:
    print('Done. Updated prompts will be used on the next lock and console login.' if args.login else
          'Done. The console prompt will be used on the next boot.', flush=True)
    time.sleep(4)

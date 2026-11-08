"""Back up and atomically install the reviewed Waybar changes."""
from datetime import datetime
from pathlib import Path
import json
import os
import shutil
from zoneinfo import ZoneInfo

source = Path(__file__).resolve().parent
target = Path.home() / '.config/waybar'
files = ['style.css',
         'scripts/volume-control.c','scripts/volume-control.so',
         'scripts/audio-client.c','scripts/audio-client.h','scripts/pulse-abi.h',
         'scripts/audio-output.py',
         'scripts/hit-areas.c','scripts/hit-areas.so']
backup = target/'backups'/('volume-independent-controls-'+datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%dT%H%M%S%z'))
backup.mkdir(parents=True)
manifest = []
for name in files:
    current = target/name
    saved = backup/name
    if current.exists():
        saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(current,saved)
    manifest.append(dict(path=name,existed=current.exists()))
(backup/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
for name in files:
    current = target/name
    temporary = current.with_name(current.name+'.installing')
    shutil.copy2(source/name,temporary)
    os.replace(temporary,current)
(source/'validation/backup-path').write_text(str(backup)+'\n')
print('Backup:',backup)
print('Installed:',len(files),'files')

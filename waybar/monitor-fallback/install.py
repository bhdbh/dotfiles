"""Back up and install the tested monitor fallback changes."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HOME_DIR = Path.home()
backup = HOME_DIR/'.config/sway/backups'/(
    'waybar-monitor-fallback-' + datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y%m%dT%H%M%S%z'))

helper = HOME_DIR/'.local/bin/sway-primary-workspaces'
bar = HOME_DIR/'.config/waybar/config.jsonc'
sway = HOME_DIR/'.config/sway/config'
assert helper.read_bytes() == (ROOT/'sway-primary-workspaces.before').read_bytes(), 'Helper changed during review'
assert sway.read_bytes() == (ROOT/'sway.before.conf').read_bytes(), 'Sway config changed during review'

config = json.loads(bar.read_text())
for module in ('cffi/optical-spacing', 'cffi/hit-areas'):
    config[module].pop('cursor', None)
files = {
    helper: (ROOT/'sway-primary-workspaces').read_bytes(),
    bar: (json.dumps(config, indent=2, ensure_ascii=False) + '\n').encode(),
    sway: (ROOT/'sway.fixed.conf').read_bytes(),
}
backup.mkdir(parents=True)
manifest = []
for target, content in files.items():
    relative = target.relative_to(HOME_DIR)
    saved = backup/relative
    saved.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, saved)
    manifest.append({'path': str(target), 'before_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                     'after_sha256': hashlib.sha256(content).hexdigest()})
for target, content in files.items():
    temporary = target.with_name(target.name + '.monitor-fallback.tmp')
    temporary.write_bytes(content)
    shutil.copymode(target, temporary)
    temporary.replace(target)
(backup/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
(ROOT/'backup-path').write_text(str(backup) + '\n')
print('Backup:', backup)
print('Installed:', len(files), 'files')

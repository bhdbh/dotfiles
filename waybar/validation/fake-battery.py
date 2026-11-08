import json
from pathlib import Path
import sys

path = Path(__file__).with_name('battery-state.json')
state = json.loads(path.read_text())
if sys.argv[1] == 'toggle-charge':
    state['charge'] += 1
    state['full'] = not state['full']
elif sys.argv[1] == 'toggle-profile':
    state['profile'] += 1
path.write_text(json.dumps(state))
print(f"Battery:\t80%\t{int(state['plugged'])}\t{int(state['full'])}\tNot charging\t80")

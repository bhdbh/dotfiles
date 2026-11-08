#!/usr/bin/python3
"""Check startup cleanup of oversized histories without loading UI or plugins."""

from collections import Counter
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

provider = Path(sys.argv[1])


def entry(name, kind):
    plugin, icon = {"app": ("Applications", "application-x-executable"),
                    "shell": ("Shell", None), "directory": ("Kidex", "folder"),
                    "file": ("Kidex", "text-x-generic"), "action": ("Actions", None)}[kind]
    return {"plugin": {"name": plugin, "icon": "system-run"},
            "selection": {"title": name, "description": "/tmp/" + name if plugin == "Kidex" else None,
                          "icon": icon, "id": None, "use_pango": False}, "query": name}


with tempfile.TemporaryDirectory(prefix="anyrun-history-limit-") as temporary:
    root = Path(temporary)
    state = root / "state/anyrun"
    state.mkdir(parents=True)
    recent = [entry(f"action-{number}", "action") for number in range(10)]
    calculations = [{"expression": f"{number}+1", "result": str(number + 1)} for number in range(10)]
    usage = [{"entry": entry(f"{kind}-{number}", kind), "count": number + 1, "last_used": bucket * 10 + number}
             for bucket, kind in enumerate(("app", "shell", "directory", "file")) for number in range(10)]
    for filename, data in (("history.json", recent), ("calculations.json", calculations), ("usage.json", usage)):
        (state / filename).write_text(json.dumps(data))
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(root / "provider.sock"))
        listener.listen()
        listener.settimeout(10)
        process = subprocess.Popen([str(provider), "--config-dir", str(root), "connect-to", str(root / "provider.sock")],
                                   env=dict(os.environ, XDG_STATE_HOME=str(root / "state")), stderr=subprocess.PIPE)
        try:
            connection, _ = listener.accept()
            with connection, connection.makefile("rwb") as stream:
                assert json.loads(stream.readline()) == {"Ready": {"info": []}}
                stream.write(b'"Quit"\n')
                stream.flush()
                process.wait(timeout=10)
                assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            errors = process.stderr.read().decode()
            process.stderr.close()
            if process.returncode:
                print(errors, file=sys.stderr)
    assert json.loads((state / "history.json").read_text()) == recent[:6]
    assert json.loads((state / "calculations.json").read_text()) == calculations[:6]
    retained = json.loads((state / "usage.json").read_text())
    assert len(retained) == 24
    assert Counter(item["entry"]["query"].rsplit("-", 1)[0] for item in retained) == {"app": 6, "shell": 6, "directory": 6, "file": 6}
    assert all(int(item["entry"]["query"].rsplit("-", 1)[1]) >= 4 for item in retained)

print("PASS: excedentes apagados dos arquivos ao iniciar; seis registros por histórico/prefixo.")

#!/usr/bin/python3
"""Launch a disposable VS Code profile, preserve logs, and close only its window."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

root = Path(tempfile.mkdtemp(prefix="probe-", dir=Path(__file__).resolve().parent))
profile = root / "profile"
(profile / "User").mkdir(parents=True)
(profile / "User/settings.json").write_text(json.dumps({
    "update.mode": "none",
    "telemetry.telemetryLevel": "off",
    "workbench.startupEditor": "none",
    "extensions.autoCheckUpdates": False,
    "extensions.autoUpdate": False,
    "security.workspace.trust.enabled": False,
}))
command = ["/usr/share/code/code", "--user-data-dir=" + str(profile),
           "--extensions-dir=" + str(root / "extensions"),
           "--disable-extensions", "--skip-welcome", "--skip-release-notes",
           "--new-window", "--verbose"] + sys.argv[1:]
environment = dict(os.environ, WAYLAND_DEBUG="client")
environment.pop("ELECTRON_RUN_AS_NODE", None)
environment.pop("VSCODE_IPC_HOOK_CLI", None)
started = time.monotonic()
with (root / "stdio.log").open("w") as log:
    process = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
    print(json.dumps({"artifacts": str(root), "pid": process.pid, "command": command}), flush=True)
    survived = False
    windows = []
    try:
        deadline = started + 10
        while time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.2)
        survived = process.poll() is None
        tree = json.loads(subprocess.check_output(["swaymsg", "-r", "-t", "get_tree"], text=True))
        (root / "tree.json").write_text(json.dumps(tree, indent=2))
        def walk(node):
            if node.get("pid") == process.pid:
                windows.append({key: node.get(key) for key in ("id", "name", "app_id", "rect", "focused")})
            for child in node.get("nodes", []) + node.get("floating_nodes", []):
                walk(child)
        walk(tree)
    finally:
        if process.poll() is None:
            subprocess.run(["swaymsg", "-q", "[pid=" + str(process.pid) + "] kill"], check=True)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
    result = {"pid": process.pid, "survived_10_seconds": survived, "windows": windows,
              "returncode": process.returncode, "elapsed_seconds": round(time.monotonic() - started, 2)}
    (root / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)

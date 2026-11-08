#!/usr/bin/python3
"""Read-only check of a mounted drive's index and the real Anyrun provider."""

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

home = Path.home()
root = home / "Drives" / (sys.argv[1] if len(sys.argv) > 1 else "Files")
assert root.is_symlink() and root.is_dir(), f"Drive link unavailable: {root}"
with socket.socket(socket.AF_UNIX) as connection:
    connection.settimeout(15)
    connection.connect("/tmp/kidex.sock")
    connection.sendall(b'{"GetIndex":null}\0')
    chunks = []
    while chunk := connection.recv(65536):
        chunks.append(chunk)
    entries = json.loads(b"".join(chunks))["Index"]
drive = {e["path"]: e["directory"] for e in entries if Path(e["path"]).is_relative_to(root)}
expected = {str(root): True}
for directory, dirs, files in os.walk(root, followlinks=False):
    dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(directory) / d).is_symlink()]
    expected.update({str(Path(directory) / d): True for d in dirs})
    expected.update({str(Path(directory) / f): False for f in files if not f.startswith(".") and not (Path(directory) / f).is_symlink()})
assert drive.items() <= expected.items(), "Indexed drive paths differ from filesystem contents (ignored paths may be absent)"
assert len(entries) == len({e["path"] for e in entries}), "Duplicate paths in the index"
assert not any(Path(e["path"]).is_relative_to(root.resolve()) for e in entries), "Raw mount paths duplicated in the index"
file_path = next(p for p, directory in sorted(drive.items(), key=lambda item: len(item[0]), reverse=True) if not directory)
directory_path = next(p for p, directory in sorted(drive.items(), key=lambda item: len(item[0]), reverse=True) if directory and p != str(root))

with tempfile.TemporaryDirectory(prefix="anyrun-drive-live-") as temporary:
    test_root = Path(temporary)
    config = test_root / "config"
    config.mkdir()
    for name in ("actions.ron", "shell.ron", "rink.ron", "kidex.ron", "applications.ron"):
        source = home / ".config/anyrun" / name
        if source.exists():
            (config / name).write_text(source.read_text().replace("pull_currencies: true", "pull_currencies: false"))
    env = dict(os.environ, XDG_STATE_HOME=str(test_root / "state"), XDG_CACHE_HOME=str(test_root / "cache"))
    plugins = re.findall(r'"(lib[^"/]+\.so)"', (home / ".config/anyrun/config.ron").read_text())
    with socket.socket(socket.AF_UNIX) as listener:
        ipc_path = str(test_root / "provider.sock")
        listener.bind(ipc_path)
        listener.listen()
        listener.settimeout(20)
        command = [str(home / ".local/libexec/anyrun-prefix-provider"), "--config-dir", str(config)]
        for plugin in plugins:
            command.extend(["-p", str(home / ".config/anyrun/plugins" / plugin)])
        process = subprocess.Popen(command + ["connect-to", ipc_path], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            connection, _ = listener.accept()
            connection.settimeout(20)
            with connection, connection.makefile("rwb") as stream:
                def send(message):
                    stream.write(json.dumps(message).encode() + b"\n")
                    stream.flush()

                ready = json.loads(stream.readline())["Ready"]["info"]
                info = {plugin["name"]: plugin for plugin in ready}

                def query(text):
                    send({"Query": {"text": text}})
                    results = {}
                    for _ in ready:
                        result = json.loads(stream.readline())["Matches"]
                        assert result["query"] == text
                        results[result["plugin"]["name"]] = result["matches"]
                    return results

                def handle(selection):
                    send({"Handle": {"plugin": info["Kidex"], "selection": selection}})
                    return json.loads(stream.readline())["Handled"]["result"]

                # Wait for the plugin's asynchronous initialization.
                deadline = time.monotonic() + 10
                while not any(item["description"] == str(root) for item in query("# " + str(root))["Kidex"]):
                    assert time.monotonic() < deadline, query("# " + str(root))["Kidex"]
                    time.sleep(0.05)
                if (home / "Projects").is_dir():
                    assert query("#project")["Kidex"][0]["description"] == str(home / "Projects")
                for text, expected_path, directory in ((file_path, file_path, False),
                                                        ("@ " + file_path, file_path, False),
                                                        ("# " + directory_path, directory_path, True)):
                    matches = query(text)["Kidex"]
                    item = next(item for item in matches if item["description"] == expected_path)
                    assert item["icon"] == ("folder" if directory else "text-x-generic")
                item = next(item for item in query("@ " + file_path)["Kidex"] if item["description"] == file_path)
                assert handle(item) == {"Refresh": True}
                menu = query("@ " + file_path)["Kidex"]
                assert [item["title"] for item in menu] == ["Open", "Copy", "Copy path", "Back"]
                assert bytes(handle(next(item for item in menu if item["title"] == "Copy path"))["Copy"]).decode() == file_path
                send("Quit")
                process.wait(timeout=10)
                assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            errors = process.stderr.read().decode()
            process.stderr.close()
            if process.returncode:
                print(errors, file=sys.stderr)

print(json.dumps({"drive": str(root), "files": sum(not d for d in drive.values()),
                  "directories": sum(drive.values()), "index_total": len(entries),
                  "normal_search": True, "file_and_directory_search": True, "copy_path": True}, ensure_ascii=False))

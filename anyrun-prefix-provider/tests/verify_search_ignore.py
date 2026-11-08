#!/usr/bin/python3
"""Exercise real indexing, live events and ignore-file reloads without a UI."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

binary = Path(sys.argv[1])

with tempfile.TemporaryDirectory(prefix="kidex-search-ignore-") as temporary:
    root = Path(temporary)
    watched = root / "home"
    config = root / "config"
    config.mkdir()
    repository = watched / "Projects/app"
    generated = repository / "node_modules/dependency/generated.js"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated")
    (watched / "cache").mkdir()
    (watched / "cache/stale.txt").write_text("cache")
    (watched / "cache.txt").write_text("normal file")
    (repository / "source.rs").write_text("source")
    (repository / "debug.log").write_text("log")
    (repository / "important.log").write_text("kept")
    rules = config / "search.ignore"
    initial = "# exclusions\nnode_modules/\ncache/\nlogs/\n*.log\n!important.log\n"
    rules.write_text(initial)
    (config / "kidex.ron").write_text(
        f'Config(ignore_file: Some({json.dumps(str(rules))}), ignored: [".*"], '
        f'directories: [WatchDir(path: {json.dumps(str(watched))}, recurse: true, ignored: [])])'
    )
    ipc = root / "kidex.sock"
    with (root / "daemon.log").open("w+") as log:
        process = subprocess.Popen([str(binary)], env=dict(os.environ, XDG_CONFIG_HOME=str(config),
                                   SOCKET_PATH=str(ipc), RUST_LOG="error"), stdout=subprocess.DEVNULL, stderr=log)
        try:
            def index():
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.settimeout(5)
                    connection.connect(str(ipc))
                    connection.sendall(b'{"GetIndex":null}\0')
                    chunks = []
                    while chunk := connection.recv(65536):
                        chunks.append(chunk)
                    return {Path(item["path"]) for item in json.loads(b"".join(chunks))["Index"]}

            def wait_for(condition):
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        log.seek(0)
                        raise AssertionError(log.read())
                    try:
                        entries = index()
                        if condition(entries):
                            return entries
                    except (FileNotFoundError, ConnectionRefusedError):
                        pass
                    assert time.monotonic() < deadline, "Indexer did not apply the expected change"
                    time.sleep(0.05)

            entries = wait_for(lambda entries: repository / "source.rs" in entries)
            assert generated not in entries and generated.parent.parent not in entries
            assert watched / "cache" not in entries
            assert repository / "debug.log" not in entries
            assert repository / "important.log" in entries
            assert watched / "cache.txt" in entries
            (watched / "logs/deep").mkdir(parents=True)
            (watched / "logs/deep/new.txt").write_text("ignored")
            (watched / "new.log").write_text("ignored")
            (watched / "visible.txt").write_text("visible")
            entries = wait_for(lambda entries: watched / "visible.txt" in entries)
            assert not any(path.is_relative_to(watched / "logs") for path in entries)
            assert watched / "new.log" not in entries
            rules.write_text(initial + "/Projects/\n")
            entries = wait_for(lambda entries: watched / "Projects" not in entries)
            assert not any(path.is_relative_to(repository) for path in entries)
            rules.unlink()
            entries = wait_for(lambda entries: generated in entries)
            assert watched / "cache/stale.txt" in entries and repository / "debug.log" in entries
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            assert process.returncode == 0

print("PASS: exclusões reais, diretórios podados, exceções, eventos novos e recarga automática de regras.")

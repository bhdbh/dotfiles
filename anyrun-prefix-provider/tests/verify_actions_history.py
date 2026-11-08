#!/usr/bin/env python3
"""Check action history with synthetic commands, including a detached locker."""

import argparse
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", type=Path, required=True)
    parser.add_argument("--actions-plugin", type=Path, required=True)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="anyrun-actions-history-") as temporary:
        root = Path(temporary)
        fixture = root / "fixture.py"
        fixture.write_text('''import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).parent
if sys.argv[1] == "child":
    # Like physlock's error_init(), keep a duplicate of the original stderr
    # even after redirecting the terminal streams.
    saved_stderr = os.dup(2)
    with open(os.devnull, "wb") as null:
        os.dup2(null.fileno(), 1)
        os.dup2(null.fileno(), 2)
    (root / "child-ready").touch()
    deadline = time.monotonic() + 15
    while not (root / "release").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    os.close(saved_stderr)
    (root / "child-done").touch()
elif sys.argv[1] == "fail":
    print("fixture failure details", file=sys.stderr)
    sys.exit(7)
else:
    subprocess.Popen([sys.executable, __file__, "child"], start_new_session=True)
    (root / "command-done").touch()
''')
        command = shlex.join([sys.executable, str(fixture), "start"])
        failure = shlex.join([sys.executable, str(fixture), "fail"])
        (root / "actions.ron").write_text(
            'Config(enable_power_actions: false, custom_actions: [\n'
            f'(title: "Suspend", command: {json.dumps(command)}, '
            'description: "Synthetic detached locker", icon: "system-suspend", confirm: true),\n'
            f'(title: "Failure", command: {json.dumps(failure)}, confirm: false),\n'
            '])\n'
        )
        state = root / "state/anyrun/history.json"
        release = root / "release"
        with socket.socket(socket.AF_UNIX) as listener, tempfile.TemporaryFile() as errors:
            address = str(root / "provider.sock")
            listener.bind(address)
            listener.listen()
            listener.settimeout(10)
            process = subprocess.Popen(
                [str(args.provider.resolve()), "--config-dir", str(root),
                 "-p", str(args.actions_plugin.resolve()), "connect-to", address],
                env=dict(os.environ, XDG_STATE_HOME=str(root / "state")),
                stdout=subprocess.DEVNULL, stderr=errors,
            )
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(3)
                    buffer = b""

                    def receive():
                        nonlocal buffer
                        while b"\n" not in buffer:
                            chunk = connection.recv(65536)
                            assert chunk, "Provider closed its connection"
                            buffer += chunk
                        line, buffer = buffer.split(b"\n", 1)
                        return json.loads(line)

                    def request(value):
                        connection.sendall(json.dumps(value).encode() + b"\n")
                        return receive()

                    info, = receive()["Ready"]["info"]
                    assert info["name"] == "Actions"

                    def query(text):
                        return request({"Query": {"text": text}})["Matches"]["matches"]

                    def handle(selection):
                        return request({"Handle": {"plugin": info, "selection": selection}})["Handled"]["result"]

                    def history():
                        return request("History")["History"]["entries"]

                    def reset():
                        connection.sendall(b'"Reset"\n')
                        # init() runs in a plugin thread; query until it arrives.
                        time.sleep(0.05)

                    deadline = time.monotonic() + 3
                    while not query(":"):
                        assert time.monotonic() < deadline, "Plugin did not initialize"
                        time.sleep(0.01)
                    assert history() == []

                    selected, = query(": Suspend")
                    assert handle(selected) == {"Refresh": True}
                    menu = {item["title"]: item for item in query(": Suspend")}
                    assert handle(menu["Back"]) == {"Refresh": False}
                    assert history() == [] and not (root / "command-done").exists()

                    selected, = query(": Suspend")
                    assert handle(selected) == {"Refresh": True}
                    confirm = next(item for item in query(": Suspend") if item["title"] == "Confirm")
                    try:
                        assert handle(confirm) == "Close"
                    except TimeoutError:
                        assert (root / "command-done").exists(), "Fixture itself failed to return"
                        assert (root / "child-ready").exists(), "Detached fixture did not start"
                        assert not state.exists(), "Unexpected history before the handler completed"
                        raise AssertionError("Action waits for a detached child's stderr; history is delayed until unlock") from None
                    deadline = time.monotonic() + 3
                    while not (root / "child-ready").exists():
                        assert time.monotonic() < deadline, "Detached fixture did not start"
                        time.sleep(0.01)
                    recent = history()
                    assert len(recent) == 1 and recent[0]["selection"] == selected
                    assert recent[0]["query"] == ": Suspend"
                    assert json.loads(state.read_text()) == recent
                    assert not (root / "child-done").exists(), "Detached child ended before history was saved"
                    print("PASS: confirmed Suspend is persisted while the synthetic locker remains active")

                    reset()
                    assert request({"Replay": {"index": 0}})["Handled"]["result"] == {"Refresh": True}
                    menu = {item["title"]: item for item in query(": Suspend")}
                    assert handle(menu["Back"]) == {"Refresh": False}
                    assert history() == recent
                    print("PASS: cancellation is not recorded and replay still requires confirmation")

                    failure, = query(": Failure")
                    assert handle(failure) == {"Refresh": True}
                    error, = query(": Failure")
                    assert error["icon"] == "dialog-error"
                    assert "fixture failure details" in error["description"]
                    assert handle(error) == "Close"
                    assert history() == recent
                    print("PASS: failures retain stderr diagnostics and do not enter history")

                    connection.sendall(b'"Quit"\n')
                    process.wait(timeout=5)
                    assert process.returncode == 0
            finally:
                release.touch()
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                deadline = time.monotonic() + 2
                while (root / "command-done").exists() and not (root / "child-done").exists():
                    assert time.monotonic() < deadline, "Detached fixture did not exit"
                    time.sleep(0.01)
                if process.returncode:
                    errors.seek(0)
                    print(errors.read().decode(), file=sys.stderr)


if __name__ == "__main__":
    main()

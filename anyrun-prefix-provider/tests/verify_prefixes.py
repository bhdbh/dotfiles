#!/usr/bin/env python3
"""Exercise the real provider and plugins over IPC with a controlled Kidex index."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", type=Path, required=True)
    parser.add_argument("--kidex-plugin", type=Path, required=True)
    parser.add_argument("--actions-plugin", type=Path, required=True)
    parser.add_argument("--actions-config", type=Path, required=True)
    parser.add_argument("--shell-config", type=Path, required=True)
    parser.add_argument("--rink-config", type=Path, required=True)
    parser.add_argument("--plugins-dir", type=Path, default=Path.home() / ".config/anyrun/plugins")
    args = parser.parse_args()
    checks = 0

    with tempfile.TemporaryDirectory(prefix="anyrun-prefix-test-") as temporary:
        root = Path(temporary)
        executables = root / "bin"
        executables.mkdir()
        opener = executables / "xdg-open"
        opener.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\nPath(' + repr(str(root / 'opened.json')) + ').write_text(json.dumps(sys.argv[1:]))\n')
        opener.chmod(0o755)
        shutil.copy2(args.actions_config, root / "actions.ron")
        shutil.copy2(args.shell_config, root / "shell.ron")
        for name in ("kidex.ron", "applications.ron"):
            source = args.plugins_dir.parent / name
            if source.exists():
                shutil.copy2(source, root / name)
        (root / "rink.ron").write_text(args.rink_config.read_text().replace("pull_currencies: true", "pull_currencies: false"))
        actions = (root / "actions.ron").read_text()
        fixtures = []
        for number in range(8):
            fixtures.append(f'(title: "History test {number}", command: "touch {root}/action-{number}", confirm: false)')
        fixtures.append(f'(title: "History confirm", command: "touch {root}/confirmed", confirm: true)')
        fixtures.append('(title: "History fail", command: "false", confirm: false)')
        actions = actions.rsplit(']', 1)[0] + ',\n'.join(fixtures) + ',\n],\n)\n'
        (root / "actions.ron").write_text(actions)
        applications = root / "data/applications"
        applications.mkdir(parents=True)
        (applications / "anyrun-history-test.desktop").write_text(
            f'[Desktop Entry]\nType=Application\nName=Anyrun History Test\nExec=touch {root}/application\nIcon=application-x-executable\n'
        )
        for number in range(8):
            (applications / f"anyrun-frequent-{number}.desktop").write_text(
                f'[Desktop Entry]\nType=Application\nName=Anyrun Frequent {number}\nExec=touch {root}/frequent-{number}\nIcon=application-x-executable\n'
            )
        index = []
        # More entries of each type than the plugin's limit catch filtering after truncation.
        for directory in (False, True):
            for number in range(6):
                path = root / (f"projeto-{number}" + ("" if directory else ".txt"))
                path.mkdir() if directory else path.touch()
                index.append({"path": str(path), "directory": directory})
            path = root / ("brave" if directory else "brave.txt")
            path.mkdir() if directory else path.touch()
            index.append({"path": str(path), "directory": directory})

        projects = root / "Projects"
        projects.mkdir()
        nested = root / "Drives/Files/backup/project"
        nested.mkdir(parents=True)
        child = nested / "unrelated"
        child.mkdir()
        for path in (projects, nested, child):
            index.append({"path": str(path), "directory": True})
        kidex = socket.socket(socket.AF_UNIX)
        kidex.bind(str(root / "kidex.sock"))
        kidex.listen()
        kidex.settimeout(0.2)
        stopped = threading.Event()

        def serve_index():
            while not stopped.is_set():
                try:
                    connection, _ = kidex.accept()
                except socket.timeout:
                    continue
                with connection:
                    request = b""
                    while b"\0" not in request:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        request += chunk
                    assert json.loads(request.rstrip(b"\0")) == {"GetIndex": None}
                    connection.sendall(json.dumps({"Index": index}).encode())

        server = threading.Thread(target=serve_index, daemon=True)
        server.start()
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(root / "provider.sock"))
        listener.listen()
        listener.settimeout(10)
        command = [str(args.provider), "--config-dir", str(root)]
        for library in ("applications", "shell", "rink", "actions", "kidex"):
            path = {
                "kidex": args.kidex_plugin,
                "actions": args.actions_plugin,
            }.get(library, args.plugins_dir / f"lib{library}.so")
            command += ["-p", str(path)]
        command += ["connect-to", str(root / "provider.sock")]
        environment = dict(os.environ, SOCKET_PATH=str(root / "kidex.sock"), XDG_STATE_HOME=str(root / "state"), XDG_DATA_HOME=str(root / "data"), PATH=str(executables) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"))
        process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            connection, _ = listener.accept()
            connection.settimeout(10)
            with connection, connection.makefile("rwb") as stream:
                def send(value):
                    stream.write(json.dumps(value).encode() + b"\n")
                    stream.flush()

                def receive():
                    line = stream.readline()
                    assert line, "Provider closed its connection"
                    return json.loads(line)

                ready = receive()["Ready"]["info"]
                assert ready[-1]["name"] == "Actions", "Actions must be the last search group"
                info = {plugin["name"]: plugin for plugin in ready}
                assert set(info) == {"Applications", "Shell", "Rink", "Actions", "Kidex"}, info

                def query(text):
                    send({"Query": {"text": text}})
                    results = {}
                    for _ in ready:
                        response = receive()["Matches"]
                        assert response["query"] == text, "Received results from an older query"
                        name = response["plugin"]["name"]
                        assert name not in results, f"Duplicate response for {text!r}: {name}"
                        results[name] = response["matches"]
                    assert set(results) == set(info)
                    return results

                def history():
                    send("History")
                    return receive()["History"]["entries"]

                def handle(plugin, selection):
                    send({"Handle": {"plugin": info[plugin], "selection": selection}})
                    return receive()["Handled"]["result"]

                def reset():
                    # Real launches start a new provider. This persistent test
                    # connection must allow the plugins' async reset to begin.
                    send("Reset")
                    time.sleep(0.05)

                assert history() == [], "A fresh profile must have no fabricated history"
                checks += 1
                for text in ("?", " ? ", "\t?\t"):
                    assert all(not matches for matches in query(text).values()), "Help must suppress plugin searches"
                    assert history() == [], "Help must not record an operation"
                    checks += 1

                # Plugin initialization is asynchronous; wait for both real plugins to be ready.
                deadline = time.monotonic() + 10
                while True:
                    results = query("brave")
                    if results["Applications"] and results["Kidex"] and query(":")["Actions"]:
                        break
                    assert time.monotonic() < deadline, "Applications/Kidex did not initialize"
                    time.sleep(0.05)
                checks += 1
                assert query("#project")["Kidex"][0]["description"] == str(projects)
                assert len(query("> Anyrun Frequent")["Applications"]) == 6
                checks += 2
                print("PASS: busca sem prefixo, caminhos próximos antes dos drives e seis resultados")

                for text, plugin, icon in (
                    ("> brave", "Applications", None),
                    (">brave", "Applications", None),
                    ("  >   brave", "Applications", None),
                    ("# projeto", "Kidex", "folder"),
                    ("@ projeto", "Kidex", "text-x-generic"),
                    ("#projeto", "Kidex", "folder"),
                    ("@projeto", "Kidex", "text-x-generic"),
                    ("  #   projeto", "Kidex", "folder"),
                    ("  @   projeto", "Kidex", "text-x-generic"),
                    ("# brave", "Kidex", "folder"),
                    ("@ brave", "Kidex", "text-x-generic"),
                    (": shutdown", "Actions", None),
                    (":shutdown", "Actions", None),
                    ("  :   reboot", "Actions", None),
                    ("$ echo teste", "Shell", None),
                    ("$echo teste", "Shell", None),
                    ("  $   echo teste", "Shell", None),
                ):
                    results = query(text)
                    assert results[plugin], f"No results for {text!r}"
                    assert all(not matches for name, matches in results.items() if name != plugin), text
                    if icon:
                        assert all(match["icon"] == icon for match in results[plugin]), (text, results)
                        if "projeto" in text or text in ("#", "@"):
                            assert len(results[plugin]) == 6, f"Search must return six entries after filtering: {text!r}"
                    checks += 1
                print("PASS: prefixos com/sem espaços, tipos corretos e filtro antes do limite")
                for text in (">", "> ", "  >   ", "@", "@ ", "  @   ", "#", "# ", "$", "$ "):
                    assert all(not matches for matches in query(text).values()), "A new profile must not invent usage"
                checks += 1

                for text in (">", "> $ echo teste", "> 1+1", "# 1+1", "@ 1+1"):
                    results = query(text)
                    allowed = "Applications" if text.startswith(">") else "Kidex"
                    assert all(not matches for name, matches in results.items() if name != allowed), text
                    checks += 1
                assert not query("1+1")["Rink"], "Calculator leaked into normal search"
                assert not query("=1+1")["Rink"], "Typing must not evaluate or invent calculator history"
                for text in ("", "   ", "=", "=   ", "= 1+1", "=1+1", "  =   1+1"):
                    assert all(not matches for matches in query(text).values()), text
                    checks += 1
                shell_results = query("$ echo teste")
                assert len(shell_results["Shell"]) == 1
                assert shell_results["Shell"][0]["title"] == "echo teste"
                assert all(not matches for name, matches in shell_results.items() if name != "Shell")
                checks += 2
                print("PASS: exclusividade e funcionamento normal de Rink/Shell")

                expected_actions = {"Suspend", "Log out", "Reboot", "Shutdown"}
                expected_actions |= {f"History test {number}" for number in range(8)} | {"History confirm", "History fail"}
                for text in (":", ": ", "  :   "):
                    results = query(text)
                    assert {match["title"] for match in results["Actions"]} == expected_actions
                    assert [match["title"] for match in results["Actions"][:4]] == ["Suspend", "Log out", "Reboot", "Shutdown"]
                    assert all(not matches for name, matches in results.items() if name != "Actions")
                    checks += 1
                for text, title in (
                    (": suspend", "Suspend"),
                    (":suspend", "Suspend"),
                    (": lock", "Suspend"),
                    (": shutdown", "Shutdown"),
                    (":shutdown", "Shutdown"),
                    (": poweroff", "Shutdown"),
                    (": power", "Shutdown"),
                    (": end session", "Log out"),
                    (": restart", "Reboot"),
                    (": reboot", "Reboot"),
                    (": logout", "Log out"),
                ):
                    results = query(text)
                    assert results["Actions"][0]["title"] == title, (text, results)
                    assert all(not matches for name, matches in results.items() if name != "Actions")
                    checks += 1
                for text in ("$", "$   "):
                    results = query(text)
                    assert all(not matches for name, matches in results.items() if name != "Shell")
                    assert all(not match["title"].strip() for match in results["Shell"])
                    checks += 1
                assert query("reboot")["Actions"], "Unprefixed actions stopped working"
                assert not query(": hibernate")["Actions"], "A removed power action is still available"
                ordered = [item["title"] for item in query(": o")["Actions"] if item["title"] in {"Log out", "Reboot", "Shutdown"}]
                assert ordered == ["Log out", "Reboot", "Shutdown"]
                assert not query(":sh echo teste")["Shell"], "The old shell prefix is still active"
                assert not query("$ :shutdown")["Actions"]
                checks += 3
                print("PASS: ações em inglês e prefixo $ exclusivo para shell")

                # A running plugin must notice additions/removals and keep IDs
                # stable when a refreshed index arrives in another order.
                original_file = next(item for item in query("@ projeto-0.txt")["Kidex"] if item["title"] == "projeto-0.txt")
                usb = root / "USB fixture"
                usb.mkdir()
                (usb / "project").mkdir()
                usb_file = usb / "project/usb-report.txt"
                usb_file.touch()
                index.append({"path": str(usb), "directory": True})
                index.reverse()
                time.sleep(0.55)
                assert any(item["description"] == str(usb) for item in query("# USB fixture")["Kidex"])
                assert not query("@ usb-report.txt")["Kidex"], "USB contents must not be searchable"
                query("@ projeto-0.txt")
                assert handle("Kidex", original_file) == {"Refresh": True}
                menu = query("@ projeto-0.txt")["Kidex"]
                assert [item["title"] for item in menu] == ["Open", "Copy", "Copy path", "Back"]
                assert all(item["description"] is None for item in menu)
                assert handle("Kidex", next(item for item in menu if item["title"] == "Back")) == {"Refresh": False}
                usb_result = next(item for item in query("# USB fixture")["Kidex"] if item["description"] == str(usb))
                assert handle("Kidex", usb_result) == {"Refresh": True}
                menu = query("# USB fixture")["Kidex"]
                copy_path = next(item for item in menu if item["title"] == "Copy path")
                usb_file.unlink()
                (usb / "project").rmdir()
                index[:] = [entry for entry in index if not Path(entry["path"]).is_relative_to(usb)]
                time.sleep(0.55)
                assert handle("Kidex", copy_path) == {"Refresh": False}, "An unplugged item must not execute"
                assert not query("# USB fixture")["Kidex"]
                assert not query("@ usb-report.txt")["Kidex"]
                assert usb.exists(), "Removal must come from the live index, not deleting the mount point"
                assert history() == []
                checks += 5
                print("PASS: índice atualizado em execução, IDs estáveis e remoção de USB sem execução")

                results = query("# projeto")
                selected = results["Kidex"][0]
                selected_path = selected["description"]
                assert Path(selected_path).name == selected["title"], "Kidex must include the item in its path"
                send({"Handle": {"plugin": info["Kidex"], "selection": selected}})
                assert receive()["Handled"]["result"] == {"Refresh": True}
                menu = query("# projeto")
                assert all(not matches for name, matches in menu.items() if name != "Kidex")
                assert [item["title"] for item in menu["Kidex"]] == ["Open", "Copy path", "Back"]
                assert all(item["description"] is None for item in menu["Kidex"])
                back = next(match for match in menu["Kidex"] if match["title"] == "Back")
                send({"Handle": {"plugin": info["Kidex"], "selection": back}})
                assert receive()["Handled"]["result"] == {"Refresh": False}
                assert all(match["icon"] == "folder" for match in query("# projeto")["Kidex"])
                restored = query("brave")
                assert restored["Applications"] and restored["Kidex"]
                checks += 2
                print("PASS: seleção/voltar do Kidex e retorno à busca geral")

                # Opening/canceling a menu is not a completed operation.
                assert history() == []
                selected = query(": History confirm")["Actions"][0]
                assert handle("Actions", selected) == {"Refresh": True}
                assert history() == []
                cancel = next(item for item in query(": History confirm")["Actions"] if item["title"] == "Cancel")
                assert handle("Actions", cancel) == {"Refresh": False}
                assert history() == []
                assert not (root / "confirmed").exists()
                checks += 1

                # Finish a harmless fixture action, then ensure replay still asks for confirmation.
                selected = query(": History confirm")["Actions"][0]
                assert handle("Actions", selected) == {"Refresh": True}
                confirmation = next(item for item in query(": History confirm")["Actions"] if item["title"] != "Cancel")
                assert handle("Actions", confirmation) == "Close"
                assert (root / "confirmed").exists()
                assert history()[0]["selection"]["title"] == "History confirm"
                (root / "confirmed").unlink()
                # A completed action closes the launcher; Reset models the next
                # launch before exercising replay in this persistent test connection.
                reset()
                send({"Replay": {"index": 0}})
                assert receive()["Handled"]["result"] == {"Refresh": True}
                assert not (root / "confirmed").exists()
                cancel = next(item for item in query(": History confirm")["Actions"] if item["title"] == "Cancel")
                assert handle("Actions", cancel) == {"Refresh": False}
                checks += 1

                # Spy on the standard system opener without launching a real UI.
                open_file = root / "opener-target.txt"
                open_directory = root / "opener-target"
                open_file.write_bytes(b"clipboard test\n")
                open_directory.mkdir()
                index.extend([
                    {"path": str(open_file), "directory": False},
                    {"path": str(open_directory), "directory": True},
                ])
                reset()
                for prefix, path, expected_menu in (
                    ("@", open_file, ["Open", "Copy", "Copy path", "Back"]),
                    ("#", open_directory, ["Open", "Copy path", "Back"]),
                ):
                    text = prefix + " " + str(path)
                    selected = next(item for item in query(text)["Kidex"] if item["description"] == str(path))
                    assert handle("Kidex", selected) == {"Refresh": True}
                    options = query(text)["Kidex"]
                    assert [item["title"] for item in options] == expected_menu
                    assert all(item["description"] is None for item in options)
                    marker = root / "opened.json"
                    marker.unlink(missing_ok=True)
                    assert handle("Kidex", options[0]) == "Close"
                    deadline = time.monotonic() + 5
                    while not marker.exists():
                        assert time.monotonic() < deadline, "Open did not invoke the system opener"
                        time.sleep(0.02)
                    assert json.loads(marker.read_text()) == [str(path)]
                    reset()
                    checks += 1
                text = "@ " + str(open_file)
                selected = next(item for item in query(text)["Kidex"] if item["description"] == str(open_file))
                assert handle("Kidex", selected) == {"Refresh": True}
                copy = next(option for option in query(text)["Kidex"] if option["title"] == "Copy")
                assert bytes(handle("Kidex", copy)["Copy"]) == open_file.read_bytes()
                reset()
                checks += 1

                # Enter evaluates in the entry; queries only show completed operations.
                def calculate(text):
                    send({"Calculate": {"text": text}})
                    response = receive()["Calculated"]
                    assert response["text"] == text
                    return response["calculation"]

                assert not query("=")["Rink"]
                assert calculate("=2+2") == {"expression": "2+2", "result": "4"}
                assert history()[0]["query"] == "=2+2"
                operations = query("=")["Rink"]
                assert [(item["description"], item["title"]) for item in operations] == [("2+2", "4")]
                assert [(item["description"], item["title"]) for item in query("=4*3")["Rink"]] == [("2+2", "4")]
                assert calculate("=4*3") == {"expression": "4*3", "result": "12"}
                assert calculate("  =   10/2") == {"expression": "10/2", "result": "5"}
                before = history()
                saved = (root / "state/anyrun/calculations.json").read_bytes()
                assert calculate("=2 +") is None
                assert calculate("= ") is None
                assert calculate("report.txt") is None
                assert history() == before
                assert (root / "state/anyrun/calculations.json").read_bytes() == saved
                assert calculate("=2+2") == {"expression": "2+2", "result": "4"}
                assert len(query("=")["Rink"]) == 3, "Repeated operations must be deduplicated"
                for number in range(7):
                    assert calculate(f"=10+{number}") == {"expression": f"10+{number}", "result": str(10+number)}
                calculator_recent = query("=")["Rink"]
                assert [item["title"] for item in calculator_recent] == ["16", "15", "14", "13", "12", "11"]
                assert all(not matches for name, matches in query("=   ").items() if name != "Rink")
                checks += 9
                print("PASS: cálculo com Enter, histórico de operações, continuação, entradas inválidas e limite de seis")

                # Shell and application history exercise their real handlers using only touch.
                shell = query(f"$ touch {root}/shell")["Shell"][0]
                assert handle("Shell", shell) == "Close"
                application = next(item for item in query("> Anyrun History Test")["Applications"] if item["title"] == "Anyrun History Test")
                assert handle("Applications", application) == "Close"
                deadline = time.monotonic() + 5
                while not ((root / "shell").exists() and (root / "application").exists()):
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
                assert history()[0]["plugin"]["name"] == "Applications"
                checks += 1

                for number in range(8):
                    result = query(f": History test {number}")["Actions"][0]
                    assert result["title"] == f"History test {number}"
                    assert handle("Actions", result) == "Close"
                    assert len(history()) <= 6
                assert [entry["selection"]["title"] for entry in history()] == [f"History test {number}" for number in range(7, 1, -1)]
                # Repeating an item moves it to the front without creating a duplicate.
                result = query(": History test 7")["Actions"][0]
                assert handle("Actions", result) == "Close"
                assert len(history()) == 6
                checks += 2

                # Usage survives leaving the six-item recent history.
                assert all(entry["plugin"]["name"] == "Actions" for entry in history())
                assert [item["title"] for item in query(">")["Applications"]] == ["Anyrun History Test"]
                checks += 1

                def run_application(title):
                    result = next(item for item in query("> " + title)["Applications"] if item["title"] == title)
                    assert handle("Applications", result) == "Close"

                for number in range(8):
                    run_application(f"Anyrun Frequent {number}")
                run_application("Anyrun History Test")
                run_application("Anyrun History Test")
                run_application("Anyrun Frequent 7")
                run_application("Anyrun Frequent 7")
                for text in (">", "> ", "  >   "):
                    results = query(text)
                    assert len(results["Applications"]) == 6
                    assert [item["title"] for item in results["Applications"][:2]] == ["Anyrun Frequent 7", "Anyrun History Test"], "Frequency must outrank recency; ties use recency"
                    assert all(not matches for name, matches in results.items() if name != "Applications")
                deadline = time.monotonic() + 5
                while not (root / "frequent-7").exists():
                    assert time.monotonic() < deadline
                    time.sleep(0.02)
                (root / "frequent-7").unlink()
                assert handle("Applications", query(">")["Applications"][0]) == "Close"
                deadline = time.monotonic() + 5
                while not (root / "frequent-7").exists():
                    assert time.monotonic() < deadline, "Suggested application used the wrong ID"
                    time.sleep(0.02)
                frequent_apps = [item["title"] for item in query(">")["Applications"]]
                checks += 2

                def copy_file(path, prefix="@"):
                    text = prefix + " " + str(path)
                    deadline = time.monotonic() + 5
                    while True:
                        selected = next((item for item in query(text)["Kidex"]
                                         if item["description"] == str(path)
                                         and item["icon"] == ("folder" if prefix == "#" else "text-x-generic")
                                         and item["title"] == path.name), None)
                        if selected is not None:
                            break
                        assert time.monotonic() < deadline
                        time.sleep(0.02)
                    assert handle("Kidex", selected) == {"Refresh": True}
                    copy = next(item for item in query(text)["Kidex"] if item["title"] == "Copy path")
                    assert bytes(handle("Kidex", copy)["Copy"]).decode() == str(path)
                    reset()

                for number in range(6):
                    copy_file(root / f"projeto-{number}.txt")
                copy_file(root / "brave.txt")
                for number in (0, 0, 1, 1):
                    copy_file(root / f"projeto-{number}.txt")
                for text in ("@", "@ ", "  @   "):
                    results = query(text)
                    assert len(results["Kidex"]) == 6, (
                        text, [item["title"] for item in results["Kidex"]],
                        [(usage["entry"]["selection"]["title"], usage["count"], usage["entry"]["query"])
                         for usage in json.loads((root / "state/anyrun/usage.json").read_text())
                         if usage["entry"]["plugin"]["name"] == "Kidex"],
                    )
                    assert [item["title"] for item in results["Kidex"][:2]] == ["projeto-1.txt", "projeto-0.txt"]
                    assert all(item["icon"] == "text-x-generic" for item in results["Kidex"])
                    assert all(not matches for name, matches in results.items() if name != "Kidex")
                # Skip a removed item even when the index still contains its old path.
                (root / "projeto-1.txt").unlink()
                suggestions = query("@")["Kidex"]
                assert len(suggestions) == 5, "Removed files must not be backfilled from discarded history"
                assert suggestions[0]["title"] == "projeto-0.txt"
                assert all(item["title"] != "projeto-1.txt" for item in suggestions)
                assert handle("Kidex", suggestions[0]) == {"Refresh": True}
                menu = query("@")["Kidex"]
                copy = next(item for item in menu if item["title"] == "Copy path")
                assert bytes(handle("Kidex", copy)["Copy"]).decode() == str(root / "projeto-0.txt")
                reset()
                frequent_files = [item["description"] for item in query("@")["Kidex"]]
                assert frequent_files[0] == str(root / "projeto-0.txt")
                assert len(frequent_files) == 5
                assert all(not matches for matches in query("").values())

                for number in range(6):
                    copy_file(root / f"projeto-{number}", "#")
                copy_file(root / "brave", "#")
                for number in (0, 0, 1, 1):
                    copy_file(root / f"projeto-{number}", "#")
                for text in ("#", "# ", "  #   "):
                    results = query(text)
                    assert len(results["Kidex"]) == 6
                    assert [item["title"] for item in results["Kidex"][:2]] == ["projeto-1", "projeto-0"]
                    assert all(item["icon"] == "folder" for item in results["Kidex"])
                    assert all(not matches for name, matches in results.items() if name != "Kidex")
                directory = query("#")["Kidex"][0]
                assert handle("Kidex", directory) == {"Refresh": True}
                copy = next(item for item in query("#")["Kidex"] if item["title"] == "Copy path")
                assert bytes(handle("Kidex", copy)["Copy"]).decode() == directory["description"]
                reset()

                for number in range(7):
                    command_text = f"touch {root}/command-{number}"
                    assert handle("Shell", query("$ " + command_text)["Shell"][0]) == "Close"
                for number in (0, 0, 1, 1):
                    command_text = f"touch {root}/command-{number}"
                    assert handle("Shell", query("$ " + command_text)["Shell"][0]) == "Close"
                for text in ("$", "$ ", "  $   "):
                    results = query(text)
                    assert len(results["Shell"]) == 6
                    assert [item["title"] for item in results["Shell"][:2]] == [f"touch {root}/command-1", f"touch {root}/command-0"]
                    assert all(not matches for name, matches in results.items() if name != "Shell")
                assert handle("Shell", query("$")["Shell"][0]) == "Close"
                frequent_shell = [item["title"] for item in query("$")["Shell"]]
                assert len(frequent_shell) == 6, "Commands selected from $ must remain resolvable"
                stored_usage = json.loads((root / "state/anyrun/usage.json").read_text())
                from collections import Counter
                buckets = Counter((entry["entry"]["plugin"]["name"],
                                   entry["entry"]["selection"]["icon"] if entry["entry"]["plugin"]["name"] == "Kidex" else None)
                                  for entry in stored_usage)
                assert len(stored_usage) == 24 and sorted(buckets.values()) == [6, 6, 6, 6], buckets
                assert len(json.loads((root / "state/anyrun/history.json").read_text())) == 6
                assert len(json.loads((root / "state/anyrun/calculations.json").read_text())) == 6
                checks += 7
                # Preserve the action replay case below after the extra popularity fixtures.
                result = query(": History test 7")["Actions"][0]
                assert handle("Actions", result) == "Close"
                checks += 3
                print("PASS: mais usados por frequência, desempate recente, limite 6 e seleção com >/@")

                # Copy Path finishes the Kidex operation and stores the actual item, not its menu.
                item = query("# projeto")["Kidex"][0]
                assert handle("Kidex", item) == {"Refresh": True}
                copy = next(option for option in query("# projeto")["Kidex"] if option["title"] == "Copy path")
                result = handle("Kidex", copy)
                assert bytes(result["Copy"]).decode() == item["description"]
                reset()
                frequent_directories = [entry["description"] for entry in query("#")["Kidex"]]
                recent = history()
                assert recent[0]["selection"] == item
                assert len(recent) == 6
                checks += 1

                # Clearing is separate from asking for history, including queued old searches.
                send({"Query": {"text": "brave"}})
                send({"Query": {"text": ""}})
                cleared = set()
                while len(cleared) < len(info):
                    response = receive()["Matches"]
                    name = response["plugin"]["name"]
                    if response["query"] == "":
                        assert not response["matches"], "An empty search returned suggestions"
                        cleared.add(name)
                assert all(not matches for matches in query("").values())
                assert history() == recent
                checks += 1

                # A failed operation and its error dismissal must not enter history.
                failed = query(": History fail")["Actions"][0]
                assert handle("Actions", failed) == {"Refresh": True}
                error = query(": History fail")["Actions"][0]
                assert error["title"] == "ERROR!"
                assert handle("Actions", error) == "Close"
                assert history() == recent
                checks += 1
                print("PASS: histórico de execuções, limite 6, deduplicação, cancelamento, falhas e limpeza")
                send("Quit")
                process.wait(timeout=10)
                assert process.returncode == 0

            # Change every action ID and the index order before a new provider process.
            shifted = (root / "actions.ron").read_text().replace(
                "custom_actions: [", 'custom_actions: [(title: "Inserted fixture", command: "true"),'
            )
            (root / "actions.ron").write_text(shifted)
            index.reverse()
            for number in (6, 7):
                (root / f"action-{number}").unlink(missing_ok=True)
            process.stderr.close()
            process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            connection, _ = listener.accept()
            connection.settimeout(10)
            with connection, connection.makefile("rwb") as stream:
                info = {plugin["name"]: plugin for plugin in receive()["Ready"]["info"]}
                assert history() == recent, "History did not persist across launches"
                assert query("=")["Rink"] == calculator_recent, "Calculator operations did not persist independently"
                checks += 1
                assert [item["title"] for item in query(">")["Applications"]] == frequent_apps
                assert [item["description"] for item in query("@")["Kidex"]] == frequent_files
                assert [item["description"] for item in query("#")["Kidex"]] == frequent_directories
                assert [item["title"] for item in query("$")["Shell"]] == frequent_shell
                checks += 1
                send({"Replay": {"index": 0}})
                assert receive()["Handled"]["result"] == {"Refresh": True}
                menu = query(recent[0]["query"])["Kidex"]
                assert [option["title"] for option in menu] == ["Open", "Copy path", "Back"]
                assert all(option["description"] is None for option in menu)
                copy = next(option for option in menu if option["title"] == "Copy path")
                assert bytes(handle("Kidex", copy)["Copy"]).decode() == item["description"]
                send({"Replay": {"index": 1}})
                assert receive()["Handled"]["result"] == "Close"
                assert (root / "action-7").exists(), "Replay used a stale action ID"
                assert not (root / "action-6").exists(), "Replay executed the wrong action"
                assert len(history()) == 6
                assert all(not matches for matches in query("").values())
                checks += 3
                print("PASS: persistência e replay após mudar IDs, sem abrir a interface")
                send("Quit")
                process.wait(timeout=10)
                assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            errors = process.stderr.read().decode()
            process.stderr.close()
            stopped.set()
            server.join(timeout=2)
            kidex.close()
            listener.close()
            if process.returncode:
                print(errors)
    print(f"{checks} verificações de integração passaram.")


if __name__ == "__main__":
    main()

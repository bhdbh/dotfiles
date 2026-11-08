#!/usr/bin/env python3
"""Verify visible action history and replay in a private headless compositor."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", type=Path, required=True)
    parser.add_argument("--actions-plugin", type=Path, required=True)
    executable = parser.add_mutually_exclusive_group(required=True)
    executable.add_argument("--test-binary", type=Path)
    executable.add_argument("--launcher", type=Path, help="Capture a launcher with an isolated D-Bus session")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--style", type=Path, default=Path.home() / ".config/anyrun/style.css")
    args = parser.parse_args()
    if args.launcher and not args.screenshot:
        parser.error("--launcher requires --screenshot")

    with tempfile.TemporaryDirectory(prefix="anyrun-actions-history-ui-") as temporary:
        root = Path(temporary)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        config = root / "config"
        config.mkdir()
        marker = config / "executed"
        command = shlex.join(["/usr/bin/touch", str(marker)])
        titles = ["Suspend", "Custom action", "Reboot"]
        (config / "actions.ron").write_text(
            "Config(enable_power_actions: false, custom_actions: [\n" +
            "\n".join(f'(title: {json.dumps(title)}, command: {json.dumps(command)}, '
                      'description: "Harmless UI fixture", icon: "system-run", confirm: true),'
                      for title in titles) + "\n])\n"
        )
        (config / "config.ron").write_text(f'''Config(
            width: Absolute(480), height: Absolute(1),
            x: Fraction(0.5), y: Fraction(0.5),
            hide_icons: true, hide_plugin_info: true,
            ignore_exclusive_zones: true, close_on_click: true,
            show_results_immediately: true, max_entries: Some(6),
            provider: {json.dumps(str(args.provider.resolve()))},
            plugins: [{json.dumps(str(args.actions_plugin.resolve()))}],
        )''')
        (config / "style.css").write_text(args.style.read_text())
        state = root / "state/anyrun"
        state.mkdir(parents=True)
        history = [{
            "plugin": {"name": "Actions", "icon": "system-run"},
            "selection": {"title": title, "description": "Harmless UI fixture",
                          "use_pango": False, "icon": "system-run", "id": index},
            "query": ": " + title,
        } for index, title in enumerate(titles)]
        (state / "history.json").write_text(json.dumps(history))
        sway_config = root / "sway.conf"
        sway_config.write_text("output HEADLESS-1 mode 1280x800\nseat seat0 fallback true\nxwayland disable\nswaybg_command -\n")
        environment = dict(os.environ, XDG_RUNTIME_DIR=str(runtime),
                           XDG_STATE_HOME=str(root / "state"),
                           XDG_CACHE_HOME=str(root / "cache"),
                           XDG_CONFIG_HOME=str(root / "xdg-config"),
                           WLR_BACKENDS="headless", WLR_HEADLESS_OUTPUTS="1",
                           WLR_RENDERER="pixman", WLR_LIBINPUT_NO_DEVICES="1",
                           GTK_A11Y="none", GDK_BACKEND="wayland", GSK_RENDERER="cairo",
                           ANYRUN_UI_CONFIG=str(config))
        for name in ("WAYLAND_DISPLAY", "SWAYSOCK", "ANYRUN_UI_PREVIEW", "ANYRUN_HISTORY_SCREENSHOT"):
            environment.pop(name, None)
        if args.screenshot:
            environment["ANYRUN_HISTORY_SCREENSHOT"] = str(args.screenshot.resolve())
        processes = []
        with (root / "sway.log").open("w+") as log:
            compositor = subprocess.Popen(["sway", "-c", str(sway_config)], env=environment,
                                          stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 10
                while True:
                    ipc = list(runtime.glob("sway-ipc.*.sock"))
                    wayland = [path for path in runtime.glob("wayland-*") if not path.name.endswith(".lock")]
                    if ipc and wayland:
                        break
                    if compositor.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    time.sleep(0.05)
                environment.update(SWAYSOCK=str(ipc[0]), WAYLAND_DISPLAY=wayland[0].name)
                if args.launcher:
                    address = str(root / "bus.sock")
                    bus_config = root / "bus.conf"
                    bus_config.write_text('''<busconfig>
                        <type>session</type><auth>EXTERNAL</auth>
                        <policy context="default">
                            <allow user="*"/><allow own="*"/>
                            <allow send_destination="*"/><allow receive_sender="*"/>
                        </policy>
                    </busconfig>''')
                    broker = subprocess.Popen(["systemd-socket-activate", "--listen=" + address,
                                               "--setenv=XDG_RUNTIME_DIR", "--setenv=DBUS_SESSION_BUS_ADDRESS",
                                               "dbus-broker-launch", "--scope", "user",
                                               "--config-file", str(bus_config)],
                                              env=dict(environment, XDG_RUNTIME_DIR=os.environ["XDG_RUNTIME_DIR"]),
                                              stdout=log, stderr=subprocess.STDOUT)
                    processes.append(broker)
                    environment["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + address
                    deadline = time.monotonic() + 5
                    while not Path(address).exists():
                        assert broker.poll() is None and time.monotonic() < deadline
                        time.sleep(0.01)
                    launcher = subprocess.Popen([str(args.launcher.resolve()), "--config-dir", str(config)],
                                                env=environment, stdin=subprocess.DEVNULL,
                                                stdout=log, stderr=subprocess.STDOUT)
                    processes.append(launcher)
                    time.sleep(2)
                    if launcher.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    subprocess.run(["grim", str(args.screenshot.resolve())], env=environment, check=True, timeout=5)
                else:
                    subprocess.run([str(args.test_binary.resolve()), "actions_history_ui_flow", "--ignored",
                                    "--nocapture", "--test-threads=1"], env=environment, check=True, timeout=30)
                assert not marker.exists(), "UI verification must never execute an action"
                assert json.loads((state / "history.json").read_text()) == history
                if args.test_binary:
                    print("PASS: Suspend and custom actions are visible; replay opens confirmation without executing")
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                if compositor.poll() is None:
                    compositor.terminate()
                    compositor.wait(timeout=5)


if __name__ == "__main__":
    main()

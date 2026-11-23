#!/usr/bin/env python3
"""Exercise the real bar and workspace buttons in a private two-output Sway."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time


REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("outputs", REPO / "sway/scripts/outputs.py")
outputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outputs)

PROBE = """
    IpcHandler {
        target: "test"
        function snapshot(): string {
            return JSON.stringify({
                primary: root.primaryOutputName,
                workspaces: root.primaryWorkspaces.map(w => ({
                    name: w.name, number: w.number, output: w.output, active: w.active
                })),
                rawWorkspaces: I3.workspaces.values.map(w => ({
                    name: w.name, output: w.monitor?.name, ipcOutput: w.lastIpcObject.output
                })),
                occupancyReady: workspaceOccupancy.ready,
                creationAllowed: root.workspaceCreationAllowed,
                recordingDestination: recordingState.destination,
                bars: testBars.instances.map(b => {
                    const [buttons, status] = b.contentItem.children;
                    const [capture, captureDivider, cpu, , , , network, , volume, ,
                           battery, leftDivider, dateTime] = status.children;
                    const charge = battery.children[5];
                    const [date, internalDivider, time] = dateTime.children;
                    const statusDividers = [cpu, volume, battery].map(item => item.children[3]);
                    return {
                        screen: b.screen?.name, width: b.width, height: b.height,
                        buttonsRight: buttons.x + buttons.width,
                        clockLeft: status.x + dateTime.x,
                        clockTexts: [date.text, time.text],
                        networkLabel: network.children[0].text,
                        networkValue: [network.children[1].text, network.children[1].color.toString()],
                        volumeRight: status.x + volume.x + volume.width,
                        audioOutput: [volume.children[3].visible, volume.children[4].visible,
                                      volume.children[4].text],
                        batteryVisible: battery.visible,
                        batteryDividerVisible: leftDivider.visible,
                        clockDividerWidth: leftDivider.width,
                        clockDividerHeight: leftDivider.height,
                        clockDividerTop: leftDivider.y,
                        internalDividerWidth: internalDivider.width,
                        internalDividerHeight: internalDivider.height,
                        internalDividerTop: internalDivider.y,
                        statusDividers: statusDividers.map(d => [d.width, d.height, d.y]),
                        internalGap: time.x - date.x - date.width,
                        batteryLeft: status.x + battery.x,
                        batteryRight: status.x + battery.x + battery.width,
                        chargeIndicator: [charge.width, charge.height, charge.radius, charge.y],
                        dateRight: status.x + dateTime.x + dateTime.width,
                        statusLeft: status.x + (captureDivider.visible ? capture.x : cpu.x),
                        captureIndicators: Array.from(capture.children).map(c => ({
                            visible: c.visible, color: c.color.toString(), x: c.x,
                            geometry: [c.width, c.height, c.radius, c.y]
                        })),
                        captureWidth: capture.width,
                        captureDivider: [captureDivider.visible, captureDivider.width,
                                         captureDivider.height, captureDivider.y],
                        captureGaps: [captureDivider.x - capture.x - capture.width,
                                      cpu.x - captureDivider.x - captureDivider.width],
                        buttons: Array.from(buttons.children)
                            .filter(c => c.visible).map(c => ({label: c.label, active: c.active}))
                    };
                })
            });
        }
        function click(label: string): void {
            const button = testBars.instances[0].contentItem.children[0].children
                .find(c => c.visible && c.label === label);
            if (!button) throw new Error("Missing button: " + label);
            button.clicked();
        }
        function setMedia(camera: bool, microphone: bool): void {
            mediaUsage.cameraInUse = camera;
            mediaUsage.microphoneInUse = microphone;
        }
        function setStatus(wifi: int, fallback: string, battery: bool, audioSwitcher: bool): void {
            systemStats.wifiPercent = wifi;
            systemStats.networkFallbackText = fallback;
            systemStats.batteryPresent = battery;
            systemStats.canCycleAudioOutput = audioSwitcher;
        }
    }
"""


def drag_pointer(socket_path, start, end, extent, *, waypoint_x=None):
    """Send a drag through the private compositor's Wayland virtual pointer."""
    interface = b"zwlr_virtual_pointer_manager_v1\0"
    header = struct.Struct("=II")
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(socket_path))
        with client.makefile("rb") as events:
            def send(object_id, opcode, *args, suffix=b""):
                payload = struct.pack(f"={len(args)}I", *args) + suffix
                client.sendall(header.pack(object_id, ((len(payload) + header.size) << 16) | opcode)
                               + payload)

            def sync(callback_id):
                messages = []
                send(1, 0, callback_id)  # wl_display.sync
                while True:
                    object_id, size_opcode = header.unpack(events.read(header.size))
                    payload = events.read((size_opcode >> 16) - header.size)
                    opcode = size_opcode & 0xffff
                    if object_id == callback_id:
                        return messages
                    if object_id == 1 and opcode == 0:
                        raise AssertionError(f"Wayland protocol error: {payload!r}")
                    messages.append((object_id, opcode, payload))

            send(1, 1, 2)  # wl_display.get_registry
            managers = [struct.unpack_from("=I", payload)[0] for obj, opcode, payload in sync(3)
                        if obj == 2 and opcode == 0 and payload[8:].startswith(interface)]
            assert len(managers) == 1, "Missing Wayland virtual pointer manager"
            binding = interface + b"\0" * (-len(interface) % 4) + struct.pack("=II", 1, 4)
            send(2, 0, managers[0], len(interface), suffix=binding)  # wl_registry.bind
            send(4, 0, 0, 5)  # create_virtual_pointer on the default seat

            timestamp = int(time.monotonic() * 1000) & 0xffffffff
            left_button = 0x110
            send(5, 1, timestamp, *start, *extent)  # motion_absolute
            send(5, 4)  # frame
            send(5, 2, timestamp, left_button, 1)  # press
            send(5, 4)
            callback_id = 6
            if waypoint_x is not None:
                send(5, 1, timestamp, waypoint_x, end[1], *extent)
                send(5, 4)
                sync(callback_id)
                callback_id += 1
                # Let Wayland report the overlap before crossing the workspace boundary.
                time.sleep(0.25)
            send(5, 1, timestamp, *end, *extent)  # exceed the titlebar drag threshold
            send(5, 1, timestamp, end[0] + 10, end[1], *extent)
            send(5, 4)
            send(5, 2, timestamp, left_button, 0)  # release
            send(5, 4)
            sync(callback_id)


def run():
    root = Path(tempfile.mkdtemp(prefix="quickshell-monitor-test-"))
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    config = root / "quickshell"
    config.mkdir()
    text = (REPO / "quickshell/shell.qml").read_text()
    text = text.replace("    Variants {", "    Variants {\n        id: testBars", 1)
    (config / "shell.qml").write_text(text.rsplit("}", 1)[0] + PROBE + "}\n")
    shutil.copyfile(REPO / "quickshell/WorkspaceOccupancy.qml", config / "WorkspaceOccupancy.qml")
    (config / "MediaUsage.qml").write_text("""import QtQuick
QtObject {
    property bool cameraInUse: false
    property bool microphoneInUse: false
}
""")
    # Fixed maximum-width readings avoid depending on host hardware or changing sensors.
    (config / "SystemStats.qml").write_text("""import QtQuick
QtObject {
    property int cpuPercent: 100
    property int cpuTemperature: 100
    property int ramPercent: 100
    property int wifiPercent: 100
    property string networkFallbackText: "Offline"
    property int volumePercent: 100
    property bool canCycleAudioOutput: true
    property int audioOutputNumber: 2
    property bool batteryPresent: true
    property int batteryPercent: 100
    property bool batteryPlugged: true
    property int batteryHealthMode: 0
    property color batteryLabelColor: "#fafafa"
    property color batteryPercentageColor: "#fafafa"
    property color batteryIndicatorColor: "#27272a"
}
""")
    sway_config = root / "sway.conf"
    sway_config.write_text("""output HEADLESS-1 mode 1366x768 pos 0 0
output HEADLESS-2 mode 1920x1080 pos 1366 0
seat seat0 fallback true
xwayland disable
swaybg_command -
default_border normal
tiling_drag enable
workspace 1 output HEADLESS-2
workspace isolated:HEADLESS-1 output HEADLESS-1
""")
    env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime),
               WLR_BACKENDS="headless", WLR_HEADLESS_OUTPUTS="2", WLR_RENDERER="pixman",
               WLR_LIBINPUT_NO_DEVICES="1", QT_QUICK_BACKEND="software")
    for key in ("SWAYSOCK", "I3SOCK", "WAYLAND_DISPLAY", "DISPLAY", "QT_QPA_PLATFORM"):
        env.pop(key, None)
    processes = []
    logs = []

    def launch(command, name):
        log = (root / f"{name}.log").open("w")
        logs.append(log)
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def wait_for(callback, description, timeout=8):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = callback()
                if last:
                    return last
            except (subprocess.SubprocessError, ValueError, IndexError):
                pass
            time.sleep(0.05)
        raise AssertionError(f"Timed out: {description}; last result: {last}")

    def sway(kind, *args):
        result = subprocess.run(["swaymsg", "-r", "-t", kind, *args], env=env,
                                capture_output=True, text=True, check=True, timeout=3)
        data = json.loads(result.stdout)
        if kind == "command":
            assert all(item.get("success") for item in data), data
        return data

    def ipc(function, *args):
        result = subprocess.run(["quickshell", "ipc", "--path", str(config), "call",
                                 "test", function, *args], env=env,
                                capture_output=True, text=True, check=True, timeout=3)
        return json.loads(result.stdout) if function == "snapshot" else None

    def reconcile(primary):
        previous = primary_file.read_text().strip()
        if previous != primary:
            sway("command", f'focus output "{primary}"')
        commands = outputs.plan_workspaces(sway("get_outputs"), sway("get_workspaces"), primary,
                                           previous)
        if commands:
            sway("command", "; ".join(commands))
        primary_file.write_text(primary + "\n")

    def settled(primary):
        state = ipc("snapshot")
        actual = {w["name"]: w["visible"] for w in sway("get_workspaces")
                  if w["output"] == primary and 1 <= w["num"] <= 9}
        if (state["primary"] == primary and len(state["bars"]) == 1
                and state["bars"][0]["screen"] == primary
                and state["bars"][0]["height"] == 30
                and {w["name"]: w["active"] for w in state["workspaces"]} == actual):
            return state
        return None

    def check_layout(state):
        bar = state["bars"][0]
        assert bar["height"] == 30, state
        assert bar["buttonsRight"] + 8 <= bar["statusLeft"], state
        assert bar["batteryDividerVisible"] == bar["batteryVisible"], state
        if bar["batteryVisible"]:
            assert bar["batteryLeft"] - bar["volumeRight"] == 19, state
            assert bar["clockLeft"] - bar["batteryRight"] == 19, state
        else:
            assert bar["clockLeft"] - bar["volumeRight"] == 19, state
        assert bar["width"] - bar["dateRight"] == 8, state
        assert (bar["clockDividerWidth"], bar["clockDividerHeight"]) == (3, 14), state
        assert bar["clockDividerTop"] == 8, state
        assert (bar["internalDividerWidth"], bar["internalDividerHeight"]) == (3, 10), state
        assert bar["internalDividerTop"] == 10, state
        assert bar["statusDividers"] == [[3, 10, 10]] * 3, state
        assert bar["internalGap"] == 19, state
        assert bar["chargeIndicator"] == [14, 14, 0, 8], state
        indicators = [indicator for indicator in bar["captureIndicators"] if indicator["visible"]]
        assert bar["captureDivider"] == [bool(indicators), 3, 14, 8], state
        assert bar["captureWidth"] == (22 * len(indicators) - 8 if indicators else 0), state
        for index, indicator in enumerate(indicators):
            assert indicator["geometry"] == [14, 14, 0, 8], state
            assert indicator["x"] == index * 22, state
        if indicators:
            assert bar["captureGaps"] == [8, 8], state
        date, hour = bar["clockTexts"]
        assert len(hour) == 5 and hour[2] == ":", state
        assert len(date) == 10 and date[2] == date[5] == "-", state
        assert len(bar["buttons"]) >= 1, state

    def open_window(name):
        sway("command", f'workspace --no-auto-back-and-forth "{name}"')
        launch(["foot", "--config", str(REPO / "foot/foot.ini"),
                "--app-id", "monitor-test", "sleep", "300"], f"foot-{len(processes)}")
        wait_for(lambda: any(w["name"] == name and "monitor-test" in (w.get("representation") or "")
                             for w in sway("get_workspaces")), f"window on {name}")

    def workspace_state(numbers, active, creation_allowed):
        state = settled("HEADLESS-2")
        labels = [str(n) for n in numbers] + (["+"] if creation_allowed else [])
        if (state and state["occupancyReady"]
                and [w["number"] for w in state["workspaces"]] == numbers
                and [w["name"] for w in state["workspaces"] if w["active"]] == [active]
                and state["creationAllowed"] == creation_allowed
                and [b["label"] for b in state["bars"][0]["buttons"]] == labels):
            return state
        return None

    def drag_window(source, destination, *, floating=False):
        workspaces = {w["name"]: w for w in sway("get_workspaces")}
        start = workspaces[source]["rect"]
        end = workspaces[destination]["rect"]
        start_x, start_y = start["x"] + start["width"] // 2, start["y"] + 8
        end_x, end_y = end["x"] + end["width"] // 2, end["y"] + end["height"] // 2
        waypoint_x = None
        if floating:
            titlebar = workspaces[source]["floating_nodes"][0]["deco_rect"]
            start_x = start["x"] + titlebar["x"] + titlebar["width"] // 2
            start_y += titlebar["y"]
            boundary = max(start["x"], end["x"])
            direction = 1 if end["x"] > start["x"] else -1
            # Keep the floating window visible on both outputs throughout the crossing.
            waypoint_x = boundary - direction * 100
            end_x = boundary + direction * 30
        drag_pointer(runtime / env["WAYLAND_DISPLAY"],
                     (start_x, start_y), (end_x, end_y), (1366 + 1920, 1080), waypoint_x=waypoint_x)
        wait_for(lambda: any(w["name"] == destination
                             and (w["floating_nodes"] if floating
                                  else "monitor-test" in (w.get("representation") or ""))
                             for w in sway("get_workspaces")), f"window dragged to {destination}")

    try:
        compositor = launch(["sway", "--unsupported-gpu", "-c", str(sway_config)], "sway")
        wait_for(lambda: list(runtime.glob("sway-ipc.*.sock")), "private compositor")
        env["SWAYSOCK"] = str(next(runtime.glob("sway-ipc.*.sock")))
        env["I3SOCK"] = env["SWAYSOCK"]
        env["WAYLAND_DISPLAY"] = next(p.name for p in runtime.glob("wayland-*")
                                      if not p.name.endswith(".lock"))
        primary_file = runtime / "sway-outputs/primary-output"
        primary_file.parent.mkdir()
        primary_file.write_text("HEADLESS-2\n")
        open_window("isolated:HEADLESS-1")
        launch(["quickshell", "--path", str(config), "--no-color"], "quickshell")
        state = wait_for(lambda: workspace_state([1], "1", False), "initial empty workspace hides +")
        check_layout(state)

        drag_window("isolated:HEADLESS-1", "1")
        wait_for(lambda: workspace_state([1], "1", True), "dragged window into workspace 1 shows +")
        drag_window("1", "isolated:HEADLESS-1")
        wait_for(lambda: workspace_state([1], "1", False), "dragged last window out of workspace 1 hides +")
        print("PASS tiling drag into and out of workspace 1 updates +", flush=True)

        sway("command", '[app_id="^monitor-test$"] floating enable')
        drag_window("isolated:HEADLESS-1", "1", floating=True)
        wait_for(lambda: workspace_state([1], "1", True), "floating window in workspace 1 shows +")
        drag_window("1", "isolated:HEADLESS-1", floating=True)
        wait_for(lambda: workspace_state([1], "1", False), "floating window leaving workspace 1 hides +")
        sway("command", '[app_id="^monitor-test$"] floating disable')
        print("PASS floating drag across the boundary between outputs updates +", flush=True)

        open_window("1")
        wait_for(lambda: workspace_state([1], "1", True), "opened window on workspace 1 shows +")

        for wifi, fallback, battery, audio_switcher, expected in (
            (24, "Offline", True, True, ["24%", "#ef4444"]),
            (25, "Online", False, False, ["25%", "#f97316"]),
            (50, "Offline", False, True, ["50%", "#fafafa"]),
            (-1, "Online", True, False, ["Online", "#fafafa"]),
            (-1, "Offline", False, False, ["Offline", "#fafafa"]),
            (100, "Offline", True, True, ["100%", "#fafafa"]),
        ):
            ipc("setStatus", str(wifi), fallback, str(battery).lower(), str(audio_switcher).lower())
            state = ipc("snapshot")
            check_layout(state)
            bar = state["bars"][0]
            assert bar["networkLabel"] == "Network:", bar
            assert bar["networkValue"] == expected, bar
            assert bar["batteryVisible"] == battery, bar
            assert bar["audioOutput"] == [audio_switcher, audio_switcher, "2" if audio_switcher else ""], bar
        print("PASS network states and colors, optional battery field, and optional audio switcher", flush=True)

        # Keyboard jumps leave gaps: + must fill the first available number,
        # even with workspace 9 present, and keep the buttons in numeric order.
        sway("command", 'workspace --no-auto-back-and-forth number 9')
        wait_for(lambda: workspace_state([1, 9], "9", False), "empty keyboard workspace hides +")
        open_window("9")
        state = wait_for(lambda: workspace_state([1, 9], "9", True), "workspace 9 with a window shows +")
        check_layout(state)
        ipc("click", "+")
        wait_for(lambda: workspace_state([1, 2, 9], "2", False), "+ fills 2 between 1 and 9")

        sway("command", 'workspace --no-auto-back-and-forth number 9')
        open_window("4")
        sway("command", 'workspace --no-auto-back-and-forth number 9')
        wait_for(lambda: workspace_state([1, 4, 9], "9", True), "sparse workspaces allow creation")
        ipc("click", "+")
        wait_for(lambda: workspace_state([1, 2, 4, 9], "2", False), "+ fills 2 between 1, 4 and 9")
        open_window("2")
        wait_for(lambda: workspace_state([1, 2, 4, 9], "2", True), "populated new workspace shows +")
        ipc("click", "+")
        wait_for(lambda: workspace_state([1, 2, 3, 4, 9], "3", False), "+ fills the next gap at 3")
        print("PASS keyboard jumps to 9, first available number, numeric order and empty workspace gating",
              flush=True)

        sway("command", 'workspace --no-auto-back-and-forth number 1; '
             '[app_id="^monitor-test$" workspace="^(2|4|9)$"] kill')
        wait_for(lambda: workspace_state([1], "1", True), "restore workspaces after sparse creation")

        for cycle in range(2):
            if cycle == 1:
                open_window("isolated:HEADLESS-1")
            sway("command", 'output HEADLESS-2 disable')
            reconcile("HEADLESS-1")
            state = wait_for(lambda: settled("HEADLESS-1"), "workspace migration to notebook")
            check_layout(state)
            remaining = [w for w in sway("get_workspaces") if w["name"].startswith("isolated:")]
            assert not remaining, ("Unreachable workspace after disconnect", remaining)
            wait_for(lambda: ipc("snapshot")["creationAllowed"], "notebook + button")
            ipc("click", "+")
            state = wait_for(lambda: not ipc("snapshot")["creationAllowed"], "empty workspace hides +")
            ipc("click", "1")
            wait_for(lambda: any(w["name"] == "1" and w["active"]
                                 for w in ipc("snapshot")["workspaces"]), "numbered button activates workspace")
            sway("command", 'output HEADLESS-2 enable')
            reconcile("HEADLESS-2")
            state = wait_for(lambda: settled("HEADLESS-2"), "workspace migration to external")
            check_layout(state)
            assert {w["name"] for w in state["workspaces"]} == {str(n) for n in range(1, cycle + 3)}, state
            print(f"PASS disconnect/reconnect cycle {cycle + 1}", flush=True)

        # Reserve the full nine-button area and stress the narrow bar with all
        # percentages at 100%, a three-digit temperature, and all status squares.
        for number in range(4, 10):
            open_window(str(number))
        sway("command", 'workspace --no-auto-back-and-forth "1"; output HEADLESS-2 disable')
        reconcile("HEADLESS-1")
        state = wait_for(lambda: settled("HEADLESS-1"), "nine workspaces on notebook")
        check_layout(state)
        assert not state["creationAllowed"], state
        assert [b["label"] for b in state["bars"][0]["buttons"]] == list("123456789"), state
        def capture_bar(count):
            state = ipc("snapshot")
            indicators = state["bars"][0]["captureIndicators"]
            return state if sum(indicator["visible"] for indicator in indicators) == count else None

        assert state["bars"][0]["captureWidth"] == 0, state
        ipc("setMedia", "true", "false")
        state = wait_for(lambda: capture_bar(1), "camera alone shows a square and divider")
        check_layout(state)
        assert [c["visible"] for c in state["bars"][0]["captureIndicators"]] == [False, True, False], state
        ipc("setMedia", "true", "true")
        check_layout(wait_for(lambda: capture_bar(2), "camera and microphone with one gap"))
        marker = runtime / "sway-screen-recording"
        recording = root / "test recording.mp4"
        marker.write_text(f"{recording}\n")
        wait_for(lambda: ipc("snapshot")["recordingDestination"] == str(recording),
                 "recorder started before the first frame")
        assert capture_bar(2), "starting the recorder must leave the red square hidden"
        marker.unlink()
        wait_for(lambda: not ipc("snapshot")["recordingDestination"], "recorder aborted before capture")
        assert capture_bar(2), "a recorder that aborts must leave the red square hidden"
        marker.write_text(f"{recording}\n")
        recording.touch()
        check_layout(wait_for(lambda: capture_bar(3), "all three capture indicators appear"))
        for red in ("#ef4444", "#18181b", "#ef4444"):
            wait_for(lambda: [c["color"] for c in ipc("snapshot")["bars"][0]["captureIndicators"]]
                     == [red, "#22c55e", "#f97316"], f"only recording blinks to {red}", timeout=2)
        marker.unlink()
        check_layout(wait_for(lambda: capture_bar(2), "camera and microphone stay visible"))
        ipc("setMedia", "false", "true")
        check_layout(wait_for(lambda: capture_bar(1), "microphone alone shows a square and divider"))
        ipc("setMedia", "false", "false")
        check_layout(wait_for(lambda: capture_bar(0), "all capture squares and their divider clear"))
        print("PASS capture squares, fixed camera/mic colors, recording blink, and 8 px spacing")
        print("PASS bar at 1920/1366 px, nine buttons, monitor migration, activation and + gating")
    except BaseException:
        print(f"Test artifacts: {root}", flush=True)
        try:
            print("Last bar state:", json.dumps(ipc("snapshot")), flush=True)
            print("Last Sway state:", json.dumps(sway("get_workspaces")), flush=True)
        except (subprocess.SubprocessError, ValueError):
            pass
        for path in root.glob("*.log"):
            print(f"{path.name}:\n{path.read_text()[-5000:]}", flush=True)
        raise
    else:
        shutil.rmtree(root)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        for log in logs:
            log.close()


if __name__ == "__main__":
    run()

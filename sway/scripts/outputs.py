#!/usr/bin/env python3
"""Manage the preferred monitor, laptop lid, and the display hotkey."""
import argparse
import fcntl
import json
import os
import socket
from pathlib import Path
import subprocess
import sys

PREFERRED = ("Acer Technologies", "QG241Y")
DISPLAY_MODES = ("extended", "external", "internal")


def quote(value):
    return json.dumps(value, ensure_ascii=False)


def dimensions(output):
    rect = output.get("rect", {})
    if rect.get("width") and rect.get("height"):
        return rect["width"], rect["height"]
    mode = output.get("current_mode") or next(iter(output.get("modes", [])), {})
    width, height = mode.get("width", 0), mode.get("height", 0)
    if output.get("transform") in ("90", "270", "flipped-90", "flipped-270"):
        width, height = height, width
    scale = output.get("scale") or 1
    return round(width / scale), round(height / scale)


def plan(outputs, closed, previous=None, display_mode="auto"):
    """Return idempotent Sway commands and the selected primary output name."""
    outputs = [o for o in outputs if not o.get("non_desktop", False)]
    internal = next((o for o in outputs if o["name"].startswith(("eDP-", "LVDS-"))), None)
    external = [o for o in outputs if o is not internal
                and (o.get("active") or display_mode != "auto")]
    panel_on = internal is not None and not (closed and external)
    if display_mode == "external" and external:
        panel_on = False
    elif display_mode == "internal" and panel_on:
        external = []
    preferred = next((o for o in external if (o.get("make"), o.get("model")) == PREFERRED), None)
    primary = preferred or (internal if panel_on else None) or next(iter(external), None)
    if primary is None:
        return [], None

    commands = []
    # Physical placement is independent of workspace priority: laptop on the
    # left, external monitors on the right, sharing their bottom edge.
    ordered = ([internal] if internal is not None and panel_on else []) + sorted(
        external, key=lambda o: (o is not preferred, o["name"]))
    if display_mode == "auto":
        if internal is not None and bool(internal.get("active")) != bool(panel_on):
            commands.append(f'output {quote(internal["name"])} {"enable" if panel_on else "disable"}')
    else:
        # Enable the destination first so a transition never disables every output.
        selected = {output["name"] for output in ordered}
        commands.extend(f'output {quote(output["name"])} enable'
                        for output in ordered if not output.get("active"))
        commands.extend(f'output {quote(output["name"])} disable'
                        for output in outputs if output.get("active")
                        and output["name"] not in selected)
    bottom = max(dimensions(o)[1] for o in ordered)
    x = 0
    for output in ordered:
        rect = output.get("rect", {})
        width, height = dimensions(output)
        y = bottom - height
        if rect.get("x") != x or rect.get("y") != y:
            commands.append(f'output {quote(output["name"])} pos {x} {y}')
        x += width

    if primary["name"] != previous:
        commands.append(f'focus output {quote(primary["name"])}')
    return commands, primary["name"]


def sway(message_type, *args):
    result = subprocess.run(["swaymsg", "-r", "-t", message_type, *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def plan_workspaces(outputs, workspaces, primary, previous_primary=None):
    """Reserve numbered workspaces for the primary; isolate other displays."""
    if primary is None:
        return []

    numbers = sorted({1} | {w["num"] for w in workspaces if w["num"] >= 0})
    commands = [f'workspace {number} output {quote(primary)}' for number in numbers]
    focused = next((w["name"] for w in workspaces if w.get("focused")), None)
    renamed = {}
    moved = set()
    switched = False
    primary_changed = previous_primary is not None and previous_primary != primary
    if primary_changed:
        # Focusing a newly connected output may select an empty workspace.
        # Restore the numbered workspace that was visible on the old primary.
        previous_visible = next((w for w in workspaces if w["output"] == previous_primary
                                 and w.get("visible") and w["num"] >= 0), None)
        if previous_visible is not None:
            focused = previous_visible["name"]

    for output in outputs:
        if not output.get("active") or output["name"] == primary or output.get("non_desktop"):
            continue
        name = f'isolated:{output["name"]}'
        commands.append(f'workspace {quote(name)} output {quote(output["name"])}')
        target = next((w for w in workspaces if w["name"] == name), None)
        current = next((w for w in workspaces if w["output"] == output["name"] and w.get("visible")), None)

        # Initial isolation keeps the secondary's current windows in place.
        # A primary change instead migrates all numbered workspaces together.
        if target is None and current is not None and not primary_changed:
            commands.append(f'rename workspace {quote(current["name"])} to {quote(name)}')
            renamed[current["name"]] = name
        elif target is None or target["output"] != output["name"]:
            commands.extend([
                f'workspace --no-auto-back-and-forth {quote(name)}',
                f'move workspace to output {quote(output["name"])}',
            ])
            moved.add(name)
            switched = True

        for workspace in workspaces:
            if workspace["output"] == output["name"] and workspace["num"] >= 0 and workspace["name"] not in renamed:
                commands.extend([
                    f'workspace --no-auto-back-and-forth {quote(workspace["name"])}',
                    f'move workspace to output {quote(primary)}',
                ])
                switched = True

    # All isolated workspaces left on the primary need numbered controls,
    # including those hidden when Sway migrates the unplugged output's workspace.
    used = {w["num"] for w in workspaces if w["name"] not in renamed}
    for workspace in workspaces:
        if (workspace["output"] != primary or not workspace["name"].startswith("isolated:")
                or workspace["name"] in moved):
            continue
        number = 1
        while number in used:
            number += 1
        name = str(number)
        commands.append(f'workspace {number} output {quote(primary)}')
        commands.append(f'rename workspace {quote(workspace["name"])} to {quote(name)}')
        renamed[workspace["name"]] = name
        used.add(number)

    if switched and focused is not None:
        commands.append(f'workspace --no-auto-back-and-forth {quote(renamed.get(focused, focused))}')
    return commands


def lid_closed():
    for path in Path("/proc/acpi/button/lid").glob("*/state"):
        if "closed" in path.read_text():
            return True
    return False


def read_display_mode(runtime):
    path = runtime / "display-mode"
    mode = path.read_text().strip() if path.exists() else "auto"
    return mode if mode in DISPLAY_MODES else "auto"


def next_display_mode(outputs, mode):
    outputs = [output for output in outputs if not output.get("non_desktop")]
    internal = next((output for output in outputs
                     if output["name"].startswith(("eDP-", "LVDS-"))), None)
    external = [output for output in outputs if output is not internal]
    if not external:
        return "internal"
    if internal is None:
        return "external"
    if mode == "auto":
        mode = ("extended" if internal.get("active") and any(o.get("active") for o in external)
                else "internal" if internal.get("active") else "external")
    return DISPLAY_MODES[(DISPLAY_MODES.index(mode) + 1) % len(DISPLAY_MODES)]


def cycle_display(runtime):
    with (runtime / "apply.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        mode = next_display_mode(sway("get_outputs"), read_display_mode(runtime))
        (runtime / "display-mode").write_text(mode + "\n")
    reconcile(runtime)


def reconcile(runtime, dry_run=False):
    with (runtime / "apply.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = runtime / "primary-output"
        previous = state.read_text().strip() if state.exists() else None
        outputs = sway("get_outputs")
        display_mode = read_display_mode(runtime)
        commands, primary = plan(outputs, lid_closed(), previous, display_mode)
        if dry_run:
            print(json.dumps({"mode": display_mode, "primary": primary, "commands": commands,
                              "workspace_commands": plan_workspaces(outputs, sway("get_workspaces"), primary, previous)}, indent=2))
            return
        if commands:
            responses = sway("command", "; ".join(commands))
            if not all(r.get("success") for r in responses):
                raise RuntimeError(f"Sway rejected output configuration: {responses}")
        workspace_commands = plan_workspaces(sway("get_outputs"), sway("get_workspaces"), primary, previous)
        if workspace_commands:
            responses = sway("command", "; ".join(workspace_commands))
            if not all(r.get("success") for r in responses):
                raise RuntimeError(f"Sway rejected workspace configuration: {responses}")
        if primary != previous:
            state.write_text((primary or "") + "\n")


def notify_ready():
    """Notify systemd from this process after primary-output is usable."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.connect(address)
        notifier.sendall(b"READY=1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--watch", action="store_true")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--cycle-display", action="store_true")
    args = parser.parse_args()
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "sway-outputs"
    runtime.mkdir(mode=0o700, exist_ok=True)
    if args.cycle_display:
        cycle_display(runtime)
        return
    if not args.watch:
        reconcile(runtime, dry_run=args.dry_run)
        return

    # Sway reloads may launch another instance; retain exactly one subscriber.
    with (runtime / "watch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with subprocess.Popen(["swaymsg", "-m", "-r", "-t", "subscribe", '["output", "tick"]'], stdout=subprocess.PIPE, text=True) as events:
            try:
                # swaymsg suppresses successful subscribe ACKs. The native first
                # tick is emitted after ACK and printed by swaymsg, so it proves
                # the subscription without another IPC implementation.
                initial = json.loads(events.stdout.readline())
                if initial.get("first") is not True:
                    raise RuntimeError(f"Sway output subscription failed: {initial}")
                ready = False
                # Apply initial configuration immediately.
                try:
                    reconcile(runtime)
                    if (runtime / "primary-output").read_text().strip():
                        notify_ready()
                        ready = True
                except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
                    print(f"sway-outputs: {exc}", file=sys.stderr, flush=True)
                for line in events.stdout:
                    try:
                        reconcile(runtime)
                        if not ready and (runtime / "primary-output").read_text().strip():
                            notify_ready()
                            ready = True
                    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
                        print(f"sway-outputs: {exc}", file=sys.stderr, flush=True)
                # A dropped subscriber must trigger supervision even if swaymsg
                # exits zero. Session shutdown stops this service via BindsTo.
                raise RuntimeError(f"Sway output subscription closed: {events.wait()}")
            finally:
                events.terminate()


if __name__ == "__main__":
    main()

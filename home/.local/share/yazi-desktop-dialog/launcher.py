#!/usr/bin/python3
"""Shared terminal launcher for the file chooser portal and Yazi action plugin."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def portal_environment():
    """Read the same env= entries (including repeats) used by termfilechooser."""
    env = dict(os.environ)
    config_home = Path(env.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    names = [*filter(None, env.get("XDG_CURRENT_DESKTOP", "").split(":")), "config"]
    candidates = [base / "xdg-desktop-portal-termfilechooser" / name
                  for base in (config_home, Path("/etc/xdg")) for name in names]
    config = next((candidate for candidate in candidates if candidate.is_file()), None)
    if config is None:
        return env
    section, continuation = "", False
    for raw in config.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section, continuation = line[1:-1], False
            continue
        if section != "filechooser":
            continue
        if line.startswith("env=") or line.startswith("env ="):
            assignment = line.split("=", 1)[1].strip()
            continuation = True
        elif raw[:1].isspace() and continuation:
            assignment = line
        else:
            continuation = False
            continue
        name, separator, value = assignment.partition("=")
        if not separator or not name.strip():
            raise ValueError("Invalid env entry in the portal configuration.")
        env[name.strip()] = value
    return env


def terminal_command(env, supplied=()):
    command = list(supplied) or shlex.split(env.get(
        "TERMCMD", "/usr/bin/foot --app-id=yazi-filechooser --title=Yazi"))
    if not command:
        raise ValueError("TERMCMD cannot be empty.")
    return command


def yazi_command():
    return str(Path.home() / ".cargo/bin/yazi")


def action(request_file):
    request_file = Path(request_file).resolve(strict=True)
    request = json.loads(request_file.read_text())
    if not isinstance(request, dict):
        raise ValueError("Invalid dialog request.")
    kind = request.get("action")
    if kind not in ("trash", "delete", "manage"):
        raise ValueError("Unsupported action for the desktop-dialog plugin.")
    cwd, targets = request.get("cwd"), request.get("targets")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise ValueError("Invalid action directory.")
    if not isinstance(targets, list) or not targets or any(
        not isinstance(target, str) or not Path(target).is_absolute() for target in targets
    ):
        raise ValueError("Invalid action selection.")
    env = portal_environment()
    env.pop("YAZI_PORTAL_FILECHOOSER", None)
    env.pop("YAZI_VSCODE_REQUEST_FILE", None)
    env["YAZI_DIALOG_REQUEST_FILE"] = str(request_file)
    terminal = terminal_command(env)
    if kind != "manage" and Path(terminal[0]).name in {"foot", "footclient"}:
        title = "Move to Trash" if kind == "trash" else "Delete Permanently"
        terminal += ["--app-id=yazi-confirm", f"--title=Yazi — {title}",
                     "--window-size-chars=76x14"]
    os.chdir(cwd)
    command = [*terminal, yazi_command(), cwd]
    os.execvpe(command[0], command, env)


def filechooser(values):
    if len(values) < 6:
        raise ValueError("The portal must provide all six file chooser arguments.")
    multiple, directory, save, suggested, output, debug, *terminal = values
    if any(value not in {"0", "1"} for value in (multiple, directory, save, debug)):
        raise ValueError("Invalid file chooser options.")
    env = portal_environment()
    env.pop("YAZI_DIALOG_REQUEST_FILE", None)
    env.pop("YAZI_VSCODE_REQUEST_FILE", None)
    env["YAZI_PORTAL_FILECHOOSER"] = "1"
    command = [*terminal_command(env, terminal), yazi_command(), f"--chooser-file={output}"]
    cwd_file = Path(output + ".1")
    if directory == "1" and save == "0":
        command.append(f"--cwd-file={cwd_file}")
    command.append(suggested)
    try:
        result = subprocess.run(command, env=env)
        if result.returncode == 0 and directory == "1" and save == "0":
            selected = Path(output)
            if (not selected.exists() or selected.stat().st_size == 0) and cwd_file.exists():
                selected.write_bytes(cwd_file.read_bytes())
        return result.returncode
    finally:
        if directory == "1" and save == "0":
            cwd_file.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    requested = sub.add_parser("action", help="Open an action handled by desktop-dialog.yazi")
    requested.add_argument("--request-file", required=True)
    chooser = sub.add_parser("filechooser", help="Receive termfilechooser's wrapper arguments")
    chooser.add_argument("values", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.mode == "action":
            action(args.request_file)
        else:
            return filechooser(args.values)
    except (OSError, ValueError) as error:
        print(f"Yazi: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

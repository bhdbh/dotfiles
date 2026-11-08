#!/usr/bin/python3
"""Check that removing an active keyboard retains a remaining keyboard map."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parent
work = Path(tempfile.mkdtemp(prefix="keyboard-removal-", dir=root))
work.chmod(0o700)
(work / "sway.conf").write_text("xwayland disable\nswaybg_command -\nseat seat0 fallback true\n")
env = dict(os.environ, XDG_RUNTIME_DIR=str(work), WLR_BACKENDS="headless",
           WLR_RENDERER="pixman", WLR_HEADLESS_OUTPUTS="1")
for name in ("SWAYSOCK", "WAYLAND_DISPLAY", "DISPLAY"):
    env.pop(name, None)
keyboards = []
with (work / "sway.log").open("w") as log:
    sway = subprocess.Popen([sys.argv[1], "-c", str(work / "sway.conf")], env=env, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 8
        while not list(work.glob("wayland-*.lock")):
            assert sway.poll() is None, (work / "sway.log").read_text()
            assert time.monotonic() < deadline
            time.sleep(0.02)
        env["WAYLAND_DISPLAY"] = next(work.glob("wayland-*.lock")).name.removesuffix(".lock")
        env["SWAYSOCK"] = str(next(work.glob("sway-ipc.*.sock")))
        def probe():
            return int(subprocess.check_output([str(root / "keyboard-client"), "probe"], env=env, text=True, timeout=3))
        def keyboard(mode="hold"):
            p = subprocess.Popen([str(root / "keyboard-client"), mode], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            keyboards.append(p)
            assert p.stdout.readline().strip() == "READY"
            return p
        first = keyboard()
        before = probe()
        empty = keyboard("empty")
        second = keyboard()
        during = probe()
        second.communicate("quit\n", timeout=3)
        after = probe()
        result = {"before": before, "during": during, "after_removal": after, "artifacts": str(work)}
        if "--code" in sys.argv[2:]:
            code = subprocess.run([sys.executable, str(root / "probe.py"), "--ozone-platform=wayland"], env=env, capture_output=True, text=True, timeout=20)
            (work / "code-probe.log").write_text(code.stdout + code.stderr)
            result["code"] = json.loads(code.stdout.splitlines()[-1])
        first.communicate("quit\n", timeout=3)
        result["without_valid_keyboard"] = probe()
        third = keyboard()
        result["after_new_keyboard"] = probe()
        empty.communicate("quit\n", timeout=3)
        result["after_inactive_removal"] = probe()
        third.communicate("quit\n", timeout=3)
        result["without_keyboards"] = probe()
        (work / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        assert before == during == after == 1, "Remaining keyboard lost its keymap"
        assert result["without_valid_keyboard"] == result["without_keyboards"] == 0
        assert result["after_new_keyboard"] == result["after_inactive_removal"] == 1
        if "code" in result:
            assert result["code"]["survived_10_seconds"] and result["code"]["windows"], result["code"]
    finally:
        for p in keyboards:
            if p.poll() is None:
                p.communicate("quit\n", timeout=3)
        sway.terminate()
        sway.wait(timeout=5)

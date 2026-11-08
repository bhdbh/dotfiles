"""Exercise readiness and fail-closed behavior against the stubbed executable."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time

binary = sys.argv[1]
with tempfile.TemporaryDirectory(prefix="tty-lock-flow-") as temporary:
    base = Path(temporary)
    for mode in ("ready", "setup-failure", "denied", "retry", "return-failure"):
        events = base / mode
        console = base / (mode + ".console")
        env = dict(os.environ, TEST_MODE=mode, TEST_EVENTS=str(events),
                   TEST_CONSOLE=str(console), LC_ALL='pt_BR.UTF-8')
        args = [binary, "-db"] if mode in ("ready", "setup-failure") else [binary, "-b"]
        child = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        result = child.wait(timeout=3)
        observed = events.read_text().splitlines()
        if mode == "ready":
            assert result == 0 and "secure" in observed, observed
            # The detached parent must wait until input and VT switching are
            # secured, yet must return without waiting for password entry.
            assert "release" not in observed, observed
            deadline = time.monotonic() + 3
            while "release" not in events.read_text().splitlines():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert console.read_text() == "Password: \n", repr(console.read_text())
        elif mode == "setup-failure":
            assert result != 0 and "auth" not in observed, observed
        elif mode == "retry":
            assert result == 0 and observed.count("auth") == 3, observed
            assert observed.count("rejected") == 2, observed
            assert "unlock" not in observed[observed.index("lock") + 1:observed.index("reset")], observed
            assert observed.index("release") > max(i for i, event in enumerate(observed) if event == "auth"), observed
            prompts = console.read_text()
            assert prompts == "Password: \n" * 3, repr(prompts)
        elif mode == "return-failure":
            assert result == 0 and observed.count("auth") == 1, observed
            assert observed.index("unlock", observed.index("lock") + 1) < observed.index("release"), observed
            assert console.read_text() == "Password: \n", repr(console.read_text())
        else:
            assert result != 0 and "auth" in observed, observed
            assert "unlock" not in observed[observed.index("lock") + 1:], observed
            assert "release" not in observed, observed
            assert console.read_text() == "Password: \n", repr(console.read_text())
print("5 locker readiness/authentication flow checks passed (stub PAM and VT)")

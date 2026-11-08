"""Exercise the shared launcher, real Yazi UI and portal wrapper in temp dirs."""
import io
import json
import os
from pathlib import Path
import re
import tempfile
import warnings

import pexpect
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    warnings.simplefilter("ignore", UserWarning)
    from pexpect.ANSI import ANSI


HOME = Path.home()
LAUNCHER = HOME / ".local/libexec/yazi-dialog"
WRAPPER = HOME / ".config/xdg-desktop-portal-termfilechooser/yazi-wrapper.sh"
YAZI = HOME / ".cargo/bin/yazi"


with tempfile.TemporaryDirectory(prefix="test-yazi-desktop-dialog-") as tmp:
    root = Path(tmp)
    work = root / "work"
    work.mkdir()
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    config = root / "config/xdg-desktop-portal-termfilechooser"
    config.mkdir(parents=True)
    terminal = root / "foot"
    terminal.write_text('''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["TERMINAL_TRACE"]).write_text(json.dumps({
    "argv": args, "request": os.environ.get("YAZI_DIALOG_REQUEST_FILE"),
    "portal": os.environ.get("YAZI_PORTAL_FILECHOOSER"),
    "legacy": os.environ.get("YAZI_VSCODE_REQUEST_FILE"),
    "marker": os.environ.get("DIALOG_TEST_MARKER"),
}))
while args and args[0].startswith("--"):
    args.pop(0)
os.execv(args[0], args)
''')
    terminal.chmod(0o755)
    (config / "config").write_text(
        f"[filechooser]\nenv=TERMCMD={terminal} --app-id=yazi-filechooser --title=Yazi\n"
        "env=PATH=@HOME@/.cargo/bin:/usr/local/bin:/usr/bin:/bin\n"
        "\tDIALOG_TEST_MARKER=preserved\n")
    env = {
        **os.environ, "TERM": "xterm-256color", "XDG_CURRENT_DESKTOP": "sway",
        "YAZI_CONFIG_HOME": str(HOME / ".config/yazi"),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_RUNTIME_DIR": str(runtime),
        "XDG_DATA_HOME": str(root / "data"), "XDG_STATE_HOME": str(root / "state"),
        "XDG_CACHE_HOME": str(root / "cache"),
    }
    for key in ("YAZI_DIALOG_REQUEST_FILE", "YAZI_VSCODE_REQUEST_FILE", "YAZI_PORTAL_FILECHOOSER"):
        env.pop(key, None)
    children = []
    traces = {}

    def start(command, args, extra=None, size=(14, 76)):
        trace = root / f"terminal-{len(children)}.json"
        child = pexpect.spawn(str(command), list(map(str, args)),
                              env={**env, "TERMINAL_TRACE": str(trace), **(extra or {})},
                              encoding="utf-8", timeout=8, dimensions=size)
        children.append(child)
        child.output = io.StringIO()
        child.logfile_read = child.output
        traces[id(child)] = trace
        child.expect_exact("\x1b[0c")
        child.send("\x1b[?1;2c")
        return child

    def action(kind, targets, legacy=False):
        request = root / f"request-{len(children)}.json"
        request.write_text(json.dumps({"action": kind, "targets": [str(p) for p in targets], "cwd": str(work)}))
        if legacy:
            child = start(YAZI, [work], {"YAZI_VSCODE_REQUEST_FILE": str(request)})
        else:
            child = start(LAUNCHER, ["action", "--request-file", request], {
                "YAZI_PORTAL_FILECHOOSER": "1", "YAZI_VSCODE_REQUEST_FILE": "/stale/request.json",
            })
        child.request_file = request
        return child

    def finished(child, timeout=5):
        child.expect(pexpect.EOF, timeout=timeout)
        child.close()
        assert child.exitstatus == 0, child.output.getvalue()[-1500:]

    def hidden(child):
        assert "UNRELATED_BROWSER_FILE.txt" not in child.output.getvalue()
        assert "PRIVATE_PREVIEW_CONTENT" not in child.output.getvalue()

    def trace(child):
        return json.loads(traces[id(child)].read_text())

    def completed(child, targets):
        finished(child)
        assert all(not os.path.lexists(target) for target in targets)
        # Reconstruct terminal frames: the renderer updates only changed cells,
        # so the visible "100%" may arrive as multiple cursor-addressed writes.
        screen = ANSI(14, 76)
        percentages = []
        for frame in child.output.getvalue().split("\x1b[?2026l"):
            frame = re.sub(r"\x1b(?:\].*?(?:\x07|\x1b\\)|[P_^].*?\x1b\\)", "", frame, flags=re.S)
            frame = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b.",
                           lambda match: match[0] if match[0].startswith("\x1b[")
                           and match[0][-1] in "HfJK" else "", frame)
            screen.write(frame)
            visible = screen.dump()
            if any(title in visible for title in ("Moving to Trash", "Deleting...", "Done")):
                values = re.findall(r"(?<![0-9])([0-9]{1,3})%", visible)
                assert len(values) == 1, visible
                percent = int(values[0])
                assert 0 <= percent <= 100
                if percent == 100:
                    assert "Done" in visible
                rows = [visible[index:index + 76] for index in range(0, len(visible), 76)]
                bar_row = next(index for index, row in enumerate(rows) if "%" in row)
                counter_row = next(index for index, row in enumerate(rows) if re.fullmatch(r"\d+ / \d+", row.strip()))
                assert counter_row == bar_row + 2 and not rows[bar_row + 1].strip(), rows
                percentages.append(percent)
        assert percentages and percentages[0] == 0 and percentages[-1] == 100, percentages
        assert percentages == sorted(percentages), percentages

    try:
        sibling = work / "UNRELATED_BROWSER_FILE.txt"
        sibling.write_text("unrelated")
        target = work / "selected.txt"
        target.write_text("PRIVATE_PREVIEW_CONTENT")
        for answer in ("\x1b", "n", "\x03"):
            child = action("trash", [target])
            child.expect_exact("Trash 1 selected file")
            child.expect_exact(target.name)
            assert target.exists() and not child.request_file.exists()
            command = trace(child)
            assert "--app-id=yazi-confirm" in command["argv"]
            assert "--window-size-chars=76x14" in command["argv"]
            assert command["portal"] is None and command["legacy"] is None
            assert command["marker"] == "preserved"
            child.send(answer)
            finished(child, timeout=1)
            assert target.read_text() == "PRIVATE_PREVIEW_CONTENT"
            assert "Done" not in child.output.getvalue()
            hidden(child)
        print("PASS: shared action launcher; confirmation-only UI; Esc/n/Ctrl+C cancel and close", flush=True)

        second = work / "espaço 'aspas' $literal.txt"
        second.write_text("literal")
        child = action("trash", [target, second])
        child.expect_exact("Trash 2 selected files")
        assert target.exists() and second.exists()
        child.send("y")
        completed(child, [target, second])
        assert not target.exists() and not second.exists()
        for item in (target, second):
            assert (root / "data/Trash/files" / item.name).exists()
        hidden(child)
        print("PASS: multi-selection trash shows 0% to 100% before exit; filenames stay literal", flush=True)

        target.write_text("PRIVATE_PREVIEW_CONTENT")
        child = action("delete", [target])
        child.expect_exact("Permanently delete 1 selected file")
        child.send("\r")
        completed(child, [target])
        assert not target.exists()
        hidden(child)
        print("PASS: permanent deletion confirms, shows progress through 100%, then closes", flush=True)

        target.write_text("PRIVATE_PREVIEW_CONTENT")
        link = work / "link.txt"
        link.symlink_to(target)
        child = action("trash", [link])
        child.expect_exact("Trash 1 selected file")
        child.send("y")
        completed(child, [link])
        assert not link.is_symlink() and target.exists()
        print("PASS: trashing a symlink preserves its target", flush=True)

        tree = work / "directory"
        (tree / "nested").mkdir(parents=True)
        (tree / "nested/content.txt").write_text("directory content")
        companion = work / "companion.txt"
        companion.write_text("companion")
        child = action("trash", [tree, companion])
        child.expect_exact("Trash 2 selected files")
        child.send("y")
        completed(child, [tree, companion])
        assert not tree.exists() and not companion.exists()
        assert (root / "data/Trash/files/directory/nested/content.txt").read_text() == "directory content"
        assert (root / "data/Trash/files/companion.txt").read_text() == "companion"
        print("PASS: a directory and companion finish together before the dialog closes", flush=True)

        child = action("trash", [target, work / "missing.txt"])
        child.expect_exact("Yazi / Dialogs")
        assert "Trash 2 selected files" not in child.output.getvalue()
        assert target.read_text() == "PRIVATE_PREVIEW_CONTENT"
        child.send("\x1b")
        finished(child)
        print("PASS: missing targets show an error and preserve every valid target", flush=True)

        child = action("trash", [target], legacy=True)
        child.expect_exact("Trash 1 selected file")
        child.send("\x1b")
        finished(child)
        assert target.exists()
        print("PASS: previous extension requests remain compatible until VS Code reloads", flush=True)

        child = action("manage", [work])
        child.expect_exact(sibling.name)
        assert "--app-id=yazi-confirm" not in trace(child)["argv"]
        child.send("\x1b")
        finished(child)
        print("PASS: explicit file management keeps the regular browser", flush=True)

        picker = root / "picker"
        picker.mkdir()
        picked = picker / "file.txt"
        picked.write_text("keep")

        def choose(directory=False, save=False, suggested=picked):
            output = root / f"chosen-{len(children)}.txt"
            child = start(WRAPPER, ["0", str(int(directory)), str(int(save)), suggested, output, "0",
                                    terminal, "--app-id=yazi-filechooser", "--title=Yazi"], {
                                        "YAZI_DIALOG_REQUEST_FILE": "/stale/request.json",
                                    }, size=(35, 120))
            child.expect_exact(picked.name)
            assert trace(child)["portal"] == "1" and trace(child)["request"] is None
            return child, output

        for directory, save in ((False, False), (True, False), (False, True)):
            child, output = choose(directory, save, picker if directory else picked)
            child.send("\x1b")
            finished(child)
            assert not output.exists() or not output.read_bytes()
            assert not Path(str(output) + ".1").exists()
        print("PASS: portal open/folder/save all cancel without returning a selection", flush=True)

        child, output = choose()
        child.send("o")
        finished(child)
        assert output.read_text().strip() == str(picked)
        child, output = choose(save=True)
        child.send("o")
        finished(child)
        assert output.read_text().strip() == str(picked) and picked.read_text() == "keep"
        child, output = choose(directory=True, suggested=picker)
        child.send("q")
        finished(child)
        assert output.read_text().strip() == str(picker)
        assert not Path(str(output) + ".1").exists()
        print("PASS: portal open/save return the chosen file; folder acceptance preserves cwd fallback", flush=True)

        child, output = choose()
        child.send("r")
        child.expect_exact("Rename:")
        child.send("unsaved")
        child.send("\x1b")
        try:
            child.expect(pexpect.EOF, timeout=0.15)
            raise AssertionError("First Esc should leave insert mode")
        except pexpect.TIMEOUT:
            pass
        child.send("\x1b")
        finished(child, timeout=1)
        assert picked.read_text() == "keep" and not output.exists()
        print("PASS: double Esc during portal rename closes immediately without changing files", flush=True)

        child = start(YAZI, [work], {"YAZI_DIALOG_REQUEST_FILE": "/already/consumed.json"}, size=(35, 120))
        child.expect_exact(sibling.name)
        child.send("\x1b")
        try:
            child.expect(pexpect.EOF, timeout=0.25)
            raise AssertionError("Standalone Esc must retain the browser")
        except pexpect.TIMEOUT:
            pass
        child.send("q")
        finished(child)
        print("PASS: standalone Yazi and stale request variables retain the normal UI and Esc", flush=True)
    except BaseException:
        if children:
            print(repr(children[-1].output.getvalue()[-4500:]), flush=True)
        raise
    finally:
        for child in children:
            if child.isalive():
                child.terminate(force=True)

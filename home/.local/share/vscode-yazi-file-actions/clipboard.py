"""Capture all Wayland clipboard formats, then let the native helper own them."""
import subprocess
import sys
import os
from pathlib import Path
import tempfile
import shutil


def main():
    directory = Path(tempfile.mkdtemp(prefix="vscode-yazi-clipboard-"))
    try:
        listing = subprocess.run(
            ["/usr/bin/wl-paste", "--list-types"], capture_output=True, timeout=5,
        )
        types = listing.stdout.decode().splitlines()
        for index, mime in enumerate(types):
            content = subprocess.run(
                ["/usr/bin/wl-paste", "--no-newline", "--type", mime],
                capture_output=True, check=True, timeout=5,
            )
            (directory / str(index)).write_bytes(content.stdout)
        (directory / "types").write_text("".join(mime + "\n" for mime in types))
        helper = Path(__file__).with_name("clipboard-owner")
        os.execv(str(helper), [str(helper), str(directory)])
    finally:
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)

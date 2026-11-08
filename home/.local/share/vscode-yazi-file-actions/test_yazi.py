"""Compatibility entry point for the shared portal/action integration checks."""
from pathlib import Path
import runpy

runpy.run_path(str(Path.home() / ".local/share/yazi-desktop-dialog/test_dialogs.py"), run_name="__main__")

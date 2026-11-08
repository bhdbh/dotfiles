#!/usr/bin/python3
"""Configure the installed Bibata cursor for GTK, with a backup."""
import configparser
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from zoneinfo import ZoneInfo


THEME = "Bibata-Material-Noir"
SIZE = 24
SCHEMA = "org.gnome.desktop.interface"


def gsettings(*arguments):
    return subprocess.check_output(
        ["gsettings", *arguments], text=True).strip()


def main():
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    themes = (home / ".icons", home / ".local/share/icons",
              Path("/usr/local/share/icons"), Path("/usr/share/icons"))
    if not any((root / THEME / "cursors/left_ptr").is_file() for root in themes):
        raise SystemExit(f"Cursor theme is not installed: {THEME}")

    values = {"cursor-theme": f"'{THEME}'", "cursor-size": str(SIZE)}
    for key in values:
        if gsettings("writable", SCHEMA, key) != "true":
            raise SystemExit(f"GSettings key is not writable: {key}")
    previous = {key: gsettings("get", SCHEMA, key) for key in values}
    prepared = []
    for version in (3, 4):
        relative = Path(f"gtk-{version}.0/settings.ini")
        target = config / relative
        settings = configparser.ConfigParser(interpolation=None)
        settings.optionxform = str
        if target.exists():
            with target.open() as stream:
                settings.read_file(stream)
        if not settings.has_section("Settings"):
            settings.add_section("Settings")
        settings["Settings"]["gtk-cursor-theme-name"] = THEME
        settings["Settings"]["gtk-cursor-theme-size"] = str(SIZE)
        prepared.append((relative, target, settings))

    stamp = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y%m%dT%H%M%S%z")
    backup = config / "waybar/backups" / f"cursor-theme-{stamp}"
    backup.mkdir(parents=True)
    manifest = {"gsettings": previous, "files": {}}
    for relative, target, _ in prepared:
        manifest["files"][str(relative)] = target.exists()
        if target.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    for _, target, settings in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".installing")
        with temporary.open("w") as stream:
            settings.write(stream, space_around_delimiters=False)
        temporary.chmod(target.stat().st_mode & 0o777 if target.exists() else 0o644)
        temporary.replace(target)
    for key, value in values.items():
        gsettings("set", SCHEMA, key, value)
        if gsettings("get", SCHEMA, key) != value:
            raise RuntimeError(f"GSettings did not retain {key}")
    print(f"GTK cursor: {THEME}, {SIZE}px")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()

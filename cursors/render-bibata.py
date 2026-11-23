#!/usr/bin/env python3
"""Recolor Bibata's Modern SVGs and render the PNG inputs for clickgen."""

from pathlib import Path
import re
import sys

import cairo
import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg


source, output = map(Path, sys.argv[1:])
colors = {
    "#00ff00": "#52525b",  # Base: zinc-600.
    "#0000ff": "#a1a1aa",  # Outline: zinc-400.
    "#ff0000": "#52525b",  # Watch background: zinc-600.
}

# Walk each entry explicitly: Path.rglob does not follow directory symlinks
# in Bibata's svg/modern tree on every Python version.
count = 0
for entry in sorted(source.iterdir()):
    files = sorted(entry.glob("*.svg")) if entry.is_dir() else [entry]
    for svg in files:
        if svg.suffix != ".svg":
            continue
        # The upstream manifest expects animation frames beside static PNGs.
        target = output / svg.with_suffix(".png").name
        target.parent.mkdir(parents=True, exist_ok=True)
        text = re.sub(
            r"#(?:00ff00|0000ff|ff0000)\b",
            lambda match: colors[match.group().lower()],
            svg.read_text(),
            flags=re.IGNORECASE,
        )
        handle = Rsvg.Handle.new_from_data(text.encode())
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 256, 256)
        viewport = Rsvg.Rectangle()
        viewport.x = viewport.y = 0
        viewport.width = viewport.height = 256
        if not handle.render_document(cairo.Context(surface), viewport):
            raise RuntimeError(f"Could not render {svg}")
        surface.write_to_png(str(target))
        count += 1

print(f"Rendered {count} Bibata Modern frames.")

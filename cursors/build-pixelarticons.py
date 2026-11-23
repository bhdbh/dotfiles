#!/usr/bin/env python3
"""Download the free Pixelarticons assets and package their native PNGs as XCursor."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
from urllib.request import urlopen

import cairo


def download_asset(item):
    filename, digest = item
    path = source_dir / filename
    if path.is_file() and sha256(path.read_bytes()).hexdigest() == digest:
        return
    for attempt in range(3):
        try:
            with urlopen(manifest["base_url"] + filename, timeout=30) as response:
                data = response.read()
            if sha256(data).hexdigest() != digest:
                raise ValueError(f"SHA-256 diferente do manifesto: {filename}")
            path.write_bytes(data)
            return
        except (OSError, ValueError):
            if attempt == 2:
                raise


def xcursor(png, hotspot):
    surface = cairo.ImageSurface.create_from_png(str(png))
    surface.flush()
    width, height = surface.get_width(), surface.get_height()
    xhot, yhot = hotspot
    if surface.get_format() != cairo.FORMAT_ARGB32:
        raise ValueError(f"PNG sem canal alpha: {png}")
    if not (0 <= xhot < width and 0 <= yhot < height):
        raise ValueError(f"Hotspot fora da imagem: {png}")
    # Cairo supplies premultiplied ARGB pixels in native byte order. Keep the
    # original 32 px image and sample every second pixel for a crisp 16 px image.
    # The source's 2x2 blocks become 1x1; no intermediate colors are introduced.
    data = surface.get_data()
    image_type = 0xfffd0002
    chunks = []
    for step in (2, 1):
        nominal = 32 // step
        scaled_width, scaled_height = (width + step - 1) // step, (height + step - 1) // step
        pixels = bytearray()
        for y in range(scaled_height):
            for x in range(scaled_width):
                offset = y * step * surface.get_stride() + x * step * 4
                pixel = bytes(data[offset:offset + 4])
                pixels.extend(pixel if sys.byteorder == "little" else pixel[::-1])
        chunk = (struct.pack("<9I", 36, image_type, nominal, 1,
                             scaled_width, scaled_height, xhot // step, yhot // step, 0)
                 + pixels)
        chunks.append((nominal, chunk))
    offset = 16 + 12 * len(chunks)
    toc = bytearray()
    for nominal, chunk in chunks:
        toc.extend(struct.pack("<III", image_type, nominal, offset))
        offset += len(chunk)
    return (struct.pack("<4sIII", b"Xcur", 16, 0x10000, len(chunks))
            + toc + b"".join(chunk for nominal, chunk in chunks))


manifest = json.loads((Path(__file__).parent / "pixelarticons/manifest.json").read_text())
cache_dir, out_dir = map(Path, sys.argv[1:])
source_dir = cache_dir / "source"
source_dir.mkdir(parents=True, exist_ok=True)
with ThreadPoolExecutor(max_workers=8) as pool:
    list(pool.map(download_asset, manifest["files"].items()))

for variant in ("Light", "Dark"):
    theme = "Pixelarticons-" + variant
    theme_dir = out_dir / theme
    cursor_dir = theme_dir / "cursors"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    for name, settings in manifest["cursors"].items():
        png_name = name + ".png"
        if variant == "Dark" and name + "-dark.png" in manifest["files"]:
            png_name = name + "-dark.png"
        (cursor_dir / name).write_bytes(xcursor(source_dir / png_name, settings["hotspot"]))
        for alias in settings["aliases"]:
            link = cursor_dir / alias
            if link.is_symlink():
                link.unlink()
            link.symlink_to(name)
    (theme_dir / "index.theme").write_text(
        f"[Icon Theme]\nName={theme}\n"
        f"Comment=Free pixel cursors by {manifest['author']}, crisp 16/32 px.\n"
        "Inherits=Bibata-Modern-Zinc,Adwaita\n")
    (theme_dir / "SOURCE.txt").write_text(
        f"{manifest['website']}\nAuthor: {manifest['author']}\n"
        f"Free PNG/SVG downloads retrieved {manifest['downloaded']}.\n"
        "Static XCursor conversion: original 32 px PNGs and nearest-neighbor 16 px images.\n"
        "Dark variants are used where offered; shared cursors retain their original colors.\n")
    (theme_dir / "sources.sha256").write_text("".join(
        f"{digest}  {name}\n" for name, digest in manifest["files"].items()))
    print(f"Built {theme}: {len(manifest['cursors'])} cursors, crisp 16/32 px.")

#!/usr/bin/env python3
"""Render the Plymouth prompt from Fedora's eurlatgr PSF1 console font."""

import argparse
import gzip
from pathlib import Path
import struct
import zlib

PROMPT = "Disk Password: "
COLOR = bytes((170, 170, 170))  # Linux console's normal foreground / VGA 7.


def load_font(path):
    data = path.read_bytes()
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    if len(data) < 4 or data[:2] != b"\x36\x04" or data[3] != 16:
        raise ValueError("Expected an 8x16 PSF1 console font (eurlatgr).")
    mode, height = data[2:4]
    count = 512 if mode & 1 else 256
    end = 4 + count * height
    if len(data) < end:
        raise ValueError("Truncated PSF1 bitmap data.")
    glyphs = [data[4 + i * height:4 + (i + 1) * height] for i in range(count)]
    mapping = {}
    if mode & 6:
        index, sequence = 0, False
        for (codepoint,) in struct.iter_unpack("<H", data[end:]):
            if codepoint == 0xFFFF:
                index, sequence = index + 1, False
            elif codepoint == 0xFFFE:
                sequence = True
            elif not sequence and index < count:
                mapping[chr(codepoint)] = glyphs[index]
    else:
        mapping = {chr(i): glyph for i, glyph in enumerate(glyphs[:128])}
    return mapping


def write_png(path, width, height, pixels):
    def chunk(kind, content):
        checksum = zlib.crc32(kind + content) & 0xFFFFFFFF
        return struct.pack("!I", len(content)) + kind + content + struct.pack("!I", checksum)

    rows = b"".join(b"\0" + pixels[y * width * 4:(y + 1) * width * 4]
                    for y in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def render(font, output):
    glyphs = [font[character] for character in PROMPT]
    pixels = bytearray()
    for row in range(16):
        for glyph in glyphs:
            for bit in range(7, -1, -1):
                pixels.extend(COLOR + b"\xff" if glyph[row] & (1 << bit) else b"\0" * 4)
    output.mkdir(parents=True, exist_ok=True)
    write_png(output / "disk-password.png", len(PROMPT) * 8, 16, pixels)
    cursor = b"\0" * (8 * 14 * 4) + (COLOR + b"\xff") * (8 * 2)
    write_png(output / "cursor.png", 8, 16, cursor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path,
                        default=Path("/usr/lib/kbd/consolefonts/eurlatgr.psfu.gz"))
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    render(load_font(args.font), args.output_dir)


if __name__ == "__main__":
    main()

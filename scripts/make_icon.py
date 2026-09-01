#!/usr/bin/env python3
"""Render assets/icon.svg to a multi-size .ico + .png brand assets.

Uses cairosvg to rasterize and Pillow to assemble the icon container.
Writes:
  assets/icon.ico     - Windows icon (16,24,32,48,64,128,256) - for exe/installer
  assets/icon-256.png - 256px PNG (apps / GitHub)
"""
import os
import struct

import cairosvg
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join(ROOT, "assets", "icon.svg")
ICO_OUT = os.path.join(ROOT, "assets", "icon.ico")
PNG_OUT = os.path.join(ROOT, "assets", "icon-256.png")

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render_png(size):
    return cairosvg.svg2png(url=SVG, output_width=size, output_height=size)


def build_ico(png_buffers):
    """Assemble a PNG-compressed ICO (valid on Windows 7+)."""
    entries = []
    offset = 6 + 16 * len(png_buffers)
    for size, buf in png_buffers:
        entries.append((size, buf, offset))
        offset += len(buf)

    header = struct.pack("<HHH", 0, 1, len(png_buffers))
    directory = b""
    images = b""
    for size, buf, off in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(buf), off)
        images += buf
    return header + directory + images


def main():
    pngs = []
    for s in SIZES:
        data = render_png(s)
        pngs.append((s, data))
        print(f"rendered {s}x{s} ({len(data)} bytes)")

    # icon-256.png direct from SVG
    big = render_png(256)
    with open(PNG_OUT, "wb") as f:
        f.write(big)
    print(f"wrote {PNG_OUT}")

    with open(ICO_OUT, "wb") as f:
        f.write(build_ico(pngs))
    print(f"wrote {ICO_OUT}")

    # sanity check: Pillow can read the ico
    im = Image.open(ICO_OUT)
    print("validated ICO:", im.format, im.size)


if __name__ == "__main__":
    main()

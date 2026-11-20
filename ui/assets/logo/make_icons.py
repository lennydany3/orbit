"""Draws Orbit's icons from the geometry in orbit-dot-sphere.svg (the refined master), so the files the program embeds can be
remade at any time:

    python ui/assets/logo/make_icons.py            (needs numpy)

writes:
    orbit-icon-256.png    the APP TILE: a white rounded square with a dashed black ring and the satellite. Window, taskbar, Alt+Tab.
    orbit-tray-32.rgba    the TASKBAR / 16 mark (a dashed ring and one dot on white), 32 x 32 straight-alpha RGBA rows. The tray icon.
    ../../src/logo_dots.rs   every dot of the primary mark, read from the SVG's `orbit-mark` group (x, y, radius, opacity).
The tile and tray numbers below are copied from the SVG's last row; if they change there, change them here.
"""
import math
import re
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

SS = 4                                      # samples per pixel side, for smooth edges

# APP TILE: a 155 x 155 square with 32 px corners; a dashed ring (radius, stroke, dash, gap) and a dot, from the tile's centre.
TILE = dict(box=155.0, corner=32.0, ring=(39.0, 8.0, 5.0, 5.0), dots=[(33, -29, 9)])
# TASKBAR / 16: a 24 x 24 square, a dashed ring and one dot.
SMALL = dict(box=24.0, corner=0.0, ring=(7.0, 2.0, 2.0, 2.0), dots=[(6, -6, 2.5)])


def coverage(size: int, draw) -> np.ndarray:
    """How much of each pixel `draw` covers, 0 to 1. `draw(x, y)` gets sample points in 0..1 units and returns a bool mask."""
    n = size * SS
    axis = (np.arange(n) + 0.5) / n
    x, y = np.meshgrid(axis, axis)
    return draw(x, y).reshape(size, SS, size, SS).mean(axis=(1, 3))


def mark(size, box, corner, ring, dots):
    """RGBA of a white shape with black ink on it. `box` is the shape's side in design units."""
    def shape(x, y):
        if corner <= 0:
            return np.ones_like(x, dtype=bool)
        r = corner / box
        dx = np.maximum(np.abs(x - 0.5) - (0.5 - r), 0)
        dy = np.maximum(np.abs(y - 0.5) - (0.5 - r), 0)
        return dx * dx + dy * dy <= r * r

    def ink(x, y):
        px, py = (x - 0.5) * box, (y - 0.5) * box              # design units from the centre, y down
        radius, stroke, dash, gap = ring
        on_ring = np.abs(np.hypot(px, py) - radius) <= stroke / 2
        # SVG dashes run clockwise from three o'clock along the circle's length, starting with a dash
        along = (np.arctan2(py, px) % (2 * math.pi)) * radius
        hit = on_ring & ((along % (dash + gap)) < dash)
        for cx, cy, r in dots:
            hit |= np.hypot(px - cx, py - cy) <= r
        return hit

    alpha = coverage(size, shape)
    black = coverage(size, lambda x, y: ink(x, y) & shape(x, y))
    out = np.zeros((size, size, 4), dtype=np.float64)
    out[..., :3] = ((alpha - black) / np.maximum(alpha, 1e-9))[..., None]   # white, darkened by the share the ink covers
    out[..., 3] = alpha
    return (out * 255 + 0.5).astype(np.uint8)


def png(rgba: np.ndarray) -> bytes:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def primary_dots(svg: str):
    """(x, y, r, opacity) of every circle in the `orbit-mark` group, with the opacity of its group (or its own) applied."""
    start = svg.index('<g id="orbit-mark"')
    body = svg[start:svg.index("</g>\n  <g fill", start)]       # up to the end of the mark's own group
    dots, group = [], 1.0
    for token in re.finditer(r'<g(?: opacity="([\d.]+)")?[^>]*>|</g>|<circle ([^>]*)/>', body):
        if token.group(0).startswith("<g"):
            group = float(token.group(1)) if token.group(1) else 1.0
        elif token.group(0) == "</g>":
            group = 1.0
        else:
            a = dict(re.findall(r'(\w+)="([^"]*)"', token.group(2)))
            dots.append((float(a["cx"]), float(a["cy"]), float(a["r"]), group * float(a.get("opacity", 1))))
    return dots


def rust_float(v: float) -> str:
    text = f"{v:g}"
    return text if "." in text else text + ".0"


def dots_source(dots) -> str:
    rows = "".join("    (" + ", ".join(rust_float(v) for v in dot) + "),\n" for dot in dots)
    return (
        "// Written by assets/logo/make_icons.py from the SVG's `orbit-mark` group. Do not edit by hand.\n\n"
        f"/// (x, y, radius, opacity) of each dot of the primary mark, in design units from its centre, y down.\n"
        f"pub const DOTS: [(f32, f32, f32, f32); {len(dots)}] = [\n{rows}];\n"
    )


def main() -> None:
    here = Path(__file__).parent
    (here / "orbit-icon-256.png").write_bytes(png(mark(256, **TILE)))
    (here / "orbit-tray-32.rgba").write_bytes(mark(32, **SMALL).tobytes())
    dots = primary_dots((here / "orbit-dot-sphere.svg").read_text(encoding="utf-8"))
    (here / ".." / ".." / "src" / "logo_dots.rs").write_text(dots_source(dots), encoding="utf-8", newline="\n")
    print(f"wrote the tile, the tray mark and {len(dots)} dots")


if __name__ == "__main__":
    sys.exit(main())

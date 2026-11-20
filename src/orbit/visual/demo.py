"""See the animations.

    python -m orbit.visual.demo                       a short tour on your own screen: the pointer glides, clicks, and the
                                                      "working" pill shows (nothing is clicked for real)
    python -m orbit.visual.demo --preview out.png     draw the animations onto a picture instead (no screen needed): a mock search
                                                      box on a light page and on a dark one, as in the README
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
import zlib

import numpy as np

from . import motion, sprites, textmask


# ---------------------------------------------------------------------------------------------------- a picture
def _png(path: str, rgb: np.ndarray) -> None:
    height, width = rgb.shape[:2]
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _paste(canvas: np.ndarray, bgra: np.ndarray, left: int, top: int) -> None:
    """Lay a premultiplied BGRA picture onto an RGB canvas at (left, top)."""
    h, w = bgra.shape[:2]
    region = canvas[top:top + h, left:left + w]
    alpha = bgra[..., 3:4].astype(np.float32) / 255.0
    rgb = bgra[..., 2::-1].astype(np.float32)                      # B,G,R -> R,G,B
    region[...] = np.clip(rgb + region.astype(np.float32) * (1.0 - alpha), 0, 255).astype(np.uint8)


def _mock_page(dark: bool, width: int, height: int) -> np.ndarray:
    """A plain page with a search box holding "tamil news live" (an illustration, not a real site)."""
    page = np.full((height, width, 3), (24, 25, 31) if dark else (247, 248, 250), dtype=np.uint8)
    box_w, box_h = 560, 58
    left, top = (width - box_w) // 2, 70
    x, y = sprites._grid(height, width)
    box = sprites._rounded_rect_sdf_at(x, y, left, top, box_w, box_h, box_h / 2)
    fill = (45, 46, 54) if dark else (255, 255, 255)
    edge = (84, 86, 98) if dark else (214, 217, 224)
    canvas = sprites._Canvas(height, width)
    canvas.over((0, 0, 0), (0.10 if not dark else 0.35) * np.exp(-np.maximum(sprites._rounded_rect_sdf_at(x, y + 4, left, top, box_w, box_h, box_h / 2), 0) / 7))
    canvas.over(edge, 0.5 - box)
    canvas.over(fill, 0.5 - (box + 1.2))
    _paste(page, canvas.to_bgra(), 0, 0)
    mask = textmask.render_text("tamil news live", 22)
    ink = np.array((235, 236, 242) if dark else (32, 33, 38), dtype=np.float32)
    ty, tx = top + (box_h - mask.shape[0]) // 2, left + 34
    region = page[ty:ty + mask.shape[0], tx:tx + mask.shape[1]].astype(np.float32)
    page[ty:ty + mask.shape[0], tx:tx + mask.shape[1]] = (region + (ink - region) * mask[..., None]).astype(np.uint8)
    return page


def preview(path: str) -> None:
    """The pointer arriving at a search box with its click ripple, and the working pill: on a light page and on a dark one."""
    width, height = 760, 330
    pages = []
    for dark in (False, True):
        page = _mock_page(dark, width, height)
        cx, cy = width // 2 + 150, 70 + 29
        ripple = sprites.ripple_frame(0.20)
        half = sprites.ripple_size(1.0) // 2
        _paste(page, ripple, cx - half, cy - half)
        pointer, pad = sprites.cursor_sprite(0.82)
        _paste(page, pointer, cx - pad, cy - pad)
        pill = sprites.Pill(textmask.render_text("Typing…", 14), 1.0)
        _paste(page, pill.frame(0.0), (width - pill.width) // 2, height - pill.height - 40)
        pages.append(page)
    _png(path, np.concatenate(pages, axis=1))


# ---------------------------------------------------------------------------------------------------- on your screen
def tour() -> None:
    from .. import config, visual

    config.VISUALS = True
    if visual._get() is None:
        print("The animations could not start here (no desktop?).")
        return
    import ctypes

    width, height = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    print("Watch your screen: the pointer glides, presses, and the pill shows. (Nothing is clicked for real.)")
    for fx, fy in ((0.30, 0.35), (0.72, 0.30), (0.55, 0.62)):
        visual.click_at(width * fx, height * fy)
        time.sleep(0.7)
    with visual.working("Searching Google…", None):
        time.sleep(2.4)
    visual.grab(width * 0.40, height * 0.20)                       # holding a title bar and carrying it across the screen
    for step in range(31):
        visual.follow(width * (0.40 + 0.30 * motion.ease_in_out(step / 30)), height * 0.20)
        time.sleep(0.02)
    visual.release()
    time.sleep(1.2)


def main() -> int:
    parser = argparse.ArgumentParser(description="See Orbit's on-screen animations")
    parser.add_argument("--preview", metavar="OUT.png", help="draw the animations onto a picture instead of your screen")
    args = parser.parse_args()
    if args.preview:
        preview(args.preview)
        print(f"saved {args.preview}")
    else:
        tour()
    return 0


if __name__ == "__main__":
    sys.exit(main())

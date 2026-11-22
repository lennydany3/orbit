"""Draws the installer's pictures from Orbit's own dot sphere, so they can be remade at any time:

    python installer/make_wizard_images.py            (needs numpy)

writes, in installer/wizard/ (24-bit BMP, the only format Inno Setup's wizard takes):
    welcome-164x314.bmp, welcome-328x628.bmp   the tall picture on the Welcome and Finished pages (100% and 200% screens)
    small-55.bmp, small-110.bmp                the small mark at the top right of the other pages

The look matches the app: near-black ground, the white dot sphere, a violet glow behind it (#8B4FD0, the app's accent).
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
DOTS_RS = HERE.parent / "ui" / "src" / "logo_dots.rs"
BG_TOP, BG_BOTTOM = np.array([6, 6, 7]), np.array([22, 12, 38])
ACCENT = np.array([139, 79, 208])
SPAN = 444.0                                              # the mark's width in design units (see ui/src/logo.rs)


def dots() -> list[tuple[float, float, float, float]]:
    text = DOTS_RS.read_text(encoding="utf-8")
    rows = re.findall(r"\(\s*(-?[\d.]+),\s*(-?[\d.]+),\s*(-?[\d.]+),\s*(-?[\d.]+)\s*\)", text)
    return [tuple(map(float, row)) for row in rows]


def background(w: int, h: int, glow_at: tuple[float, float], glow_r: float, strength: float, flat: bool = False) -> np.ndarray:
    """A vertical dark gradient with a soft violet glow centred at `glow_at` (pixels)."""
    t = (np.arange(h) / max(h - 1, 1))[:, None, None] * (0 if flat else 1)
    img = BG_TOP * (1 - t) + BG_BOTTOM * t
    img = np.broadcast_to(img, (h, w, 3)).copy()
    y, x = np.mgrid[0:h, 0:w]
    d = np.hypot(x - glow_at[0], y - glow_at[1]) / glow_r
    glow = np.exp(-d * d * 2.2)[..., None] * strength
    return img * (1 - glow) + ACCENT * glow


def disc(img: np.ndarray, cx: float, cy: float, r: float, colour: np.ndarray, alpha: float) -> None:
    """An anti-aliased filled circle blended onto `img` (coverage from a 4 x 4 grid per pixel)."""
    h, w, _ = img.shape
    x0, x1 = max(int(cx - r - 1), 0), min(int(cx + r + 2), w)
    y0, y1 = max(int(cy - r - 1), 0), min(int(cy + r + 2), h)
    if x0 >= x1 or y0 >= y1:
        return
    sub = (np.arange(4) + 0.5) / 4
    xs = (np.arange(x0, x1)[:, None] + sub[None, :]).reshape(-1)
    ys = (np.arange(y0, y1)[:, None] + sub[None, :]).reshape(-1)
    inside = (xs[None, :] - cx) ** 2 + (ys[:, None] - cy) ** 2 <= r * r
    cover = inside.reshape(y1 - y0, 4, x1 - x0, 4).mean(axis=(1, 3))[..., None] * alpha
    img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - cover) + colour * cover


def sphere(img: np.ndarray, cx: float, cy: float, size: float) -> None:
    k = size / SPAN
    for x, y, r, opacity in dots():
        disc(img, cx + x * k, cy + y * k, max(r * k, 0.6), np.array([245, 245, 250]), opacity)


def specks(img: np.ndarray, seed: int, count: int) -> None:
    """A few faint dots in the background, like the particles around the sphere in the app."""
    rng = np.random.default_rng(seed)
    h, w, _ = img.shape
    for _ in range(count):
        disc(img, rng.uniform(0, w), rng.uniform(0, h), rng.uniform(0.5, 1.3) * w / 164, np.array([200, 180, 240]), rng.uniform(0.12, 0.4))


def bmp(img: np.ndarray) -> bytes:
    h, w, _ = img.shape
    rgb = np.clip(img + 0.5, 0, 255).astype(np.uint8)[::-1, :, ::-1]            # BMP rows run bottom up, in BGR order
    pad = (-w * 3) % 4
    rows = b"".join(rgb[r].tobytes() + b"\x00" * pad for r in range(h))
    head = b"BM" + struct.pack("<IHHI", 54 + len(rows), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(rows), 2835, 2835, 0, 0)
    return head + info + rows


def welcome(scale: int) -> bytes:
    w, h = 164 * scale, 314 * scale
    img = background(w, h, (w / 2, h * 0.36), w * 0.95, 0.42)
    specks(img, 7, 46 * scale)
    sphere(img, w / 2, h * 0.36, w * 0.68)
    return bmp(img)


def small(size: int) -> bytes:
    img = background(size, size, (size / 2, size / 2), size * 0.75, 0.5, flat=True)
    sphere(img, size / 2, size / 2, size * 0.8)
    return bmp(img)


def main() -> None:
    out = HERE / "wizard"
    out.mkdir(exist_ok=True)
    (out / "welcome-164x314.bmp").write_bytes(welcome(1))
    (out / "welcome-328x628.bmp").write_bytes(welcome(2))
    (out / "small-55.bmp").write_bytes(small(55))
    (out / "small-110.bmp").write_bytes(small(110))
    print(f"wrote 4 pictures to {out}")


if __name__ == "__main__":
    main()

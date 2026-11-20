"""Drawing for the on-screen animations, with numpy only: Orbit's pointer, the click ripple, and the "working" pill.

Everything is drawn from signed-distance fields, so edges are anti-aliased at any size. Pictures come out as premultiplied
BGRA (the format a Windows layered window wants), as uint8 arrays of shape (height, width, 4).
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from . import motion

ACCENT = (104, 112, 255)           # Orbit's blue: visible on a white page and on a dark one
INK = (16, 16, 22)
WHITE = (255, 255, 255)

# The pointer: the usual arrow shape, drawn a little bigger than the system one so a person can follow it.
_ARROW = [(0.0, 0.0), (0.0, 16.5), (4.2, 12.6), (7.0, 19.0), (9.6, 17.9), (6.9, 11.6), (12.2, 11.6)]
_ARROW_SIZE = 1.28                 # extra size on top of the DPI scale
_PAD = 15                          # room round the arrow for its glow and shadow, in pixels at scale 1


def _to_bgra(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Straight float RGB (0..255) and alpha (0..1) -> premultiplied BGRA uint8."""
    out = np.empty(alpha.shape + (4,), dtype=np.float32)
    out[..., 0] = rgb[..., 2] * alpha
    out[..., 1] = rgb[..., 1] * alpha
    out[..., 2] = rgb[..., 0] * alpha
    out[..., 3] = alpha * 255.0
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


class _Canvas:
    """Float premultiplied RGBA (0..1) that layers are composited onto, back to front."""

    def __init__(self, h: int, w: int):
        self.px = np.zeros((h, w, 4), dtype=np.float32)

    def over(self, color: tuple[int, int, int], alpha: np.ndarray) -> None:
        a = np.clip(alpha, 0.0, 1.0)[..., None]
        rgb = np.array(color, dtype=np.float32) / 255.0
        self.px[..., :3] = rgb * a + self.px[..., :3] * (1 - a)
        self.px[..., 3:] = a + self.px[..., 3:] * (1 - a)

    def to_bgra(self, opacity: float = 1.0) -> np.ndarray:
        px = self.px * float(opacity)
        out = np.empty(px.shape, dtype=np.float32)
        out[..., 0], out[..., 1], out[..., 2], out[..., 3] = px[..., 2], px[..., 1], px[..., 0], px[..., 3]
        return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _polygon_sdf(x: np.ndarray, y: np.ndarray, pts: list[tuple[float, float]]) -> np.ndarray:
    """Signed distance in pixels from each point to a polygon: negative inside, positive outside."""
    n = len(pts)
    d2 = np.full(x.shape, np.inf, dtype=np.float32)
    inside = np.zeros(x.shape, dtype=bool)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        t = np.clip(((x - x0) * ex + (y - y0) * ey) / (ex * ex + ey * ey), 0.0, 1.0)
        d2 = np.minimum(d2, (x - (x0 + t * ex)) ** 2 + (y - (y0 + t * ey)) ** 2)
        crosses = (y0 > y) != (y1 > y)
        if ey != 0:
            inside ^= crosses & (x < x0 + ex * (y - y0) / ey)
    d = np.sqrt(d2)
    return np.where(inside, -d, d)


def _grid(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    return xs + 0.5, ys + 0.5


# ---------------------------------------------------------------------------------------------------- the pointer
def cursor_size(scale: float) -> tuple[int, int, int]:
    """(width, height, pad) of the pointer's picture at this scale. The arrow's tip is at (pad, pad)."""
    s = scale * _ARROW_SIZE
    pad = int(math.ceil(_PAD * scale))
    return int(math.ceil(13 * s)) + 2 * pad, int(math.ceil(20 * s)) + 2 * pad, pad


@lru_cache(maxsize=64)
def _cursor_cached(scale_key: int) -> tuple[np.ndarray, int]:
    scale = scale_key / 100.0
    w, h, pad = cursor_size(scale)
    s = scale * _ARROW_SIZE
    pts = [(pad + px * s, pad + py * s) for px, py in _ARROW]
    x, y = _grid(h, w)
    sd = _polygon_sdf(x, y, pts)
    shifted = _polygon_sdf(x - 1.6 * scale, y - 2.8 * scale, pts)          # the same shape, down and to the right: the shadow
    canvas = _Canvas(h, w)
    canvas.over((0, 0, 0), 0.34 * np.exp(-np.maximum(shifted, 0) / (2.6 * scale)))                 # shadow
    canvas.over(ACCENT, 0.42 * np.exp(-np.maximum(sd, 0) / (4.2 * scale)) * (sd > -1))             # glow
    canvas.over(INK, 0.5 - (sd - 1.35 * scale))                                                    # outline
    canvas.over(WHITE, 0.5 - sd)                                                                   # fill
    return canvas.to_bgra(), pad


def quantize_scale(scale: float) -> float:
    """Pointers are drawn (and cached) in steps of 2%: the scale that is really used for `scale`."""
    return int(round(max(0.3, scale) * 50)) * 2 / 100.0


def cursor_sprite(scale: float = 1.0, opacity: float = 1.0) -> tuple[np.ndarray, int]:
    """(premultiplied BGRA picture of Orbit's pointer, pad): the arrow's tip is `pad` pixels in from the top-left corner."""
    sprite, pad = _cursor_cached(int(round(quantize_scale(scale) * 100)))
    if opacity >= 0.999:
        return sprite, pad
    return (sprite.astype(np.float32) * opacity + 0.5).astype(np.uint8), pad


# ---------------------------------------------------------------------------------------------------- the click ripple
def ripple_size(scale: float) -> int:
    return int(round(150 * scale)) // 2 * 2


@lru_cache(maxsize=8)
def _distance_grid(size: int) -> np.ndarray:
    x, y = _grid(size, size)
    return np.hypot(x - size / 2, y - size / 2)


def ripple_frame(t: float, scale: float = 1.0) -> np.ndarray | None:
    """The ripple `t` seconds after a click, as a picture centred on the click; None once it is over."""
    rings = motion.ripple_rings(t)
    if not rings:
        return None
    size = ripple_size(scale)
    dist = _distance_grid(size)
    canvas = _Canvas(size, size)
    full = size / 2 - 3 * scale
    for share, opacity in rings:
        radius = share * full
        width = (1.7 + 2.4 * (1 - share)) * scale
        ring = np.clip(1.0 - np.abs(dist - radius) / width, 0.0, 1.0) ** 1.4
        canvas.over(ACCENT, ring * opacity * 0.95)
        canvas.over(ACCENT, (dist < radius) * 0.16 * opacity)                 # a faint wash inside
    rim = np.clip(1.0 - np.abs(dist - rings[0][0] * full) / (1.1 * scale), 0.0, 1.0)
    canvas.over(WHITE, rim * rings[0][1] * 0.55)                                # a thin bright edge so it reads on dark pages
    return canvas.to_bgra()


# ---------------------------------------------------------------------------------------------------- the "working" pill
class Pill:
    """A small dark pill with a turning dot ring and a label: "Searching Google…". Built once per label; each frame only
    the dots change."""

    def __init__(self, label_mask: np.ndarray, scale: float = 1.0):
        self.scale = scale
        s = scale
        self.pad = int(round(14 * s))
        self.body_h = int(round(38 * s))
        text_h, text_w = label_mask.shape
        self.spin_w = int(round(34 * s))
        self.body_w = self.spin_w + text_w + int(round(18 * s))
        self.width, self.height = self.body_w + 2 * self.pad, self.body_h + 2 * self.pad
        canvas = _Canvas(self.height, self.width)
        x, y = _grid(self.height, self.width)
        full = _rounded_rect_sdf_at(x, y, self.pad, self.pad, self.body_w, self.body_h, self.body_h / 2)
        shadow = _rounded_rect_sdf_at(x, y + 3 * s, self.pad, self.pad, self.body_w, self.body_h, self.body_h / 2)
        canvas.over((0, 0, 0), 0.38 * np.exp(-np.maximum(shadow, 0) / (5.0 * s)))
        canvas.over((20, 20, 27), (0.5 - full) * 0.93)                                      # the body
        canvas.over(WHITE, np.clip(0.5 - np.abs(full + 0.5), 0, 1) * 0.18)                  # a hairline border
        ty = self.pad + (self.body_h - text_h) // 2
        tx = self.pad + self.spin_w
        mask = np.zeros((self.height, self.width), dtype=np.float32)
        mask[ty:ty + text_h, tx:tx + text_w] = label_mask
        canvas.over((244, 244, 248), mask)
        self._base = canvas.px
        self._cx = self.pad + self.spin_w / 2 + 2 * s
        self._cy = self.pad + self.body_h / 2

    def frame(self, phase: float, opacity: float = 1.0) -> np.ndarray:
        canvas = _Canvas(self.height, self.width)
        canvas.px = self._base.copy()
        ring, dot = 8.6 * self.scale, 2.5 * self.scale
        x0, y0 = int(self._cx - ring - dot - 2), int(self._cy - ring - dot - 2)
        x1, y1 = int(self._cx + ring + dot + 3), int(self._cy + ring + dot + 3)
        xs, ys = _grid(self.height, self.width)
        sub = (slice(y0, y1), slice(x0, x1))
        layer = _Canvas(y1 - y0, x1 - x0)
        layer.px = canvas.px[sub].copy()
        for ux, uy, bright in motion.spinner_dots(phase):
            d = np.hypot(xs[sub] - (self._cx + ux * ring), ys[sub] - (self._cy + uy * ring))
            layer.over(ACCENT if bright > 0.85 else WHITE, np.clip(dot * (0.65 + 0.35 * bright) + 0.5 - d, 0, 1) * (0.25 + 0.75 * bright))
        canvas.px[sub] = layer.px
        return canvas.to_bgra(opacity)


def _rounded_rect_sdf_at(x, y, left: float, top: float, w: float, h: float, radius: float) -> np.ndarray:
    qx = np.abs(x - (left + w / 2)) - (w / 2 - radius)
    qy = np.abs(y - (top + h / 2)) - (h / 2 - radius)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - radius

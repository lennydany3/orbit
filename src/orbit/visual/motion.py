"""The maths of the animations: easing, the curved path a cursor takes, the press and the ripple. Pure functions (no
Windows, no drawing), so they are unit-tested and the same numbers drive every animation."""
from __future__ import annotations

import math

Point = tuple[float, float]


def clamp01(t: float) -> float:
    return 0.0 if t <= 0.0 else 1.0 if t >= 1.0 else t


def ease_in_out(t: float) -> float:
    """Slow start, fast middle, slow end (cubic)."""
    t = clamp01(t)
    return 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


def ease_out(t: float) -> float:
    return 1 - (1 - clamp01(t)) ** 3


def travel_seconds(distance_px: float, speed: float = 1.0) -> float:
    """How long a cursor takes to cross `distance_px`: quick for short hops, never slower than about a second.
    `speed` > 1 is faster (ORBIT_VISUALS_SPEED)."""
    seconds = min(1.0, max(0.32, 0.30 + distance_px / 2600.0))
    return seconds / max(0.25, speed)


def arc_point(a: Point, b: Point, t: float, bend: float = 0.16, max_bow_px: float = 90.0) -> Point:
    """Where a cursor is at progress `t` (0..1, already eased) on its way from `a` to `b`.
    A hand does not move in a straight line: the path bows a little (always upward, or to the left for a vertical move)."""
    t = clamp01(t)
    dx, dy = b[0] - a[0], b[1] - a[1]
    dist = math.hypot(dx, dy)
    if dist < 1.0:
        return b
    nx, ny = -dy / dist, dx / dist                       # a unit vector at right angles to the path
    if ny > 0 or (ny == 0 and nx > 0):                   # bow up (negative y), not down
        nx, ny = -nx, -ny
    bow = min(dist * bend, max_bow_px)
    cx, cy = (a[0] + b[0]) / 2 + nx * bow, (a[1] + b[1]) / 2 + ny * bow
    u = 1 - t                                            # a quadratic curve through a, the bowed middle and b
    return (u * u * a[0] + 2 * u * t * cx + t * t * b[0], u * u * a[1] + 2 * u * t * cy + t * t * b[1])


# ---- a click: the pointer presses down, the ripple starts at the moment of impact, then it lets go ----
PRESS_SECONDS = 0.34
IMPACT_AT = 0.13          # seconds into the press when the finger "lands": the real click happens here, the ripple starts here


def press_scale(t: float) -> float:
    """The cursor's size during a press that began `t` seconds ago: shrinks to 78% on impact, holds, springs back."""
    if t <= 0:
        return 1.0
    if t < IMPACT_AT:
        return 1 - 0.22 * ease_out(t / IMPACT_AT)
    if t < IMPACT_AT + 0.09:
        return 0.78
    return min(1.0, 0.78 + 0.22 * ease_out((t - IMPACT_AT - 0.09) / 0.12))


RIPPLE_SECONDS = 0.62


def ripple_rings(t: float) -> list[tuple[float, float]]:
    """The rings of a ripple that began `t` seconds ago: [(radius as a share of the full size, opacity 0..1)], two rings,
    the second a moment behind the first. Empty once it is over."""
    rings = []
    for delay in (0.0, 0.11):
        p = (t - delay) / (RIPPLE_SECONDS - delay)
        if 0.0 <= p < 1.0:
            rings.append((0.18 + 0.82 * ease_out(p), (1 - p) ** 1.6))
    return rings


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_rect(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    """A rectangle (left, top, right, bottom) a share `t` of the way from `a` to `b`."""
    return tuple(round(lerp(x, y, t)) for x, y in zip(a, b))  # type: ignore[return-value]


def window_glide_seconds(distance_px: float, speed: float = 1.0) -> float:
    """How long a window takes to slide across `distance_px` (slower than a bare cursor: it is a heavy thing)."""
    return min(1.1, max(0.45, 0.40 + distance_px / 3200.0)) / max(0.25, speed)


def spinner_dots(phase: float, count: int = 8) -> list[tuple[float, float, float]]:
    """The dots of the "working" spinner: [(x, y on a unit circle, brightness 0..1)]. `phase` in turns (1.0 = one full turn):
    the brightest dot leads and the rest trail off behind it."""
    out = []
    for i in range(count):
        angle = 2 * math.pi * (i / count) - math.pi / 2
        behind = ((phase * count) - i) % count            # how many dots behind the leader this one is
        out.append((math.cos(angle), math.sin(angle), max(0.18, 1.0 - behind / count * 1.05)))
    return out

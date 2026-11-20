"""Scrolling a page or a window with the mouse wheel, and checking that the picture really moved.

The wheel goes to whatever is under the pointer, so the real pointer is moved over the middle of the area, the wheel is turned,
and the pointer is put back. Orbit's animated pointer glides there first, so you see where the scroll happens.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ... import visual
from ..base import ToolResult, VerifyResult
from . import mouse, screen

AMOUNT_SCREENS = {"little": 0.30, "normal": 0.75, "lot": 2.0}       # how far one scroll goes, in screens
NOTCH_PIXELS = 100                                                  # a wheel notch scrolls about 100 pixels at 100% scale
EDGE_BURST_NOTCHES = 40
MAX_EDGE_BURSTS = 12
MOVED_PIXELS = 300                                                  # sampled pixels that must differ for "the picture moved"
SETTLE_S = 1.6                                                      # browsers glide to a stop: give the picture this long to change
EDGE_BUDGET_S = 8.0                                                 # "all the way down" on an endless page stops after this long
EDGE_SETTLE_S = 0.9                                                 # at an edge: how long without any change counts as "no more page"


@dataclass
class Outcome:
    moved: bool        # the picture changed (so the page really scrolled)
    notches: int


Region = tuple[int, int, int, int]


def notches_for(region_height: int, amount: str, times: int, scale: float = 1.0) -> int:
    """How many wheel notches make one scroll of this size on an area this tall (pure, unit-tested)."""
    per_screen = region_height / (NOTCH_PIXELS * max(1.0, scale))
    return max(1, min(60, int(AMOUNT_SCREENS.get(amount, 0.75) * per_screen * max(1, times) + 0.5)))     # (half rounds up)


def _picture(region: Region) -> screen.Shot:
    left, top, right, bottom = region
    return screen.capture(left, top, right - left, bottom - top)


def _changed_within(region: Region, before: screen.Shot, seconds: float, poll_s: float = 0.15) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        time.sleep(poll_s)
        if screen.changed_pixels(before, _picture(region)) >= MOVED_PIXELS:
            return True
        if time.monotonic() >= deadline:
            return False


def scroll_region(region: Region, direction: str, amount: str = "normal", times: int = 1, scale: float = 1.0) -> Outcome:
    """Scroll the area `region` (left, top, right, bottom). `direction` is down, up, top or bottom."""
    left, top, right, bottom = region
    x, y = (left + right) // 2, top + (bottom - top) * 55 // 100
    visual.glide_to(x, y)
    if direction in ("top", "bottom"):
        sign = 1 if direction == "bottom" else -1
        moved_any = False
        deadline = time.monotonic() + EDGE_BUDGET_S
        for _ in range(MAX_EDGE_BURSTS):
            if time.monotonic() >= deadline:
                break
            before = _picture(region)
            mouse.scroll(x, y, sign * EDGE_BURST_NOTCHES, pause_s=0.007)
            if not _changed_within(region, before, SETTLE_S if not moved_any else EDGE_SETTLE_S, poll_s=0.08):
                break
            moved_any = True
            time.sleep(0.12)
        return Outcome(moved_any, 0)
    notches = notches_for(bottom - top, amount, times, scale)
    before = _picture(region)
    mouse.scroll(x, y, notches if direction == "down" else -notches)
    return Outcome(_changed_within(region, before, SETTLE_S), notches)


def describe(direction: str, amount: str = "normal", times: int = 1) -> str:
    if direction in ("top", "bottom"):
        return f"scrolled to the {direction}"
    size = {"little": " a little", "lot": " a lot", "normal": ""}.get(amount, "")
    return f"scrolled {direction}{size}" + (f" {times} times" if times > 1 else "")


def result_for(outcome: Outcome, direction: str, amount: str, times: int, hwnd: int) -> ToolResult:
    """The tool result for a scroll: what was done, and whether the picture moved (checked by `verify`)."""
    result = ToolResult.success(describe(direction, amount, times))
    result.context.update(moved=outcome.moved, direction=direction, hwnd=hwnd)
    return result


def verify(result: ToolResult) -> VerifyResult:
    """The picture is read back: it must have changed. At the very top or bottom nothing can move, so for "to the top/bottom"
    a picture that did not change means "already there"; for a plain scroll it means the scroll did nothing."""
    direction = result.context["direction"]
    if result.context["moved"]:
        return VerifyResult(True, "the page moved on screen")
    if direction in ("top", "bottom"):
        return VerifyResult(True, f"the page did not move: it was already at the {direction}")
    edge = "bottom" if direction == "down" else "top"
    return VerifyResult(False, f"the page did not move (it may already be at the {edge}, or not react to the scroll)")

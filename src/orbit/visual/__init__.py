"""On-screen animations for what Orbit does: its own pointer glides to the thing it is about to click and presses it (with a
ripple), the pointer grabs a window by its title bar and carries it to another monitor, and a small "working..." pill shows
while a job runs.

The animations are only for you to watch: they never change what Orbit does, never take the focus, and are excluded from
Orbit's own screenshots. Everything here is safe to call at any time: with the animations off (ORBIT_VISUALS=0, --no-visuals),
on a PC with no desktop, or if anything at all goes wrong, each function simply does nothing.
"""
from __future__ import annotations

import contextlib
import sys
import threading
from typing import Callable, Iterator

from .. import config

_lock = threading.Lock()
_stage = None
_unusable = False

Rect = tuple[int, int, int, int]


def enabled() -> bool:
    return bool(config.VISUALS) and sys.platform == "win32" and not _unusable


def _get():
    """The running stage, started the first time it is needed (None when the animations are off or cannot run here)."""
    global _stage, _unusable
    if _stage is not None or not enabled():
        return _stage
    with _lock:
        if _stage is None and not _unusable:
            try:
                from . import stage as stage_module

                candidate = stage_module.Stage(scale=stage_module.default_scale(), speed=config.VISUALS_SPEED,
                                               capturable=stage_module.env_flag("ORBIT_VISUALS_CAPTURABLE", False))
                if candidate.start():
                    _stage = candidate
                else:
                    _unusable = True
            except Exception:
                _unusable = True
    return _stage


def install(stage) -> None:
    """Use this stage (tests put a fake here)."""
    global _stage, _unusable
    _stage, _unusable = stage, False


def _call(name: str, *args) -> None:
    stage = _get()
    if stage is None:
        return
    try:
        getattr(stage, name)(*args)
    except Exception:
        pass                                    # an animation must never break an action


def sane(x: float, y: float) -> bool:
    """Is this a point that could be on a screen? (A control that is scrolled away reports a rectangle at 0,0 or far off.)"""
    return -30000 < x < 30000 and -30000 < y < 30000 and (x, y) != (0, 0)


def glide_to(x: float, y: float) -> None:
    """The pointer moves there along a curved path. Returns when it arrives."""
    if sane(x, y):
        _call("glide_to", x, y)


def click_at(x: float, y: float) -> None:
    """The pointer glides there and presses. Returns at the moment of impact, so the real click can follow at once."""
    if sane(x, y):
        _call("click_at", x, y)


def grab(x: float, y: float) -> None:
    """The pointer glides there and presses and holds (the title bar of a window it is about to carry)."""
    if sane(x, y):
        _call("grab", x, y)


def follow(x: float, y: float) -> None:
    if sane(x, y):
        _call("follow", x, y)


def release(ripple: bool = True) -> None:
    _call("release", ripple)


@contextlib.contextmanager
def working(label: str, anchor: Callable[[], Rect | None] | Rect | None = None) -> Iterator[None]:
    """Show the "working" pill (with `label`) while the block runs. `anchor` = the rectangle of the window being worked in
    (or a function that returns it): the pill sits at its bottom centre. A job that finishes quickly never shows it."""
    started = False
    if enabled() and _get() is not None:
        try:
            rect = anchor() if callable(anchor) else anchor
        except Exception:
            rect = None
        _call("busy_on", label, rect)
        started = True
    try:
        yield
    finally:
        if started:
            _call("busy_off")

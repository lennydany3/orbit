"""Moving and pressing windows as a person would, so you can watch it happen.

  * `glide_window`: Orbit's pointer goes to the window's title bar, grabs it, and carries the window to its new place (across
    monitors too), then lets go. The window really slides, a few dozen small steps over about half a second.
  * `press_caption_button`: the pointer goes to a window's minimize / maximize / close button and presses.

All of it is for the eye only. When animations are off (ORBIT_VISUALS=0) the window simply jumps to its place; either way the
same final position is set and verified by the caller, exactly as before.
"""
import ctypes
import time

from ... import config, visual
from ...visual import motion
from . import windows

_FRAME_S = 1 / 90


def dpi_scale() -> float:
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except (AttributeError, OSError):
        return 1.0


def title_bar_point(rect: tuple[int, int, int, int], scale: float | None = None) -> tuple[int, int]:
    """A spot on a window's title bar that is empty enough to grab: left of centre, in the middle of the caption height."""
    scale = scale or dpi_scale()
    left, top, right, _bottom = rect
    width = right - left
    return left + max(60, min(int(width * 0.38), width - 200)), top + round(16 * scale)


def caption_button_point(rect: tuple[int, int, int, int], which: str, scale: float | None = None) -> tuple[int, int]:
    """Where a window's caption button is (Windows 11: three 46 px buttons at the top right, 32 px tall)."""
    scale = scale or dpi_scale()
    _left, top, right, _bottom = rect
    from_right = {"close": 23, "maximize": 69, "restore": 69, "minimize": 115}[which]
    return right - round(from_right * scale), top + round(16 * scale)


def press_caption_button(hwnd: int, which: str) -> None:
    """The pointer glides to the window's minimize / maximize / restore / close button and presses it (nothing is clicked for
    real: the caller does the real action at once, as before)."""
    if visual.enabled():
        visual.click_at(*caption_button_point(windows.visible_rect(hwnd), which))


def glide_window(hwnd: int, target: tuple[int, int, int, int]) -> None:
    """Move a window so its VISIBLE part is exactly `target` = (left, top, width, height), carried by Orbit's pointer.
    Returns when the window is in place (and the pointer has let go)."""
    left, top, width, height = target
    if not visual.enabled():
        windows.set_rect(hwnd, left, top, width, height)
        return
    start = windows.visible_rect(hwnd)
    goal = (left, top, left + width, top + height)
    scale = dpi_scale()
    grab_x, grab_y = title_bar_point(start, scale)
    visual.grab(grab_x, grab_y)                                            # the pointer arrives and takes hold
    distance = ((goal[0] - start[0]) ** 2 + (goal[1] - start[1]) ** 2) ** 0.5 / scale
    seconds = motion.window_glide_seconds(distance, config.VISUALS_SPEED)
    began = time.perf_counter()
    while True:
        progress = (time.perf_counter() - began) / seconds
        if progress >= 1.0:
            break
        l, t, r, b = motion.lerp_rect(start, goal, motion.ease_in_out(progress))
        windows.set_rect(hwnd, l, t, r - l, b - t)
        visual.follow(grab_x + (l - start[0]), grab_y + (t - start[1]))
        time.sleep(_FRAME_S)
    windows.set_rect(hwnd, left, top, width, height)                       # exactly where it was meant to end up
    visual.follow(grab_x + (left - start[0]), grab_y + (top - start[1]))
    visual.release()

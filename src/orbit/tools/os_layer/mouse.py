"""The real mouse: click at a spot and turn the wheel. Used when a control cannot be pressed the accessible way, and to scroll a
page. The pointer is put back where the person had it afterwards, so Orbit never leaves your mouse somewhere strange.

Orbit's animated pointer (see orbit.visual) is drawn separately and does not move the real one.
"""
import ctypes
import time
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_long, ctypes.c_size_t]

_LEFT_DOWN, _LEFT_UP, _WHEEL = 0x0002, 0x0004, 0x0800
NOTCH = 120                    # one click of a wheel, in Windows' units


def position() -> tuple[int, int]:
    here = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(here))
    return here.x, here.y


def click(x: int, y: int, restore: bool = True) -> None:
    """A real left click at a screen position."""
    before = position()
    _user32.SetCursorPos(int(x), int(y))
    _user32.mouse_event(_LEFT_DOWN, 0, 0, 0, 0)
    _user32.mouse_event(_LEFT_UP, 0, 0, 0, 0)
    if restore:
        _user32.SetCursorPos(*before)


def scroll(x: int, y: int, notches: int, pause_s: float = 0.018, restore: bool = True) -> None:
    """Turn the wheel over a spot: `notches` > 0 scrolls the page DOWN (towards its end), < 0 scrolls UP.
    The wheel goes to whatever is under the pointer, so the pointer is moved there first."""
    before = position()
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(0.04)
    step = -NOTCH if notches > 0 else NOTCH                     # a wheel turned towards you (negative) scrolls down
    for _ in range(abs(int(notches))):
        _user32.mouse_event(_WHEEL, 0, 0, step, 0)
        time.sleep(pause_s)
    if restore:
        _user32.SetCursorPos(*before)

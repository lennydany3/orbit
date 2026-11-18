"""Monitors: list them, find which one a window is on, and understand "secondary", "the other one", "left"."""
import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)

try:  # real pixel coordinates on every monitor (otherwise Windows scales them for a "DPI-unaware" program)
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
except (OSError, AttributeError):
    pass

_MONITOR_DEFAULTTONEAREST = 2
_MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, _MONITORENUMPROC, wintypes.LPARAM]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class MonitorNotFound(Exception):
    """Which monitor was meant could not be worked out. The message is safe to show."""


@dataclass(frozen=True)
class Monitor:
    handle: int
    left: int
    top: int
    right: int
    bottom: int
    work: tuple[int, int, int, int]  # the usable area (left, top, right, bottom), without the taskbar
    primary: bool

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def describe(self, number: int) -> str:
        where = "primary" if self.primary else "secondary"
        return f"monitor {number} ({where}): {self.width}x{self.height}, at x={self.left}"


def list_monitors() -> list[Monitor]:
    """All monitors, the primary one first, then the others from left to right."""
    found: list[Monitor] = []

    def visit(handle, _hdc, _rect, _lparam):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        user32.GetMonitorInfoW(handle, ctypes.byref(info))
        m, w = info.rcMonitor, info.rcWork
        found.append(Monitor(int(handle), m.left, m.top, m.right, m.bottom, (w.left, w.top, w.right, w.bottom), bool(info.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(visit), 0)
    return sorted(found, key=lambda m: (not m.primary, m.left))


def monitor_of(hwnd: int, monitors: list[Monitor] | None = None) -> Monitor | None:
    handle = int(user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST) or 0)
    return next((m for m in (monitors if monitors is not None else list_monitors()) if m.handle == handle), None)


_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}


def resolve(spec: str, monitors: list[Monitor], current: Monitor | None) -> Monitor:
    """"primary", "secondary", "other"/"next", "left", "right", "second", "monitor 2" -> one monitor (never a guess)."""
    if len(monitors) < 2:
        raise MonitorNotFound("there is only one monitor connected")
    words = re.sub(r"\b(?:my|the|monitor|screen|display|one)\b", " ", spec.lower()).split()
    word = " ".join(words)
    left_to_right = sorted(monitors, key=lambda m: m.left)
    cur = next((i for i, m in enumerate(left_to_right) if current is not None and m.handle == current.handle), -1)
    if word in ("primary", "main", "first", "1st", "1"):
        return next(m for m in monitors if m.primary)
    if word in ("secondary", "second", "2nd", "2", "external", "extra"):
        secondary = [m for m in monitors if not m.primary]
        if len(secondary) > 1 and word != "secondary":
            return monitors[1]
        return secondary[0]
    if word in ("other", "another", "next", "opposite", "different", ""):
        return left_to_right[(cur + 1) % len(left_to_right)]  # the next one to the right, wrapping around
    if word == "left":
        return left_to_right[0]
    if word == "right":
        return left_to_right[-1]
    if word in _ORDINALS or word.isdigit():
        n = _ORDINALS.get(word) or int(word)
        if 1 <= n <= len(monitors):
            return monitors[n - 1]
    raise MonitorNotFound(f"I don't know which monitor '{spec}' is. Monitors: " + "; ".join(m.describe(i) for i, m in enumerate(monitors, 1)))

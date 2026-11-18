"""Top-level windows via the Win32 API (ctypes only): list, match by name, bring to front."""
import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.GetConsoleWindow.restype = wintypes.HWND
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x80
_DWMWA_CLOAKED = 14
_SW_RESTORE = 9
_SW_MAXIMIZE = 3
_SW_MINIMIZE = 6
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_SWP_NOZORDER_NOACTIVATE = 0x0004 | 0x0010
_WM_CLOSE = 0x0010
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 2
_QUERY_LIMITED = 0x1000


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    exe: str  # lowercase process name without .exe ("" if Windows won't tell us, e.g. elevated apps)

    def label(self) -> str:
        return f"{self.title} ({self.exe or '?'})"


def _exe_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(_QUERY_LIMITED, False, pid.value)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).stem.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return bool(cloaked.value)


def list_windows() -> list[Window]:
    """Visible, titled, real top-level windows (no tool windows, no hidden/suspended UWP shells)."""
    found: list[Window] = []

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
            return True
        if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == "Program Manager":
            return True
        found.append(Window(hwnd=int(hwnd), title=buf.value, exe=_exe_name(hwnd)))
        return True

    user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def foreground_window() -> Window | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    return next((w for w in list_windows() if w.hwnd == int(hwnd)), None)


def match_windows(windows: list[Window], hints: list[str]) -> list[Window]:
    """Windows whose process name or title fits any hint, best match first."""
    hints = [h.lower() for h in hints if h]
    scored = []
    for order, w in enumerate(windows):
        title = w.title.lower()
        score = 0
        for h in hints:
            squashed = h.replace(" ", "")
            if w.exe and w.exe in (h, squashed):
                score = max(score, 4)
            elif title == h:
                score = max(score, 4)
            elif title.startswith(h):
                score = max(score, 3)
            elif h in title:
                score = max(score, 2)
            elif len(squashed) >= 4 and w.exe and squashed in w.exe:
                score = max(score, 1)
            elif len(squashed) >= 4 and squashed in title.replace(" ", ""):  # "speed test" vs "Speedtest"
                score = max(score, 2)
            # The query has extra words ("Speedtest by Ookla"): the window's process or title name sits inside it.
            elif len(w.exe) >= 4 and f" {w.exe} " in f" {h} ":
                score = max(score, 3)
            elif len(title) >= 4 and f" {title} " in f" {h} ":
                score = max(score, 2)
        if score:
            scored.append((-score, order, w))
    return [w for _, _, w in sorted(scored, key=lambda t: (t[0], t[1]))]


def focus(hwnd: int) -> bool:
    """Bring a window to the front. Returns whether Windows actually made it the foreground window."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    if _is_foreground(hwnd):
        return True
    # Windows blocks focus stealing; a tap of Alt makes our request count as user input.
    user32.keybd_event(_VK_MENU, 0, 0, None)
    user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, None)
    user32.SetForegroundWindow(hwnd)
    return _is_foreground(hwnd)


def _is_foreground(hwnd: int, wait_s: float = 0.4) -> bool:
    deadline = time.monotonic() + wait_s
    while True:
        if int(user32.GetForegroundWindow() or 0) == hwnd:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def console_hwnd() -> int:
    """The window Orbit itself runs in (never closed by a close request)."""
    return int(kernel32.GetConsoleWindow() or 0)


def close(hwnd: int) -> None:
    """Ask a window to close, like clicking its X. The app may still ask to save; nothing is force-killed."""
    user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)


# ---- size, position and state (maximize / minimize / move) ----
def is_maximized(hwnd: int) -> bool:
    return bool(user32.IsZoomed(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def maximize(hwnd: int) -> None:
    user32.ShowWindow(hwnd, _SW_MAXIMIZE)


def minimize(hwnd: int) -> None:
    user32.ShowWindow(hwnd, _SW_MINIMIZE)


def restore(hwnd: int) -> None:
    user32.ShowWindow(hwnd, _SW_RESTORE)


def rect(hwnd: int) -> tuple[int, int, int, int]:
    """Outer rectangle (left, top, right, bottom), including the invisible resize border Windows adds."""
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def visible_rect(hwnd: int) -> tuple[int, int, int, int]:
    """The rectangle you actually see (without the invisible border)."""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) == 0:
        return r.left, r.top, r.right, r.bottom
    return rect(hwnd)


def set_rect(hwnd: int, left: int, top: int, width: int, height: int) -> None:
    """Place the VISIBLE part of a window exactly here (the invisible border is compensated for)."""
    out, vis = rect(hwnd), visible_rect(hwnd)
    d_left, d_top = vis[0] - out[0], vis[1] - out[1]
    d_width, d_height = (out[2] - out[0]) - (vis[2] - vis[0]), (out[3] - out[1]) - (vis[3] - vis[1])
    user32.SetWindowPos(hwnd, None, left - d_left, top - d_top, width + d_width, height + d_height, _SWP_NOZORDER_NOACTIVATE)

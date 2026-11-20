"""The stage: a thread that owns a few transparent windows laid over your desktop and draws Orbit's animations on them.

  * Orbit's pointer: glides along a curved path to where Orbit is about to act, presses, and leaves a ripple.
  * A "working" pill (spinner + label) at the bottom of the window Orbit is working in.

The windows are layered (per-pixel transparency), topmost, click-through and never take focus, so they cannot get in the way
of what you or Orbit are doing. They are also excluded from screen capture (Windows 10 2004+), so Orbit's own screenshots
(OCR, "did the page change?") never see the animation. With ORBIT_VISUALS_CAPTURABLE=1 they are captured, which is how the
pictures in the docs are made.

Everything here runs on the stage's own thread; other threads only post commands and (when they ask) wait for an event.
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
from ctypes import wintypes

import numpy as np

from . import motion, sprites, textmask

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

_WS_POPUP = 0x80000000
_WS_EX = 0x8 | 0x20 | 0x80 | 0x80000 | 0x08000000      # topmost, transparent to clicks, tool window, layered, no-activate
_SW_HIDE, _SW_SHOWNOACTIVATE = 0, 4
_ULW_ALPHA, _AC_SRC_ALPHA = 2, 1
_WDA_EXCLUDEFROMCAPTURE = 0x11
_WM_NCHITTEST, _WM_MOUSEACTIVATE, _HTTRANSPARENT, _MA_NOACTIVATE = 0x84, 0x21, -1, 3
_HWND_TOPMOST, _SWP_FLAGS = -1, 0x1 | 0x2 | 0x10        # no size, no move, no activate

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
                                       wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.c_void_p, wintypes.DWORD]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte), ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


@WNDPROC
def _wndproc(hwnd, msg, wparam, lparam):
    if msg == _WM_NCHITTEST:
        return _HTTRANSPARENT
    if msg == _WM_MOUSEACTIVATE:
        return _MA_NOACTIVATE
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class _Layer:
    """One transparent window showing one premultiplied-BGRA picture at a screen position."""

    def __init__(self, class_name: str, hinstance, capture_safe: bool):
        self.hwnd = user32.CreateWindowExW(_WS_EX, class_name, "", _WS_POPUP, 0, 0, 1, 1, None, None, hinstance, None)
        if not self.hwnd:
            raise OSError(f"could not create an overlay window (error {ctypes.get_last_error()})")
        self.capture_safe = bool(capture_safe and user32.SetWindowDisplayAffinity(self.hwnd, _WDA_EXCLUDEFROMCAPTURE))
        self.visible = False
        self._size = (0, 0)
        self._dc = self._bitmap = self._old = None
        self._bits = ctypes.c_void_p()
        self._screen_dc = user32.GetDC(None)

    def _allocate(self, w: int, h: int) -> None:
        self._free()
        self._dc = gdi32.CreateCompatibleDC(self._screen_dc)
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)     # top-down
        self._bitmap = gdi32.CreateDIBSection(self._dc, ctypes.byref(header), 0, ctypes.byref(self._bits), None, 0)
        self._old = gdi32.SelectObject(self._dc, self._bitmap)
        self._size = (w, h)

    def _free(self) -> None:
        if self._dc:
            gdi32.SelectObject(self._dc, self._old)
            gdi32.DeleteObject(self._bitmap)
            gdi32.DeleteDC(self._dc)
        self._dc = self._bitmap = self._old = None

    def present(self, picture: np.ndarray, x: int, y: int) -> None:
        h, w = picture.shape[:2]
        if (w, h) != self._size:
            self._allocate(w, h)
        picture = np.ascontiguousarray(picture)
        ctypes.memmove(self._bits.value, picture.ctypes.data, w * h * 4)
        blend = _BLENDFUNCTION(0, 0, 255, _AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self.hwnd, self._screen_dc, ctypes.byref(wintypes.POINT(int(x), int(y))),
                                   ctypes.byref(wintypes.SIZE(w, h)), self._dc, ctypes.byref(wintypes.POINT(0, 0)), 0,
                                   ctypes.byref(blend), _ULW_ALPHA)
        if not self.visible:
            user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
            user32.SetWindowPos(self.hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_FLAGS)        # above windows that became topmost since
            self.visible = True

    def hide(self) -> None:
        if self.visible:
            user32.ShowWindow(self.hwnd, _SW_HIDE)
            self.visible = False

    def destroy(self) -> None:
        self.hide()
        self._free()
        user32.DestroyWindow(self.hwnd)
        user32.ReleaseDC(None, self._screen_dc)


class Stage:
    """The animation thread. Create one, `start()` it, then call the public methods from any thread."""

    HOLD_S = 1.7              # the pointer stays this long after its last action, then fades away
    BUSY_FADE_IN_S, BUSY_FADE_OUT_S, BUSY_LINGER_S = 0.20, 0.25, 0.45
    FRAME_S = 1 / 60

    def __init__(self, scale: float = 1.0, speed: float = 1.0, capturable: bool = False):
        self.scale, self.speed, self.capturable = sprites.quantize_scale(max(0.75, scale)), speed, capturable
        self.failed = False
        self.capture_safe = False
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="orbit-visual", daemon=True)
        # state, touched only by the stage thread
        self._pos = (0.0, 0.0)
        self._shown = False
        self._opacity = 0.0
        self._move = None          # (start time, seconds, from, to, done event)
        self._press = None         # (start time, x, y, with ripple, impact event)
        self._held = False
        self._ripples: list[tuple[float, float, float]] = []
        self._last_active = 0.0
        self._busy = None          # dict: label, anchor, since, show_after, off_at
        self._pill = None          # (label, sprites.Pill)
        self._pill_pos = (0, 0)
        self._tick_at = time.perf_counter()

    # ------------------------------------------------------------------------------------------ for other threads
    def start(self) -> bool:
        self._thread.start()
        self._ready.wait(4.0)
        return not self.failed

    def glide_to(self, x: float, y: float, wait: bool = True) -> None:
        """Move the pointer there along a curved path (it appears at your mouse the first time). Returns on arrival."""
        done = threading.Event()
        self._q.put(("glide", float(x), float(y), done))
        if wait:
            done.wait(2.5)

    def click_at(self, x: float, y: float) -> None:
        """Glide there and press. Returns at the moment of impact: do the real click now, the ripple plays on its own."""
        self.glide_to(x, y)
        impact = threading.Event()
        self._q.put(("press", float(x), float(y), True, impact))
        impact.wait(1.0)

    def grab(self, x: float, y: float) -> None:
        """Glide there and press and HOLD (grabbing a window's title bar) until `release`."""
        self.glide_to(x, y)
        impact = threading.Event()
        self._q.put(("press", float(x), float(y), False, impact))
        self._q.put(("hold", True))
        impact.wait(1.0)

    def follow(self, x: float, y: float) -> None:
        """Put the held pointer exactly here (no animation of its own; the caller moves it a little each frame)."""
        self._q.put(("follow", float(x), float(y)))

    def release(self, ripple: bool = True) -> None:
        self._q.put(("hold", False))
        if ripple:
            self._q.put(("ripple_here",))

    def busy_on(self, label: str, anchor: tuple[int, int, int, int] | None, show_after: float = 0.3) -> None:
        self._q.put(("busy_on", label, anchor, show_after))

    def busy_off(self) -> None:
        self._q.put(("busy_off",))

    def shutdown(self) -> None:
        self._q.put(("quit",))

    # ------------------------------------------------------------------------------------------ the stage thread
    def _run(self) -> None:
        try:
            self._setup()
        except Exception:
            self.failed = True
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        while True:
            animating = self._animating()
            try:
                command = self._q.get(timeout=self.FRAME_S / 2 if animating else 0.25)
            except queue.Empty:
                command = None
            while command is not None:
                if command[0] == "quit":
                    self._teardown()
                    return
                self._handle(command)
                try:
                    command = self._q.get_nowait()
                except queue.Empty:
                    command = None
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            started = time.perf_counter()
            try:
                self._tick(started)
            except Exception:
                self._abandon()                   # an animation bug must never take anything else down with it
                continue
            if self._animating():
                time.sleep(max(0.0, self.FRAME_S - (time.perf_counter() - started)))

    def _setup(self) -> None:
        hinstance = kernel32.GetModuleHandleW(None)
        wc = _WNDCLASSW()
        wc.lpfnWndProc = _wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = "OrbitVisualOverlay"
        user32.RegisterClassW(ctypes.byref(wc))                      # (registering twice just fails harmlessly)
        safe = not self.capturable
        self._cursor_layer = _Layer(wc.lpszClassName, hinstance, safe)
        self._ripple_layer = _Layer(wc.lpszClassName, hinstance, safe)
        self._busy_layer = _Layer(wc.lpszClassName, hinstance, safe)
        self.capture_safe = self._cursor_layer.capture_safe
        w, h, pad = sprites.cursor_size(self.scale)
        self._cursor_canvas_size = (h, w)
        self._cursor_pad = pad
        self._ripple_size = sprites.ripple_size(self.scale)

    def _teardown(self) -> None:
        for layer in (self._cursor_layer, self._ripple_layer, self._busy_layer):
            layer.destroy()

    def _abandon(self) -> None:
        """Drop whatever was animating and release anyone waiting for it."""
        for item in (self._move, self._press):
            if item:
                item[-1].set()
        self._move = self._press = None
        self._ripples.clear()
        self._held = False
        for layer in (self._cursor_layer, self._ripple_layer, self._busy_layer):
            layer.hide()
        self._shown, self._busy = False, None

    def _animating(self) -> bool:
        return bool(self._move or self._press or self._ripples or self._busy or self._shown)

    def _handle(self, command: tuple) -> None:
        now = time.perf_counter()
        kind = command[0]
        if kind == "glide":
            _, x, y, done = command
            if self._move:
                self._move[-1].set()
            if not self._shown:
                here = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(here))
                self._pos, self._shown, self._opacity = (float(here.x), float(here.y)), True, 0.0
            distance = ((x - self._pos[0]) ** 2 + (y - self._pos[1]) ** 2) ** 0.5
            self._move = (now, motion.travel_seconds(distance / self.scale, self.speed), self._pos, (x, y), done)
            self._last_active = now
        elif kind == "press":
            _, x, y, ripple, impact = command
            if self._press:
                self._press[-1].set()
            self._pos, self._shown, self._move = (x, y), True, None
            self._press = (now, x, y, ripple, impact)
            self._last_active = now
        elif kind == "hold":
            self._held = command[1]
            self._last_active = now
        elif kind == "follow":
            self._pos, self._shown = (command[1], command[2]), True
            self._last_active = now
        elif kind == "ripple_here":
            self._ripples.append((now, self._pos[0], self._pos[1]))
        elif kind == "busy_on":
            _, label, anchor, show_after = command
            busy = self._busy
            still_up = busy is not None and (busy["off_at"] is None or now < busy["off_at"] + self.BUSY_FADE_OUT_S)
            if still_up:
                busy.update(label=label, off_at=None)               # the next step of the same job: keep the pill, change its words
                if anchor:
                    busy["anchor"] = anchor
            else:
                self._busy = dict(label=label, anchor=anchor, since=now, show_after=show_after, off_at=None)
                self._pill = None
        elif kind == "busy_off" and self._busy and self._busy["off_at"] is None:
            self._busy["off_at"] = now + self.BUSY_LINGER_S

    # ---- one frame ----
    def _tick(self, now: float) -> None:
        dt, self._tick_at = min(0.1, now - self._tick_at), now
        press_scale = 1.0
        if self._move:
            start, seconds, a, b, done = self._move
            progress = (now - start) / seconds
            if progress >= 1.0:
                self._pos, self._move = b, None
                done.set()
            else:
                self._pos = motion.arc_point(a, b, motion.ease_in_out(progress))
            self._last_active = now
        if self._press:
            start, x, y, ripple, impact = self._press
            age = now - start
            if age >= motion.IMPACT_AT and not impact.is_set():
                impact.set()
                if ripple:
                    self._ripples.append((now, x, y))
            press_scale = motion.press_scale(age)
            if age >= motion.PRESS_SECONDS:
                self._press = None
            self._last_active = now
        if self._held:
            press_scale = min(press_scale, 0.86)
        self._draw_cursor(now, dt, press_scale)
        self._draw_ripples(now)
        self._draw_busy(now)

    def _draw_cursor(self, now: float, dt: float, press_scale: float) -> None:
        wanted = self._shown and (self._held or self._move or self._press or now - self._last_active < self.HOLD_S)
        rate = 16.0 if wanted else 8.0
        self._opacity += ((1.0 if wanted else 0.0) - self._opacity) * min(1.0, dt * rate)
        if not wanted and self._opacity < 0.02:
            self._opacity, self._shown = 0.0, False
            self._cursor_layer.hide()
            return
        if not self._shown:
            return
        sprite, pad = sprites.cursor_sprite(self.scale * press_scale, min(1.0, self._opacity))
        h, w = self._cursor_canvas_size
        canvas = np.zeros((h, w, 4), dtype=np.uint8)
        top, left = self._cursor_pad - pad, self._cursor_pad - pad          # the tip stays put while the arrow shrinks
        sh, sw = sprite.shape[:2]
        canvas[top:top + sh, left:left + sw] = sprite[: h - top, : w - left]
        self._cursor_layer.present(canvas, round(self._pos[0]) - self._cursor_pad, round(self._pos[1]) - self._cursor_pad)

    def _draw_ripples(self, now: float) -> None:
        self._ripples = [r for r in self._ripples if now - r[0] < motion.RIPPLE_SECONDS]
        if not self._ripples:
            self._ripple_layer.hide()
            return
        start, x, y = self._ripples[-1]
        frame = sprites.ripple_frame(now - start, self.scale)
        if frame is None:
            self._ripple_layer.hide()
            return
        half = self._ripple_size // 2
        self._ripple_layer.present(frame, round(x) - half, round(y) - half)

    def _draw_busy(self, now: float) -> None:
        busy = self._busy
        if busy is None:
            self._busy_layer.hide()
            return
        shown_at = busy["since"] + busy["show_after"]
        fade = 1.0
        if busy["off_at"] is not None and now > busy["off_at"]:
            fade = 1.0 - (now - busy["off_at"]) / self.BUSY_FADE_OUT_S
            if fade <= 0.0:
                self._busy, self._pill = None, None
                self._busy_layer.hide()
                return
        if now < shown_at:
            return                                                       # quick jobs never show it at all
        fade = min(fade, (now - shown_at) / self.BUSY_FADE_IN_S)
        if self._pill is None or self._pill[0] != busy["label"]:
            mask = textmask.render_text(busy["label"], max(11, round(14 * self.scale)))
            self._pill = (busy["label"], sprites.Pill(mask, self.scale))
            self._pill_pos = self._pill_position(self._pill[1], busy["anchor"])
        pill = self._pill[1]
        self._busy_layer.present(pill.frame(((now - busy["since"]) * 1.3) % 1.0, max(0.0, min(1.0, fade))), *self._pill_pos)

    def _pill_position(self, pill: sprites.Pill, anchor: tuple[int, int, int, int] | None) -> tuple[int, int]:
        """Bottom centre of the window Orbit is working in, or of the primary monitor's work area."""
        if anchor is None:
            anchor = (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1) - round(48 * self.scale))
        left, top, right, bottom = anchor
        return (left + right) // 2 - pill.width // 2, bottom - pill.height - round(22 * self.scale)


def default_scale() -> float:
    """The display scale (1.0 = 96 dpi, 1.25 = 120 dpi...), as the system reports it."""
    try:
        return max(1.0, user32.GetDpiForSystem() / 96.0)
    except (AttributeError, OSError):
        return 1.0


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() not in ("0", "off", "false", "no", "")

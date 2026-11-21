"""Take a screenshot, now: "take a screenshot of this screen", "screenshot this window", "capture all my screens".

Orbit grabs the picture straight from Windows (the same capture it uses to read a window, so nothing new is installed), saves it as
a PNG in the Screenshots folder (`Pictures\\Screenshots`, where Win+PrtScn puts them; `ORBIT_SCREENSHOT_DIR` moves it) and puts a
copy on the clipboard so it can be pasted at once (`ORBIT_SCREENSHOT_CLIPBOARD=0` turns that off).

  * "this screen" is the monitor the mouse pointer is on; "this window" is the window in front, or one named ("of Brave");
    "all screens" is every monitor side by side.
  * Orbit's own pointer, "working" pill and orb are left out of the picture, so it shows what you were looking at.
  * The tool takes no path from anyone: the picture can only be written to that one folder.
  * The picture itself never goes to a language model. Only a one-line note of where it was saved does, and that note names the
    folder as %USERPROFILE%\\... so the cloud model is not told your user name.
"""
from __future__ import annotations

import ctypes
import struct
import time
import zlib
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from ... import config
from ..base import ToolResult, VerifyResult
from . import monitors, mouse, screen, windows

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_LEVEL = 3                         # zlib effort: a screenshot is saved in about a tenth of a second, and is still a good deal smaller than a BMP
REPAINT_S = 0.25                      # after bringing another window to the front: time for it to draw before the picture is taken


class ShotParams(BaseModel):
    what: Literal["screen", "window", "all"] = Field(
        default="screen",
        description="screen = the monitor the mouse pointer is on (what the user means by 'this screen'), window = the window in front "
                    "(or the one named in `window`), all = every monitor together")
    window: str = Field(default="", description="only with what=window: part of the title or app name of the window to capture, e.g. Brave (empty = the window in front)")


# ---------------------------------------------------------------- the picture as a PNG (no imaging library needed)
def png_bytes(shot: screen.Shot) -> bytes:
    """A capture (BGRA, bottom-up rows) as a PNG: 8-bit RGB, every row stored as the difference from the row above, which is what makes
    flat screens (windows, text, backgrounds) compress well."""
    h, w = shot.height, shot.width
    rgb = np.frombuffer(shot.pixels, dtype=np.uint8).reshape(h, w, 4)[::-1, :, 2::-1].reshape(h, w * 3)       # top row first, R G B
    raw = np.empty((h, w * 3 + 1), dtype=np.uint8)
    raw[:, 0] = 2                                                                                          # filter "Up"
    raw[0, 1:] = rgb[0]
    raw[1:, 1:] = rgb[1:] - rgb[:-1]                                                                       # wraps round at 256, as the filter needs

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), PNG_LEVEL)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- the clipboard
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
_user32.SetClipboardData.restype = ctypes.c_void_p
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
_CF_DIB, _GMEM_MOVEABLE = 8, 0x0002


def dib_bytes(shot: screen.Shot) -> bytes:
    """The capture as a Windows DIB (what a paste of a picture reads): a header, then 32-bit pixels, bottom-up, fully opaque."""
    pixels = np.frombuffer(shot.pixels, dtype=np.uint8).copy()
    pixels[3::4] = 255                                         # the screen's alpha bytes are 0, which some programs paste as see-through
    return struct.pack("<IiiHHIIiiII", 40, shot.width, shot.height, 1, 32, 0, len(pixels), 0, 0, 0, 0) + pixels.tobytes()


def copy_to_clipboard(shot: screen.Shot) -> None:
    """Replaces what is on the clipboard with the picture. Raises OSError (with the reason) if Windows will not let it."""
    data = dib_bytes(shot)
    handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("there is not enough memory to copy the picture")
    where = _kernel32.GlobalLock(handle)
    ctypes.memmove(where, data, len(data))
    _kernel32.GlobalUnlock(handle)
    for _ in range(10):                                        # another program may hold the clipboard for a moment
        if _user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        _kernel32.GlobalFree(handle)
        raise OSError("another program is using the clipboard")
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(_CF_DIB, handle):
            raise OSError("Windows would not take the picture")
        handle = None                                          # the clipboard owns that memory now
    finally:
        _user32.CloseClipboard()
        if handle:
            _kernel32.GlobalFree(handle)


# ---------------------------------------------------------------- which part of the screen
def _on_screen(mons: list[monitors.Monitor]) -> monitors.Monitor:
    """The monitor the mouse pointer is on (the primary one if it cannot be told)."""
    x, y = mouse.position()
    return next((m for m in mons if m.left <= x < m.right and m.top <= y < m.bottom), mons[0])


def region_for(p: ShotParams) -> tuple[tuple[int, int, int, int], str, str]:
    """(left, top, right, bottom, in screen pixels), what it is called in a sentence, and an error to say instead ("" = fine).
    Brings a named window to the front first, so what covers it is not what gets captured."""
    mons = monitors.list_monitors()
    if not mons:
        return (0, 0, 0, 0), "", "I can't find a screen to capture"
    if p.what == "all":
        rect = (min(m.left for m in mons), min(m.top for m in mons), max(m.right for m in mons), max(m.bottom for m in mons))
        return rect, "all your screens" if len(mons) > 1 else "this screen", ""
    if p.what == "window":
        if p.window.strip():
            found = windows.match_windows(windows.list_windows(), [p.window.strip()])
            if not found:
                return (0, 0, 0, 0), "", f"no open window matches '{p.window.strip()}'"
            win = found[0]
        else:
            win = windows.foreground_window()
            if win is None:
                return (0, 0, 0, 0), "", "no window is in front to capture"
        if windows.is_minimized(win.hwnd):
            windows.restore(win.hwnd)
        front = windows.foreground_window()
        if p.window.strip() and (front is None or front.hwnd != win.hwnd):
            windows.focus(win.hwnd)
            time.sleep(REPAINT_S)
        named = p.window.strip()[:40]                                      # (the window's own title can be a whole sentence, cut off)
        return windows.visible_rect(win.hwnd), f"the {named} window" if named else "the window in front", ""
    m = _on_screen(mons)
    return (m.left, m.top, m.right, m.bottom), "this screen", ""


# ---------------------------------------------------------------- saving
def _unused_name(folder: Path, when: datetime) -> Path:
    base = f"Orbit screenshot {when:%Y-%m-%d %H-%M-%S}"
    path = folder / f"{base}.png"
    n = 2
    while path.exists():                                       # two in the same second
        path = folder / f"{base} ({n}).png"
        n += 1
    return path


def shown_path(path: Path) -> str:
    """The path as a sentence may carry it: under the user's folder it starts %USERPROFILE%, so the user name goes nowhere."""
    try:
        return str(Path("%USERPROFILE%") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def take(p: ShotParams, *, folder: Path | None = None, clipboard: bool | None = None, now: datetime | None = None) -> ToolResult:
    """The screenshot, as fast as Windows gives it: the picture is taken before anything else is done."""
    try:
        (left, top, right, bottom), what, problem = region_for(p)
        if problem:
            return ToolResult.failure(problem, retryable=False, not_found=problem.startswith("no open window"))
        shot = screen.capture(left, top, right - left, bottom - top)
    except screen.ScreenError as e:
        return ToolResult.failure(f"{e}. Windows does not let programs capture some screens, like the lock screen or protected video", retryable=False)
    if not np.frombuffer(shot.pixels, dtype=np.uint8).reshape(-1, 4)[:, :3].any():          # (the fourth byte, alpha, is 0 on a screen)
        return ToolResult.failure("the picture came out completely black (Windows does not let programs capture some screens, "
                                  "like the lock screen, a permission prompt, or protected video)", retryable=False)
    png = png_bytes(shot)
    folder = folder or config.SCREENSHOT_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = _unused_name(folder, now or datetime.now())
        path.write_bytes(png)
    except OSError as e:
        return ToolResult.failure(f"could not save the screenshot in {shown_path(folder)}: {e.strerror or e}", retryable=False)
    copied, why = False, ""
    if config.SCREENSHOT_CLIPBOARD if clipboard is None else clipboard:
        try:
            copy_to_clipboard(shot)
            copied = True
        except OSError as e:
            why = str(e)
    summary = f"took a screenshot of {what} ({shot.width}x{shot.height}), saved to {shown_path(path)}"
    summary += " and copied to the clipboard" if copied else (f" (could not copy it to the clipboard: {why})" if why else "")
    result = ToolResult.success(summary)
    result.context.update(path=str(path), size=len(png), copied=copied)
    return result


def verify(p: ShotParams, r: ToolResult) -> VerifyResult:
    """Reads the file back: it is there, it is as big as what was written, and it starts like a PNG."""
    path = Path(r.context["path"])
    try:
        with path.open("rb") as f:
            head = f.read(len(PNG_SIGNATURE))
        size = path.stat().st_size
    except OSError as e:
        return VerifyResult(False, f"the file is not there: {e.strerror or e}")
    if head != PNG_SIGNATURE or size != r.context["size"]:
        return VerifyResult(False, "the file on disk is not the picture that was taken")
    return VerifyResult(True, f"a {size // 1024} KB PNG is on disk")

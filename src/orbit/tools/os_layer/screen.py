"""See and press things on screen in ANY app, even ones that expose no buttons to Windows (custom-drawn apps like Speedtest).

How it works, with no AI model: take a screenshot of the window, read the words on it with Windows' built-in OCR,
and click the middle of the word you named. After the click, a second screenshot tells whether anything changed.
Text read from the screen is untrusted data, never instructions.
"""
import asyncio
import ctypes
import struct
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass

from . import uia

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]

_SRCCOPY, _CAPTUREBLT, _DIB_RGB_COLORS = 0x00CC0020, 0x40000000, 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


@dataclass
class Shot:
    left: int      # where the picture sits on the screen
    top: int
    width: int
    height: int
    pixels: bytes  # BGRA, bottom-up rows (the BMP layout)

    def as_bmp(self) -> bytes:
        header = struct.pack("<2sIHHI", b"BM", 54 + len(self.pixels), 0, 0, 54)
        info = struct.pack("<IiiHHIIiiII", 40, self.width, self.height, 1, 32, 0, len(self.pixels), 0, 0, 0, 0)
        return header + info + self.pixels


@dataclass
class Word:
    text: str
    x: int       # position inside the picture
    y: int
    width: int
    height: int


class ScreenError(Exception):
    """Could not capture or read the screen. The message is safe to show."""


def capture(left: int, top: int, width: int, height: int) -> Shot:
    """A picture of that part of the screen (screen coordinates; works on any monitor)."""
    if width < 4 or height < 4:
        raise ScreenError("the window has no visible size")
    screen = _user32.GetDC(None)
    mem = _gdi32.CreateCompatibleDC(screen)
    bitmap = _gdi32.CreateCompatibleBitmap(screen, width, height)
    old = _gdi32.SelectObject(mem, bitmap)
    try:
        if not _gdi32.BitBlt(mem, 0, 0, width, height, screen, left, top, _SRCCOPY | _CAPTUREBLT):
            raise ScreenError("could not take a screenshot of the window")
        info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width, height, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(width * height * 4)
        _gdi32.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(info), _DIB_RGB_COLORS)
        return Shot(left, top, width, height, buf.raw)
    finally:
        _gdi32.SelectObject(mem, old)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(mem)
        _user32.ReleaseDC(None, screen)


def changed_pixels(before: Shot, after: Shot) -> int:
    """How many (sampled) pixels differ clearly between two pictures of the same window. A new digit in a calculator's
    display is a handful of pixels, so this is a count, not a share of the whole window."""
    if (before.width, before.height) != (after.width, after.height):
        return 10**6  # the window was resized: certainly changed
    a, b = before.pixels, after.pixels
    changed = 0
    for i in range(0, len(a) - 3, 8):  # every second pixel (4 bytes each)
        if abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2]) > 48:
            changed += 1
    return changed


async def _recognize(bmp: bytes) -> list[Word]:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(bmp)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise ScreenError("Windows has no OCR language installed (Settings > Time & language > Language & region)")
    result = await engine.recognize_async(bitmap)
    return [Word(w.text, int(w.bounding_rect.x), int(w.bounding_rect.y), int(w.bounding_rect.width), int(w.bounding_rect.height))
            for line in result.lines for w in line.words]


def read_words(shot: Shot) -> list[Word]:
    """The words Windows' OCR finds in a picture, with where they are."""
    # Own thread = own event loop (Playwright and others may already run one in this thread).
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_recognize(shot.as_bmp()))).result()


def find_text(words: list[Word], query: str) -> tuple[int, int, str] | None:
    """Where on the picture the user's words are: (centre x, centre y, the text found), or None.
    Needs consecutive words in the same place, forgives typos, ignores filler like "the" and "button"."""
    q = [w for w in uia._words(query) if not uia._is_filler(w)] or uia._words(query)
    if not q:
        return None
    best = None
    for start in range(len(words)):
        span = words[start:start + len(q)]
        if len(span) < len(q):
            break
        # the words of a phrase sit side by side: same line, small gaps
        if any(abs((a.y + a.height / 2) - (b.y + b.height / 2)) > max(a.height, b.height) for a, b in zip(span, span[1:])):
            continue
        scores = []
        for qw, w in zip(q, span):
            got = uia._words(w.text)
            if got and got[0] == qw:
                scores.append(1.0)
            elif got and uia._word_in(qw, got) or (len(qw) <= 3 and got and got[0] == qw):
                scores.append(0.7)
            else:
                scores.append(0.0)
        if all(s > 0 for s in scores):
            total = sum(scores) / len(scores)
            if best is None or total > best[0]:
                left = min(w.x for w in span)
                right = max(w.x + w.width for w in span)
                top = min(w.y for w in span)
                bottom = max(w.y + w.height for w in span)
                best = (total, (left + right) // 2, (top + bottom) // 2, " ".join(w.text for w in span))
    return (best[1], best[2], best[3]) if best else None


def click_at(x: int, y: int) -> None:
    """Click at a screen position, then put the pointer back where the user had it."""
    here = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(here))
    _user32.SetCursorPos(x, y)
    _user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
    _user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up
    _user32.SetCursorPos(here.x, here.y)

"""Text as an alpha mask (a float array, 0..1), drawn with Windows' own font engine (GDI). No imaging library needed: the text
is drawn white on black into a memory bitmap and that picture IS the mask."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache

import numpy as np

_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_HDC, _HGDIOBJ = wintypes.HDC, wintypes.HGDIOBJ
_gdi32.CreateCompatibleDC.restype = _HDC
_gdi32.CreateCompatibleDC.argtypes = [_HDC]
_gdi32.CreateFontW.restype = wintypes.HFONT
_gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
_gdi32.SelectObject.restype = _HGDIOBJ
_gdi32.SelectObject.argtypes = [_HDC, _HGDIOBJ]
_gdi32.DeleteObject.argtypes = [_HGDIOBJ]
_gdi32.DeleteDC.argtypes = [_HDC]
_gdi32.GetTextExtentPoint32W.argtypes = [_HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.SIZE)]
_gdi32.TextOutW.argtypes = [_HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
_gdi32.SetTextColor.argtypes = [_HDC, wintypes.COLORREF]
_gdi32.SetBkColor.argtypes = [_HDC, wintypes.COLORREF]
_gdi32.SetBkMode.argtypes = [_HDC, ctypes.c_int]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = [_HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.GdiFlush.argtypes = []


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


_FW_SEMIBOLD, _DEFAULT_CHARSET, _ANTIALIASED_QUALITY, _OPAQUE = 600, 1, 4, 2


@lru_cache(maxsize=64)
def render_text(text: str, pixel_height: int, face: str = "Segoe UI") -> np.ndarray:
    """`text` as a (height, width) float32 mask in 0..1, set in `face` with letters about `pixel_height` pixels tall."""
    if not text:
        return np.zeros((pixel_height, 1), dtype=np.float32)
    dc = _gdi32.CreateCompatibleDC(None)
    font = _gdi32.CreateFontW(-pixel_height, 0, 0, 0, _FW_SEMIBOLD, 0, 0, 0, _DEFAULT_CHARSET, 0, 0, _ANTIALIASED_QUALITY, 0, face)
    old_font = _gdi32.SelectObject(dc, font)
    try:
        size = wintypes.SIZE()
        _gdi32.GetTextExtentPoint32W(dc, text, len(text), ctypes.byref(size))
        width, height = size.cx + 4, size.cy + 2
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)   # top-down rows
        bits = ctypes.c_void_p()
        bitmap = _gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        old_bitmap = _gdi32.SelectObject(dc, bitmap)
        try:
            _gdi32.SetBkMode(dc, _OPAQUE)
            _gdi32.SetBkColor(dc, 0x000000)
            _gdi32.SetTextColor(dc, 0xFFFFFF)
            _gdi32.TextOutW(dc, 2, 1, text, len(text))
            _gdi32.GdiFlush()
            raw = (ctypes.c_ubyte * (width * height * 4)).from_address(bits.value)
            picture = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4).copy()
        finally:
            _gdi32.SelectObject(dc, old_bitmap)
            _gdi32.DeleteObject(bitmap)
        return (picture[..., 1].astype(np.float32) / 255.0) ** 0.85         # the green channel is the grey of the white-on-black text
    finally:
        _gdi32.SelectObject(dc, old_font)
        _gdi32.DeleteObject(font)
        _gdi32.DeleteDC(dc)

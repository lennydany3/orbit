"""Pressing key combinations and typing text into whatever window has the keyboard (Windows SendInput).

Used only by the user's own custom commands (settings.json), never offered to the language model, and always behind a yes/no
that lists exactly what will be pressed or typed. There is nothing to read back afterwards, so these are never "verified".

Key combinations are written `ctrl+shift+s`, `win+shift+s`, `alt+tab`, `f5`, `enter`: any of the modifiers ctrl, alt, shift, win
plus exactly one key from `KEYS`.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

MODIFIERS = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}
_ALIASES = {"control": "ctrl", "windows": "win", "super": "win", "cmd": "win", "meta": "win", "option": "alt", "escape": "esc",
            "return": "enter", "del": "delete", "ins": "insert", "pgup": "pageup", "pgdn": "pagedown", "prtsc": "printscreen",
            "prtscn": "printscreen", "spacebar": "space", "bksp": "backspace"}

KEYS: dict[str, int] = {chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)}       # a-z: virtual keys 0x41-0x5A
KEYS.update({str(d): 0x30 + d for d in range(10)})                                    # 0-9
KEYS.update({f"f{n}": 0x70 + n - 1 for n in range(1, 25)})                            # f1-f24
KEYS.update({"enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24,
             "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "printscreen": 0x2C,
             "minus": 0xBD, "equals": 0xBB, "comma": 0xBC, "period": 0xBE, "slash": 0xBF, "semicolon": 0xBA, "quote": 0xDE,
             "lbracket": 0xDB, "rbracket": 0xDD, "backslash": 0xDC, "backtick": 0xC0})
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B}       # these need the extended-key flag

MAX_TEXT_CHARS = 500


class KeyError_(ValueError):
    """A key combination Orbit does not understand."""


@dataclass(frozen=True)
class Chord:
    modifiers: tuple[str, ...]      # in the order ctrl, alt, shift, win
    key: str

    def __str__(self) -> str:
        return "+".join([*self.modifiers, self.key])


def parse_chord(text: str) -> Chord:
    """"Win + Shift + S" -> Chord(("shift", "win"), "s"), written back as "shift+win+s". Raises KeyError_ with the reason."""
    parts = [p.strip().lower() for p in str(text).split("+")]
    if not text or any(not p for p in parts):
        raise KeyError_("write keys like ctrl+shift+s")
    names = [_ALIASES.get(p, p) for p in parts]
    mods = [n for n in names if n in MODIFIERS]
    keys = [n for n in names if n not in MODIFIERS]
    if len(set(mods)) != len(mods):
        raise KeyError_("a modifier is repeated")
    if len(keys) != 1:
        raise KeyError_("a combination needs exactly one key besides ctrl, alt, shift and win" if not keys else "only one key besides the modifiers")
    if keys[0] not in KEYS:
        raise KeyError_(f"unknown key '{keys[0]}'")
    return Chord(tuple(m for m in ("ctrl", "alt", "shift", "win") if m in mods), keys[0])


# ---------------------------------------------------------------- Windows SendInput
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):               # present only so the union is as large as Windows expects
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


# One key event: (virtual key, scan code, flags).
Event = tuple[int, int, int]


def _send(events: list[Event]) -> None:
    """Hands the events to Windows in one call, so nothing else can slip in between them."""
    inputs = (_INPUT * len(events))()
    for i, (vk, scan, flags) in enumerate(events):
        inputs[i].type = INPUT_KEYBOARD
        inputs[i].ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
    sent = ctypes.windll.user32.SendInput(len(events), inputs, ctypes.sizeof(_INPUT))
    if sent != len(events):
        raise OSError(f"Windows accepted {sent} of {len(events)} key events (another program may be blocking input)")


def chord_events(chord: Chord) -> list[Event]:
    """Modifiers down, the key down and up, modifiers up in reverse."""
    def one(name: str, up: bool) -> Event:
        vk = MODIFIERS.get(name) or KEYS[name]
        return (vk, 0, (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0))

    return ([one(m, False) for m in chord.modifiers] + [one(chord.key, False), one(chord.key, True)]
            + [one(m, True) for m in reversed(chord.modifiers)])


def press_keys(text: str, send: Callable[[list[Event]], None] = _send) -> Chord:
    chord = parse_chord(text)
    send(chord_events(chord))
    return chord


def text_events(text: str) -> list[Event]:
    """Each character as a Unicode key press (so any language types correctly); a newline presses Enter and a tab presses Tab."""
    events: list[Event] = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            events += [(KEYS["enter"], 0, 0), (KEYS["enter"], 0, KEYEVENTF_KEYUP)]
        elif ch == "\t":
            events += [(KEYS["tab"], 0, 0), (KEYS["tab"], 0, KEYEVENTF_KEYUP)]
        else:
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):                      # a character outside the basic plane is two UTF-16 units
                unit = int.from_bytes(data[i:i + 2], "little")
                events += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    return events


def type_text(text: str, send: Callable[[list[Event]], None] = _send, pause_s: float = 0.0) -> int:
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"text is longer than {MAX_TEXT_CHARS} characters")
    events = text_events(text)
    for i in range(0, len(events), 64):                           # in batches, so a long text does not flood the target window
        send(events[i:i + 64])
        if pause_s:
            time.sleep(pause_s)
    return len(text)

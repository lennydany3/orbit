"""Global hotkeys (pynput): hold-to-talk and the kill switch. They work whichever window has focus.

The listener threads only put small events on a queue; the voice loop reads them with poll(). No audio logic runs here.
Hold-to-talk is Right Ctrl by default. Because Ctrl is also a shortcut key, a hold only counts if it lasts at least
PTT_MIN_HOLD_S and no other key was pressed meanwhile (so Right Ctrl+C is never mistaken for talking).
"""
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pynput import keyboard

from .. import settings
from . import config

SETTINGS_CHECK_S = 1.0       # how often the settings file is looked at for a new hold-to-talk key


@dataclass
class KeyEvent:
    kind: str            # "down" | "up" | "kill"
    held_s: float = 0.0  # up only: how long the key was held
    valid: bool = True   # up only: False for a quick tap or a shortcut like Ctrl+C


def parse_key(name: str):
    """'ctrl_r' -> keyboard.Key.ctrl_r;  'f9' -> keyboard.Key.f9;  'z' -> a character key."""
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(f"Unknown key '{name}'. Use a pynput name like ctrl_r, f9, caps_lock or a single character.")


class Hotkeys:
    def __init__(self, ptt_key: str | None = None, kill_combo: str = config.KILL_COMBO,
                 min_hold_s: float = config.PTT_MIN_HOLD_S, settings_file: Path | None = None, follow_settings: bool = True):
        """`ptt_key` None = the one in the settings file (else the default). With `follow_settings`, a new one saved in the
        Settings page applies within a second, with no restart."""
        self._settings_file = settings_file
        self._follow = follow_settings and ptt_key is None
        self._checked_at = time.monotonic()
        self._stamp = self._file_stamp()
        self.ptt_name = ptt_key or settings.load(settings_file).talk_key
        self.ptt_key = parse_key(self.ptt_name)
        self._vk = self._virtual_key(self.ptt_key)
        self.kill_combo = kill_combo
        self.min_hold_s = min_hold_s
        self.killed = threading.Event()
        self._events: queue.Queue[KeyEvent] = queue.Queue()
        self._down_at: float | None = None
        self._tainted = False
        self._listener: keyboard.Listener | None = None
        self._combo: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._combo = keyboard.GlobalHotKeys({self.kill_combo: self._on_kill})
        self._listener.start()
        self._combo.start()

    def stop(self) -> None:
        for listener in (self._listener, self._combo):
            if listener is not None:
                listener.stop()

    @staticmethod
    def _virtual_key(key):
        return getattr(getattr(key, "value", key), "vk", None)       # Windows virtual-key code, for the safety check

    def _file_stamp(self):
        try:
            st = (self._settings_file or settings.path()).stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def set_ptt_key(self, name: str) -> bool:
        """Changes the hold-to-talk key. A key held right now is let go of first, so nothing is left listening."""
        try:
            key = parse_key(name)
        except ValueError:
            return False
        if key == self.ptt_key:
            return False
        if self._down_at is not None:
            self._on_release(self.ptt_key)
        self.ptt_name, self.ptt_key, self._vk = name, key, self._virtual_key(key)
        return True

    def _follow_settings(self) -> None:
        now = time.monotonic()
        if not self._follow or now - self._checked_at < SETTINGS_CHECK_S:
            return
        self._checked_at = now
        stamp = self._file_stamp()
        if stamp != self._stamp:
            self._stamp = stamp
            self.set_ptt_key(settings.load(self._settings_file).talk_key)

    def physically_down(self) -> bool | None:
        """Does Windows say the hold-to-talk key is down right now? None if it cannot tell."""
        if sys.platform != "win32" or self._vk is None:
            return None
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(self._vk) & 0x8000)

    def poll(self) -> list[KeyEvent]:
        self._follow_settings()
        # A busy PC can drop a key-release event, which would leave Orbit listening until the time limit.
        # If we think the key is down but Windows says it is up, the release was missed: synthesize it.
        if self._down_at is not None and self.physically_down() is False:
            self._on_release(self.ptt_key)
        events = []
        try:
            while True:
                events.append(self._events.get_nowait())
        except queue.Empty:
            return events

    # ---- listener threads: keep these tiny ----
    def _on_press(self, key) -> None:
        if key == self.ptt_key:
            if self._down_at is None:        # Windows repeats key-down events while a key is held: only the first counts
                self._down_at, self._tainted = time.perf_counter(), False
                self._events.put(KeyEvent("down"))
        elif self._down_at is not None:
            self._tainted = True             # another key while holding: this was a shortcut, not talking

    def _on_release(self, key) -> None:
        if key == self.ptt_key and self._down_at is not None:
            held = time.perf_counter() - self._down_at
            self._events.put(KeyEvent("up", held, valid=not self._tainted and held >= self.min_hold_s))
            self._down_at = None

    def _on_kill(self) -> None:
        self.killed.set()
        self._events.put(KeyEvent("kill"))

"""Global hotkeys (pynput): hold-to-talk and the kill switch. They work whichever window has focus.

The listener threads only put small events on a queue; the voice loop reads them with poll(). No audio logic runs here.
Hold-to-talk is Right Ctrl by default. Because Ctrl is also a shortcut key, a hold only counts if it lasts at least
PTT_MIN_HOLD_S and no other key was pressed meanwhile (so Right Ctrl+C is never mistaken for talking).
"""
import queue
import threading
import time
from dataclasses import dataclass

from pynput import keyboard

from . import config


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
    def __init__(self, ptt_key: str = config.PTT_KEY, kill_combo: str = config.KILL_COMBO,
                 min_hold_s: float = config.PTT_MIN_HOLD_S):
        self.ptt_key = parse_key(ptt_key)
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

    def poll(self) -> list[KeyEvent]:
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

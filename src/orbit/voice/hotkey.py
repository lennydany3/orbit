"""Global hotkeys (pynput): hold-to-talk and the kill switch. They work whichever window has focus.

The listener threads only put small events on a queue; the voice loop reads them with poll(). No audio logic runs here.
Hold-to-talk is Right Ctrl by default. Because Ctrl is also a shortcut key, a hold only counts if it lasts at least
PTT_MIN_HOLD_S and no other key was pressed meanwhile (so Right Ctrl+C is never mistaken for talking).

The talk key may also be a mouse button (`mouse1` left, `mouse2` right, `mouse3` middle, `mouse4` back, `mouse5` forward). A mouse
button is also what you click with, so it only starts listening once it has been held for PTT_MIN_HOLD_S: an ordinary click never
wakes Orbit. (Orbit does not take the click away from the program under it; the button still clicks as it always does.)
"""
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pynput import keyboard, mouse

from .. import settings
from . import config

SETTINGS_CHECK_S = 1.0       # how often the settings file is looked at for a new hold-to-talk key


@dataclass
class KeyEvent:
    kind: str            # "down" | "up" | "kill"
    held_s: float = 0.0  # up only: how long the key was held
    valid: bool = True   # up only: False for a quick tap or a shortcut like Ctrl+C


# The Windows virtual-key code of each mouse button (GetAsyncKeyState: is it really down right now?)
_MOUSE_VK = {"left": 0x01, "right": 0x02, "middle": 0x04, "x1": 0x05, "x2": 0x06}


def parse_key(name: str):
    """'ctrl_r' -> keyboard.Key.ctrl_r;  'f9' -> keyboard.Key.f9;  'z' -> a character key;  'mouse5' -> mouse.Button.x2."""
    if name in settings.MOUSE_TALK:
        return getattr(mouse.Button, settings.MOUSE_TALK[name])
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(f"Unknown key '{name}'. Use a pynput name like ctrl_r, f9, caps_lock, a single character or mouse1 to mouse5.")


def is_mouse(key) -> bool:
    return isinstance(key, mouse.Button)


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
        self._announced = True               # has "down" been sent for the hold in progress? (a mouse button waits for the minimum hold)
        self._started = False
        self._listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None
        self._combo: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._combo = keyboard.GlobalHotKeys({self.kill_combo: self._on_kill})
        self._listener.start()
        self._combo.start()
        self._started = True
        self._sync_mouse_listener()

    def stop(self) -> None:
        self._started = False
        for listener in (self._listener, self._combo, self._mouse_listener):
            if listener is not None:
                listener.stop()
        self._mouse_listener = None

    def _sync_mouse_listener(self) -> None:
        """The mouse hook is only installed while the talk key is a mouse button: nothing watches your mouse otherwise."""
        wanted = self._started and is_mouse(self.ptt_key)
        if wanted and self._mouse_listener is None:
            self._mouse_listener = mouse.Listener(on_click=self._on_click)
            self._mouse_listener.start()
        elif not wanted and self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None

    @staticmethod
    def _virtual_key(key):
        if is_mouse(key):
            return _MOUSE_VK.get(key.name)
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
        self._sync_mouse_listener()
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
        down_at = self._down_at                   # (the listener thread may let go at any moment: read it once)
        if down_at is not None and not self._announced and time.perf_counter() - down_at >= self.min_hold_s:
            self._announced = True               # a mouse button held long enough: now it counts as talking
            self._events.put(KeyEvent("down"))
        events = []
        try:
            while True:
                events.append(self._events.get_nowait())
        except queue.Empty:
            return events

    # ---- listener threads: keep these tiny ----
    def _on_press(self, key) -> None:
        if is_mouse(self.ptt_key):
            if self._down_at is not None:
                self._tainted = True         # a key pressed while the mouse button is held: not talking
            return
        if key == self.ptt_key:
            if self._down_at is None:        # Windows repeats key-down events while a key is held: only the first counts
                self._down_at, self._tainted, self._announced = time.perf_counter(), False, True
                self._events.put(KeyEvent("down"))
        elif self._down_at is not None:
            self._tainted = True             # another key while holding: this was a shortcut, not talking

    def _on_release(self, key) -> None:
        if key == self.ptt_key and self._down_at is not None:
            self._let_go()

    def _on_click(self, x, y, button, pressed) -> None:
        if button != self.ptt_key:
            return
        if pressed:
            if self._down_at is None:
                self._down_at, self._tainted, self._announced = time.perf_counter(), False, False
        elif self._down_at is not None:
            self._let_go()

    def _let_go(self) -> None:
        held = time.perf_counter() - self._down_at
        if not self._announced and held >= self.min_hold_s:
            self._events.put(KeyEvent("down"))   # held long enough, but the loop did not look in between: it still counts
            self._announced = True
        if self._announced:                      # a hold that was too short (a plain mouse click) leaves no trace at all
            self._events.put(KeyEvent("up", held, valid=not self._tainted and held >= self.min_hold_s))
        self._down_at, self._announced = None, True

    def _on_kill(self) -> None:
        self.killed.set()
        self._events.put(KeyEvent("kill"))

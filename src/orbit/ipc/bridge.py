"""What the voice loop tells the face, and what the face may tell it back.

The voice loop calls these methods from its own thread. They never raise (a broken face must not break a voice turn) and
never block. `NullBridge` is the same interface doing nothing, so the loop has no "is there a face?" checks.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

from ..voice.state import State
from . import protocol

log = logging.getLogger("orbit.ipc")

# The voice loop's state -> the sphere's state.
ORB_FOR = {
    State.IDLE: "idle",
    State.WAKE_DETECTED: "wake",
    State.LISTENING: "listening",
    State.TRANSCRIBING: "transcribing",
    State.THINKING: "thinking",
    State.CONFIRMING: "confirming",
    State.SPEAKING: "speaking",
    State.FOLLOW_UP: "follow_up",
}

# Levels only mean something while a voice is being heard: yours (mic) or Orbit's (speaker).
_MIC_STATES = (State.LISTENING, State.FOLLOW_UP, State.CONFIRMING)
_VOICE_STATES = (State.SPEAKING, State.CONFIRMING)


class NullBridge:
    """No face: every call does nothing."""

    def state_changed(self, old, new, reason="", seconds=None) -> None: ...
    def mic_frame(self, frame) -> None: ...
    def audio_block(self, block) -> None: ...
    def tick(self) -> None: ...
    def transcript(self, who, text) -> None: ...
    def reply(self, text, spoken, kind, emotion) -> None: ...
    def outcome(self, ok) -> None: ...
    def confirm_asked(self, question, action) -> int:
        return 0
    def confirm_done(self, confirm_id, answer, by) -> None: ...
    def killed(self) -> None: ...
    def reminder(self, reminder_id, title, text) -> None: ...

    def poll_answer(self):
        return None

    def poll_request(self):
        return None


def _safe(method):
    """A face problem is logged and forgotten, never raised into the voice loop."""

    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("ui bridge: %s failed", method.__name__)
            return None

    wrapper.__name__ = method.__name__
    return wrapper


class UiBridge:
    def __init__(self, publish: Callable[[dict], None], clock: Callable[[], float] = time.monotonic, level_hz: float = 20.0):
        self._publish = publish
        self._clock = clock
        self._min_gap = 1.0 / level_hz
        self._lock = threading.Lock()
        self._state = State.IDLE
        self._orb = "idle"
        self._level = 0.0
        self._sent_level = 0.0
        self._sent_at = -1.0
        self._audio_at = -1.0
        self._next_confirm = 1
        self._open_confirm: int | None = None
        self._answers: queue.Queue[protocol.ConfirmAnswer] = queue.Queue()
        self._requests: queue.Queue[protocol.PlayRequest] = queue.Queue(maxsize=8)

    # ---- for the server ----
    def snapshot(self) -> dict:
        with self._lock:
            return protocol.hello(self._orb, self._level)

    def on_command(self, answer: protocol.ConfirmAnswer | protocol.PlayRequest) -> str | None:
        """A face answered a confirmation, or asked for one of the user's saved playlists. Returns why it was not accepted, or None."""
        if isinstance(answer, protocol.PlayRequest):
            try:
                self._requests.put_nowait(answer)
            except queue.Full:
                return "Orbit is already working on earlier requests"
            return None
        with self._lock:
            if self._open_confirm is None or answer.id != self._open_confirm:
                return "no such confirmation is open"
            self._open_confirm = None                  # the first answer wins; a second one is "not open"
        self._answers.put(answer)
        return None

    # ---- state ----
    @_safe
    def state_changed(self, old: State, new: State, reason: str = "", seconds: float | None = None) -> None:
        orb = ORB_FOR[new]
        with self._lock:
            self._state, self._orb = new, orb
            self._level = 0.0
        self._publish(protocol.orb_state(orb, reason=reason, seconds=seconds if new is State.FOLLOW_UP else None))
        self._send_level(0.0)

    @_safe
    def outcome(self, ok: bool) -> None:
        """A request just finished: a `success` or `error` flash, sent right before the reply is spoken."""
        orb = "success" if ok else "error"
        with self._lock:
            self._orb = orb
        self._publish(protocol.orb_state(orb))

    @_safe
    def killed(self) -> None:
        with self._lock:
            self._orb, self._level = "muted", 0.0
        self._publish(protocol.orb_state("muted", reason="kill switch"))

    # ---- levels ----
    @_safe
    def mic_frame(self, frame) -> None:
        if self._state in _MIC_STATES:
            self._level_seen(protocol.level_from_pcm(frame))

    @_safe
    def audio_block(self, block) -> None:
        """A block Orbit's voice just played (any thread)."""
        if self._state in _VOICE_STATES:
            self._level_seen(protocol.level_from_pcm(block, protocol.VOICE_RANGE))

    def _level_seen(self, value: float) -> None:
        now = self._clock()
        with self._lock:
            self._level, self._audio_at = value, now
        self._send_level(value)

    @_safe
    def tick(self) -> None:
        """Called every loop turn: a voice that stopped (paused, or a pause between sentences) lets the level fall to 0."""
        with self._lock:
            stale = self._level > 0 and self._clock() - self._audio_at > 0.3
            if stale:
                self._level = 0.0
        if stale:
            self._send_level(0.0)

    def _send_level(self, value: float) -> None:
        """At most `level_hz` messages a second and only real changes, except that falling to silence is always sent."""
        now = self._clock()
        with self._lock:
            falling_silent = value == 0.0 and self._sent_level != 0.0
            if not falling_silent:
                if now - self._sent_at < self._min_gap or abs(value - self._sent_level) < 0.02:
                    return
            self._sent_level, self._sent_at = value, now
        self._publish(protocol.level(value))

    # ---- what was said ----
    @_safe
    def transcript(self, who: str, text: str) -> None:
        self._publish(protocol.transcript(who, text))

    @_safe
    def reply(self, text: str, spoken: str, kind: str, emotion: str) -> None:
        self._publish(protocol.reply(text, spoken, kind, emotion))

    @_safe
    def reminder(self, reminder_id: str, title: str, text: str) -> None:
        """A reminder just came due: the face shows it (Orbit says it out loud itself)."""
        self._publish(protocol.reminder(reminder_id, title, text))

    def poll_request(self) -> protocol.PlayRequest | None:
        """A request from the face (the Play button), if one is waiting."""
        try:
            return self._requests.get_nowait()
        except queue.Empty:
            return None

    # ---- confirmations ----
    @_safe
    def confirm_asked(self, question: str, action: str) -> int:
        with self._lock:
            cid, self._next_confirm = self._next_confirm, self._next_confirm + 1
            self._open_confirm = cid
        while True:                                    # an answer to an earlier question can never be mistaken for this one
            try:
                self._answers.get_nowait()
            except queue.Empty:
                break
        self._publish(protocol.confirm(cid, question, action))
        return cid

    @_safe
    def confirm_done(self, confirm_id: int, answer: bool, by: str) -> None:
        with self._lock:
            if self._open_confirm == confirm_id:
                self._open_confirm = None
        self._publish(protocol.confirm_done(confirm_id, answer, by))

    def poll_answer(self) -> protocol.ConfirmAnswer | None:
        try:
            return self._answers.get_nowait()
        except queue.Empty:
            return None

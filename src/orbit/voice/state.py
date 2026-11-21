"""The voice state machine: which step of a turn we are in, which steps may follow which, and timing.

  idle -> wake_detected -> listening -> transcribing -> thinking -> speaking -> follow_up -> idle
                                                         |  ^
                                                    confirming (a risky action asks "yes or no?")

Three turns start from idle without anyone speaking: a reminder or timer comes due and rings (idle -> alarm, which ends by itself, or
by "ok thanks", the talk key or the window's Stop button, or goes on to speaking for one short answer), a late one is only said
(idle -> speaking), and the window's Play button asks for a saved playlist (idle -> thinking).

Every transition is logged with a timestamp. `idle` can always be entered (a timeout, an error, the kill switch).
Pure logic: no audio, no threads, so it is unit-tested without any hardware.
"""
import time
from datetime import datetime
from enum import Enum
from typing import Callable


class State(str, Enum):
    IDLE = "idle"
    WAKE_DETECTED = "wake_detected"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    CONFIRMING = "confirming"
    SPEAKING = "speaking"
    FOLLOW_UP = "follow_up"
    ALARM = "alarm"                       # a reminder or timer is ringing and waiting to hear that you have heard it


ALLOWED: dict[State, set[State]] = {
    State.IDLE: {State.WAKE_DETECTED, State.LISTENING, State.SPEAKING, State.THINKING, State.ALARM},   # LISTENING directly = hold-to-talk
    State.WAKE_DETECTED: {State.LISTENING},
    State.LISTENING: {State.TRANSCRIBING},
    State.TRANSCRIBING: {State.THINKING, State.SPEAKING},                # SPEAKING = "Sorry, I didn't catch that"
    State.THINKING: {State.CONFIRMING, State.SPEAKING},
    State.CONFIRMING: {State.THINKING},
    State.SPEAKING: {State.FOLLOW_UP, State.LISTENING},                  # LISTENING = the hotkey interrupted the speech
    State.FOLLOW_UP: {State.LISTENING},
    State.ALARM: {State.SPEAKING, State.LISTENING},                      # SPEAKING = "Okay, I'll ring again in 5 minutes"; LISTENING = the talk key
}


class IllegalTransition(Exception):
    pass


class VoiceState:
    def __init__(self, clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] | None = None,
                 wall: Callable[[], datetime] = datetime.now, on_change: Callable[["State", "State", str], None] | None = None):
        self._clock, self._log, self._wall, self._on_change = clock, log, wall, on_change
        self.state = State.IDLE
        self._since = clock()
        self.history: list[tuple[str, State, State, str]] = []   # (timestamp, from, to, reason)

    def to(self, new: State, reason: str = "") -> None:
        old = self.state
        if new != State.IDLE and new not in ALLOWED[old]:
            raise IllegalTransition(f"{old.value} -> {new.value} is not allowed")
        stamp = self._wall().strftime("%H:%M:%S.%f")[:-3]
        self.history.append((stamp, old, new, reason))
        self.state, self._since = new, self._clock()
        if self._log:
            self._log(f"[{stamp}] {old.value} -> {new.value}" + (f" ({reason})" if reason else ""))
        if self._on_change:
            self._on_change(old, new, reason)

    @property
    def elapsed(self) -> float:
        """Seconds spent in the current state."""
        return self._clock() - self._since

    def timed_out(self, seconds: float) -> bool:
        return self.elapsed >= seconds

    def is_(self, *states: State) -> bool:
        return self.state in states


class TurnTimer:
    """Marks the moments of one turn, so the log shows where the time went."""

    ORDER = ["speech_end", "text", "reply", "first_audio", "spoken"]

    def __init__(self, clock: Callable[[], float] = time.perf_counter):
        self._clock, self.marks = clock, {}

    def mark(self, name: str, at: float | None = None) -> None:
        self.marks[name] = self._clock() if at is None else at

    def gap(self, a: str, b: str) -> float | None:
        return self.marks[b] - self.marks[a] if a in self.marks and b in self.marks else None

    def summary(self, end_silence_s: float = 0.0) -> str:
        parts = []
        for label, a, b in (("transcribe", "speech_end", "text"), ("agent", "text", "reply"), ("reply -> first audio", "reply", "first_audio")):
            g = self.gap(a, b)
            if g is not None:
                parts.append(f"{label} {g:.2f}s")
        total = self.gap("speech_end", "first_audio")
        if total is not None:
            parts.append(f"= {total:.2f}s from end of speech to first spoken word" + (f" (+{end_silence_s:.1f}s end-of-speech wait)" if end_silence_s else ""))
        return " | ".join(parts)

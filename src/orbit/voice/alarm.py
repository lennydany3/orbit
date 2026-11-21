"""A reminder or a timer that rings until you say you have heard it.

When one comes due Orbit plays its sound over and over (a gentle bell for a reminder, digital beeps for a timer or an alarm),
says what it is, and stops only when you tell it to: "ok", "thanks", "fine", "got it", "stop" ... , or "snooze" /
"remind me again in 5 minutes" to have it ring again later. Holding the talk key or pressing Stop in the window stops it too.

Two parts, both free of the voice loop so they can be tested alone:

  `judge(text)`    what a thing you said means to a ringing alarm: stop it, snooze it, or nothing to do with it
  `AlarmPlayer`    plays one sound in a loop on the speaker, fading out when stopped (no click)

The sounds are in the `alarms` folder next to this file (see the README there for where they come from).
"""
from __future__ import annotations

import math
import re
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import reminders as reminder_text

SOUNDS_DIR = Path(__file__).with_name("alarms")

# Which sound rings for what was asked: a reminder is a nudge, a timer or an alarm clock has to be unmistakable.
SOUND_FOR = {"reminder": "reminder.wav", "timer": "timer.wav", "alarm": "timer.wav"}
FADE_OUT_S = 0.05
DEFAULT_SNOOZE_MIN = 5
MAX_SNOOZE_MIN = 120


def sound_kind(kinds) -> str:
    """The sound for several things that came due together: the louder one wins."""
    return "timer" if any(k in ("timer", "alarm") for k in kinds) else "reminder"


# ---- what you said ------------------------------------------------------------------------------------------------------------

# Said by themselves or in a short sentence, these mean "I have heard it" ("ok thanks for reminding me", "yeah fine").
_SOFT = (r"o\.?k\.?|okay|okey|kay|k|fine|alright|all right|thanks?|thank you|thankyou|thx|tnx|ty|cheers|got it|gotcha|noted|understood|"
         r"i know|i hear you|i heard you|heard you|yes|yeah|yep|yup|sure|cool|good|great|nice|perfect|awesome|done|right|will do|roger|appreciate it|"
         r"i'?m up|i'?m awake|awake")
# These stop it however long the sentence is.
_STRONG = (r"stop|enough|dismiss|silence|quiet|shut up|shush|mute|cancel|turn (?:it|that|this) off|turn off|switch (?:it|that) off|switch off|"
           r"that will do|that'?ll do|no more")
_THANKS = re.compile(r"\b(?:thanks?|thank you|thankyou|thx|tnx|ty|cheers|appreciate)\b")
_SNOOZE = re.compile(r"\b(?:snooze|(?:remind|ring|ask|tell|call|wake) (?:me|us) (?:again|later|in|after|a (?:bit|little) later)|(?:ring|remind) again|"
                     r"again (?:in|after|later)|more (?:minutes?|time)|give me (?:a|an|\d+|\w+) (?:min|hour|sec)|not now|in a bit|after a while|few (?:more )?minutes)\b")
MAX_SOFT_WORDS = 8                  # a longer sentence is probably not meant for the alarm unless it says "stop"


@dataclass(frozen=True)
class Verdict:
    kind: str                       # "dismiss" | "snooze" | "ignore"
    minutes: int = 0                # snooze: for how long
    thanks: bool = False            # dismiss: you said thank you (Orbit answers, "You're welcome.")


def _words(text: str) -> list[str]:
    text = re.sub(r"\bo\.\s?k\.?", "okay", text.lower())
    return re.findall(r"[a-z0-9']+", text)


def judge(text: str) -> Verdict:
    """What `text` (what you said while it rang) means to the alarm."""
    said = " ".join(_words(text))
    if not said:
        return Verdict("ignore")
    if _SNOOZE.search(said):
        seconds = reminder_text.spoken_length(said)
        minutes = DEFAULT_SNOOZE_MIN if seconds is None else min(MAX_SNOOZE_MIN, max(1, math.ceil(seconds / 60)))
        return Verdict("snooze", minutes)
    thanks = bool(_THANKS.search(said))
    if re.search(rf"\b(?:{_STRONG})\b", said):
        return Verdict("dismiss", thanks=thanks)
    if len(said.split()) <= MAX_SOFT_WORDS and re.search(rf"(?<![a-z0-9'])(?:{_SOFT})(?![a-z0-9'])", said):
        return Verdict("dismiss", thanks=thanks)
    return Verdict("ignore")


# ---- the sound ----------------------------------------------------------------------------------------------------------------

_cache: dict[str, tuple[np.ndarray, int]] = {}


def load_sound(kind: str, folder: Path = SOUNDS_DIR) -> tuple[np.ndarray, int]:
    """(mono int16 samples, sample rate) of the sound for `kind`. Raises FileNotFoundError if the file is missing."""
    name = SOUND_FOR.get(kind, SOUND_FOR["reminder"])
    key = f"{folder}/{name}"
    if key not in _cache:
        with wave.open(str(folder / name), "rb") as w:
            if w.getsampwidth() != 2:
                raise ValueError(f"{name} must be a 16-bit WAV")
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if w.getnchannels() > 1:
                data = data.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
            _cache[key] = (data, w.getframerate())
    return _cache[key]


class AlarmPlayer:
    """Plays an alarm sound over and over until `stop()`, which fades it out in 50 ms so it never clicks.

    `on_audio`, if set, is given each block as it is played (the face's sphere pulses with it). `gain` is 0 to 1."""

    def __init__(self, device: str | int | None = None, gain: float = 1.0, folder: Path = SOUNDS_DIR):
        from .audio_io import resolve_device

        self.device = resolve_device(device, "output")
        self.gain = min(1.0, max(0.0, gain))
        self.folder = folder
        self.on_audio = None
        self.kind = ""
        self._stream = None
        self._clip = np.zeros(1, np.int16)
        self._pos = 0
        self._fade = 0                      # samples of fade-out still to play (0 = not stopping)
        self._fade_total = 1
        self._silent = threading.Event()    # set once a fade-out has finished

    @property
    def playing(self) -> bool:
        return self._stream is not None

    def set_volume(self, volume: float) -> None:
        self.gain = min(1.0, max(0.0, float(volume)))

    def set_device(self, spec: str | int | None) -> bool:
        from .audio_io import resolve_device

        device = resolve_device(spec, "output")
        changed = device != self.device
        self.device = device
        return changed

    def ring_seconds(self, kind: str, at_least: float) -> float:
        """How long to ring for about `at_least` seconds without cutting a sound in half: a whole number of loops."""
        clip, rate = load_sound(kind, self.folder)
        once = len(clip) / rate
        return max(1, math.ceil(at_least / once)) * once

    def start(self, kind: str) -> None:
        """Starts the sound for `kind` (a reminder, a timer or an alarm) from its beginning. Raises if it cannot play."""
        import sounddevice as sd

        self.stop()
        clip, rate = load_sound(kind, self.folder)
        self.kind, self._clip, self._pos, self._fade = kind, clip, 0, 0
        self._fade_total = max(1, int(FADE_OUT_S * rate))
        self._silent.clear()
        stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16", device=self.device, callback=self._fill)
        stream.start()
        self._stream = stream

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            self._fade = self._fade_total
            self._silent.wait(0.3)          # the callback plays the fade-out, then goes quiet
            stream.stop()
            stream.close()
        except Exception:  # noqa: BLE001 - a sound card that has gone away must not break the voice
            pass

    def _fill(self, outdata, frames, time_info, status) -> None:
        clip = self._clip
        index = (self._pos + np.arange(frames)) % len(clip)
        block = clip[index].astype(np.float32) * self.gain
        if self._fade:
            left = self._fade
            block *= np.clip((left - np.arange(frames)) / self._fade_total, 0.0, 1.0)
            self._fade = max(0, left - frames)
            if self._fade == 0:
                self._silent.set()
        self._pos = int((self._pos + frames) % len(clip))
        out = np.clip(block, -32768, 32767).astype(np.int16)
        outdata[:, 0] = out
        listener = self.on_audio
        if listener is not None:
            try:
                listener(out)
            except Exception:  # noqa: BLE001
                pass

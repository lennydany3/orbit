"""Wake word. One 80 ms mic frame in, one score out, on one CPU thread.

Two kinds of word, chosen by the file in models/openwakeword/:
  <word>.npz   a word trained here with `python -m orbit.voice.run train-wake` (the custom "hey_orbit"), checked by a short
               local Whisper pass before it counts (WakeVerifier), because a small home-trained model false-triggers more
  <word>.onnx  an openWakeWord pre-trained word (hey_mycroft, hey_jarvis, alexa): no extra check

A "wake" needs `hits` consecutive frames at or above the threshold (a lone spike is ignored), and after one wake further
wakes are ignored for `refractory_s`, so one "hey orbit" is never reported twice.
"""
import re
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

from . import config

_RING_FRAMES = 30   # 2.4 s of audio kept, so a candidate can be checked after the fact


@dataclass
class WakeResult:
    score: float             # best model score for this frame, 0-1
    triggered: bool          # True on the frame where a wake is confirmed
    rejected: bool = False   # a candidate that the Whisper check did not confirm
    heard: str = ""          # what the check heard (when a check ran)


def sounds_like_orbit(text: str) -> bool:
    """True if the transcript contains "orbit" or something that sounds like it ("Orbit.", "or bit", "orbits")."""
    words = re.findall(r"[a-z']+", text.lower())
    pairs = [a + b for a, b in zip(words, words[1:])]      # "or bit" -> "orbit"
    return any(SequenceMatcher(None, w, "orbit").ratio() >= 0.8 for w in words + pairs)


class WakeVerifier:
    """Runs only on candidates (rare): a quick local Whisper pass over the last 2.4 s that must hear "orbit"."""

    def __init__(self, model: str = config.WAKE_VERIFY_MODEL, threads: int = 2):
        from .stt import Transcriber

        self._engine = Transcriber(model, threads=threads, prompt=config.WAKE_VERIFY_PROMPT)
        self.model = model

    def check(self, audio: np.ndarray) -> tuple[bool, str]:
        peak = max(1, int(np.abs(audio).max()))        # a quiet mic (peaks near -23 dBFS) makes Whisper mishear: bring it to -3 dBFS
        loud = np.clip(audio.astype(np.float32) * (0.708 * 32767 / peak), -32768, 32767).astype(np.int16)
        heard = self._engine.transcribe(loud).text
        return sounds_like_orbit(heard), heard


class _CustomScorer:
    """openWakeWord's frozen feature extractor + the classifier trained by wake_train.py (plain numpy)."""

    def __init__(self, path):
        from .wake_train import FRAME, WINDOW, Scorer, extractor

        self.scorer, self._pre, self._silence, self._window = Scorer.load(path), extractor(), np.zeros(FRAME, np.int16), WINDOW
        self.threshold = self.scorer.threshold
        self.reset()

    def reset(self) -> None:
        # The extractor starts from random noise; feeding ~2 s of silence replaces it, exactly as in training.
        for _ in range(26):
            self._pre(self._silence)

    def predict(self, frame: np.ndarray) -> float:
        self._pre(frame)
        return float(self.scorer(self._pre.get_features(self._window).reshape(1, -1))[0])


class WakeWord:
    def __init__(self, model: str = config.WAKEWORD_MODEL, threshold: float | None = None, hits: int = config.WAKE_HITS,
                 refractory_s: float = config.WAKE_REFRACTORY_S, verify: bool | None = None):
        directory = config.WAKEWORD_DIR
        self.name, self.hits, self.refractory_s = model, hits, refractory_s
        self.verifier: WakeVerifier | None = None
        self._ring: deque = deque(maxlen=_RING_FRAMES)
        custom = directory / f"{model}.npz"
        if custom.exists():
            self._model = _CustomScorer(custom)
            self.threshold = threshold if threshold is not None else self._model.threshold
            self.custom = True
            self.predict = self._model.predict
            if config.WAKE_VERIFY if verify is None else verify:
                self.verifier = WakeVerifier()
        else:
            found = sorted(directory.glob(f"{model}*.onnx"))
            if not found:
                raise FileNotFoundError(
                    f"Wake word '{model}' not found in {directory}. A pre-trained word downloads with:\n"
                    f"  python -m orbit.voice.fetch_models wakeword {model}\n"
                    f"A custom word such as hey_orbit is trained with:  python -m orbit.voice.run train-wake")
            from openwakeword.model import Model

            self.threshold = threshold if threshold is not None else config.WAKE_THRESHOLD
            self.custom = False
            self._model = Model(
                wakeword_models=[str(found[0])], inference_framework="onnx",
                melspec_model_path=str(directory / "melspectrogram.onnx"),
                embedding_model_path=str(directory / "embedding_model.onnx"),
                ncpu=1)
            self.predict = self._predict_pretrained
        self.reset()
        self.predict(np.zeros(config.FRAME_SAMPLES, dtype=np.int16))   # warm-up
        self.reset()

    def reset(self) -> None:
        """Forget buffered audio (call when the mic was paused, so old audio is not mixed with new)."""
        self._model.reset()
        self._streak = 0
        self._clock = 0.0                    # seconds of audio seen
        self._blocked_until = 0.0
        self._ring.clear()

    def _predict_pretrained(self, frame: np.ndarray) -> float:
        scores = self._model.predict(frame)
        return float(max(scores.values())) if scores else 0.0

    def process(self, frame: np.ndarray, on_candidate=None) -> WakeResult:
        """frame: int16, 1280 samples (80 ms). `on_candidate()` is called as soon as the model fires, before the (slower)
        Whisper check, so a caller can e.g. mute Orbit's own voice at once and un-mute it if the check says "no"."""
        score = self.predict(frame)
        self._ring.append(frame)
        self._clock += len(frame) / config.SAMPLE_RATE
        self._streak = self._streak + 1 if score >= self.threshold else 0
        if self._streak < self.hits or self._clock < self._blocked_until:
            return WakeResult(score, False)
        self._streak = 0
        heard = ""
        if on_candidate is not None:
            on_candidate()
        if self.verifier is not None:
            ok, heard = self.verifier.check(np.concatenate(self._ring))
            if not ok:
                self._blocked_until = self._clock + config.WAKE_REJECT_COOLDOWN_S
                return WakeResult(score, False, rejected=True, heard=heard)
        self._blocked_until = self._clock + self.refractory_s
        return WakeResult(score, True, heard=heard)


def strip_wake_phrase(text: str, model: str = config.WAKEWORD_MODEL) -> str:
    """Whisper may transcribe the wake phrase itself ("Hey Orbit, open Spotify"); the agent should only see the request."""
    words = r"[\W_]+".join(re.escape(w) for w in model.split("_"))   # "hey orbit" also matches "Hey, Orbit"
    return re.sub(rf"^\W*{words}\b[\s,.!:-]*", "", text, flags=re.I).strip() or text

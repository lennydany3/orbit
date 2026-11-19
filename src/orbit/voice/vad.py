"""Voice activity detection: Silero VAD (ONNX, CPU) plus a segmenter that turns a stream of frames into speech clips.

SileroVAD judges one 32 ms window at a time. SpeechSegmenter feeds it the mic's 80 ms frames, applies
hysteresis, and returns events:  start (speech began)  |  end (a finished utterance, with its audio)
                                 |  ignored (too short to be speech)
The returned clip = a little audio from before speech began + the speech + a short silence tail. Silence is trimmed.
"""
import collections
from dataclasses import dataclass

import numpy as np

from . import config


class SileroVAD:
    """Stateful speech-probability model. One instance per audio stream."""

    def __init__(self, model_path=config.SILERO_MODEL):
        if not model_path.exists():
            raise FileNotFoundError(
                f"Silero VAD model not found at {model_path}. Download it once (see the voice setup notes), "
                "then run again. Orbit never downloads models by itself.")
        import onnxruntime as ort  # imported here so the segmenter can be tested without it

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1   # always-on part: keep it to one thread
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
        if "state" not in {i.name for i in self._session.get_inputs()}:
            raise RuntimeError("This silero_vad.onnx is an old (v4) model; Orbit needs the v5 one (inputs: input, state, sr).")
        self._sr = np.array(config.SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, config.VAD_CONTEXT), dtype=np.float32)

    def __call__(self, window: np.ndarray) -> float:
        """window: float32 in [-1, 1], exactly VAD_WINDOW samples. Returns the probability that it contains speech."""
        x = np.concatenate([self._context, window[None, :]], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -config.VAD_CONTEXT:]
        return float(out[0, 0])


def speech_seconds(vad: SileroVAD, clip: np.ndarray, threshold: float = config.VAD_START_THRESHOLD) -> float:
    """How much of a clip is speech. Used to skip hold-to-talk recordings that are only silence (Whisper invents text for silence)."""
    vad.reset()
    voiced = 0
    for i in range(0, len(clip) - config.VAD_WINDOW + 1, config.VAD_WINDOW):
        voiced += vad(clip[i:i + config.VAD_WINDOW].astype(np.float32) / 32768.0) >= threshold
    vad.reset()
    return voiced * config.VAD_WINDOW / config.SAMPLE_RATE


@dataclass
class VadEvent:
    kind: str                          # "start" | "end" | "ignored"
    t: float                           # seconds on the audio clock (time the segmenter has been fed)
    speech_s: float = 0.0              # seconds of actual speech (end / ignored)
    clip: np.ndarray | None = None     # int16 audio (end only)
    reason: str = ""                   # "silence" or "max_length" (end only)


class SpeechSegmenter:
    def __init__(
        self,
        vad,
        start_threshold: float = config.VAD_START_THRESHOLD,
        end_threshold: float = config.VAD_END_THRESHOLD,
        start_windows: int = config.VAD_START_WINDOWS,
        end_silence_s: float = config.VAD_END_SILENCE_S,
        min_speech_s: float = config.VAD_MIN_SPEECH_S,
        preroll_s: float = config.VAD_PREROLL_S,
        keep_tail_s: float = config.VAD_KEEP_TAIL_S,
        max_utterance_s: float = config.VAD_MAX_UTTERANCE_S,
    ):
        self.vad = vad
        self.start_threshold, self.end_threshold = start_threshold, end_threshold
        self.start_windows, self.min_speech_s = start_windows, min_speech_s
        self.window = config.VAD_WINDOW
        self.window_s = self.window / config.SAMPLE_RATE
        self._end_silence = round(end_silence_s / self.window_s)    # all limits below are counted in windows
        self._keep_tail = round(keep_tail_s / self.window_s)
        self._max_windows = round(max_utterance_s / self.window_s)
        self._preroll: collections.deque = collections.deque(maxlen=round(preroll_s / self.window_s) + start_windows)
        self.last_prob = 0.0
        self.reset()

    def reset(self) -> None:
        """Forget everything (call after the mic was paused, so old audio is not glued to new audio)."""
        self.vad.reset()
        self.speaking = False
        self._buffer = np.zeros(0, dtype=np.int16)
        self._clip: list[np.ndarray] = []
        self._preroll.clear()
        self._highs = 0       # consecutive speech windows while waiting to start
        self._silence = 0     # consecutive non-speech windows while speaking
        self._voiced = 0      # speech windows in this utterance
        self._windows = 0     # windows processed: the audio clock

    def feed(self, frame: np.ndarray) -> list[VadEvent]:
        """Give it the next mic frame (int16, any length). Returns the events that frame produced (usually none)."""
        events = []
        self._buffer = np.concatenate([self._buffer, frame])
        while len(self._buffer) >= self.window:
            window, self._buffer = self._buffer[: self.window], self._buffer[self.window:]
            self.last_prob = self.vad(window.astype(np.float32) / 32768.0)
            event = self._step(window, self.last_prob)
            if event:
                events.append(event)
            self._windows += 1
        return events

    def _step(self, window: np.ndarray, prob: float) -> VadEvent | None:
        now = self._windows * self.window_s
        if not self.speaking:
            self._preroll.append(window)
            self._highs = self._highs + 1 if prob >= self.start_threshold else 0
            if self._highs < self.start_windows:
                return None
            self.speaking = True
            self._clip = list(self._preroll)
            self._silence, self._voiced = 0, self._highs
            started = now - (self._highs - 1) * self.window_s
            self._highs = 0
            return VadEvent("start", started)

        self._clip.append(window)
        if prob >= self.end_threshold:
            self._silence, self._voiced = 0, self._voiced + 1
        else:
            self._silence += 1
        if self._silence >= self._end_silence:
            return self._finish(now, "silence")
        if len(self._clip) >= self._max_windows:
            return self._finish(now, "max_length")
        return None

    def _finish(self, now: float, reason: str) -> VadEvent:
        speech_s = self._voiced * self.window_s
        drop = max(0, self._silence - self._keep_tail)    # trim trailing silence down to the tail we keep
        clip = np.concatenate(self._clip[: len(self._clip) - drop])
        self.speaking = False
        self._clip, self._preroll = [], collections.deque(maxlen=self._preroll.maxlen)
        self._highs = self._silence = self._voiced = 0
        end = now + self.window_s
        if speech_s < self.min_speech_s:
            return VadEvent("ignored", end, speech_s=speech_s)
        return VadEvent("end", end, speech_s=speech_s, clip=clip, reason=reason)

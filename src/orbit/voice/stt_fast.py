"""Fast local speech-to-text, built the way Handy (github.com/cjpais/Handy) makes its dictation fast.

What makes it fast, and where each idea lives here:

  1. A fast model. NVIDIA Parakeet TDT 0.6B, int8, run by sherpa-onnx on the CPU. Parakeet is a transducer: it reads the audio once
     and emits words, with no 30-second padded window like Whisper's encoder, so a 2 s command costs about 2 s worth of compute,
     not 30 s worth. (ParakeetTranscriber)
  2. Loaded once and kept warm. The model is loaded and run once on silence at start-up, then stays in memory for every turn.
  3. Only speech reaches it. The Silero VAD already trims silence on the capture path (vad.py); the model never sees silence.
  4. Decoding while you are still talking. While the talk key is held, LiveDecode cuts the audio at natural pauses and decodes each
     finished piece in the background, so when you let go only the last piece is left (Handy's live long-form chunking).
  5. Work off the mic thread. Decoding runs in one worker thread, in order; the mic loop never waits for it.

Same interface as stt.Transcriber: name, model_name, load_s, transcribe(clip) -> Transcript, submit(clip) -> Future, close().
Nothing here touches the network: the model is downloaded once, by hand or by the first-run provisioning (fetch_models.py).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

from . import config
from .stt import Transcript, apply_vocab_fixes

PARAKEET_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")


def parakeet_ready(model: str = config.PARAKEET_MODEL) -> bool:
    """Are all the model's files downloaded?"""
    path = config.PARAKEET_DIR / model
    return all((path / name).exists() for name in PARAKEET_FILES)


class ParakeetTranscriber:
    name = "parakeet"
    live_chunking = True                          # cheap enough to decode pieces while you talk (see LiveDecode)

    def __init__(self, model: str = config.PARAKEET_MODEL, threads: int = config.PARAKEET_THREADS):
        path = config.PARAKEET_DIR / model
        missing = [name for name in PARAKEET_FILES if not (path / name).exists()]
        if missing:
            raise FileNotFoundError(
                f"Parakeet model '{model}' not found in {path} (missing {', '.join(missing)}). Download it once with:\n"
                f"  python -m orbit.voice.fetch_models parakeet {model}")
        import sherpa_onnx  # heavy import: only when speech-to-text is actually used

        self.model_name, self.threads = model, threads
        start = time.perf_counter()
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(path / "encoder.int8.onnx"), decoder=str(path / "decoder.int8.onnx"),
            joiner=str(path / "joiner.int8.onnx"), tokens=str(path / "tokens.txt"),
            num_threads=threads, sample_rate=config.SAMPLE_RATE, decoding_method="greedy_search",
            model_type="nemo_transducer", provider="cpu")
        self.transcribe(np.zeros(config.SAMPLE_RATE, dtype=np.int16))   # warm-up: the first call pays one-time setup costs
        self.load_s = time.perf_counter() - start
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")   # one worker: chunks decode in order

    def transcribe(self, clip: np.ndarray) -> Transcript:
        """Blocking. clip: int16 mono 16 kHz."""
        start = time.perf_counter()
        audio = np.asarray(clip, dtype=np.float32) / 32768.0
        # A short run of silence after the last word: a clip cut right at a word's end (letting go of the key) can lose that word.
        pad = np.zeros(int(config.PARAKEET_TAIL_PAD_S * config.SAMPLE_RATE), dtype=np.float32)
        stream = self._recognizer.create_stream()
        stream.accept_waveform(config.SAMPLE_RATE, np.concatenate([audio, pad]))
        self._recognizer.decode_stream(stream)
        text = apply_vocab_fixes(stream.result.text.strip())
        return Transcript(text, time.perf_counter() - start, len(clip) / config.SAMPLE_RATE)

    def submit(self, clip: np.ndarray) -> "Future[Transcript]":
        """Non-blocking: queue the clip for the worker thread and return a Future."""
        return self._pool.submit(self.transcribe, clip)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def join_transcripts(parts: list[Transcript], seconds: float, audio_s: float) -> Transcript:
    """One transcript from the pieces of one recording, in order."""
    return Transcript(" ".join(p.text for p in parts if p.text).strip(), seconds, audio_s)


class LiveDecode:
    """Decodes one hold-to-talk recording while it is still being recorded.

    Every mic frame goes in as it arrives (`feed`). Once at least `min_chunk_s` of audio has built up, the next pause of
    `cut_silence_s` cuts it: the piece up to the middle of that pause is queued for decoding at once, and the rest carries on.
    Cuts only happen in silence, never inside a word, so each piece decodes as well as the whole would. When you let go,
    `finish(clip)` queues only the last piece and returns one Future for the whole text.

    `vad` scores 32 ms windows (a SileroVAD of its own: it is stateful). This runs on the mic thread, like the segmenter does.
    """

    def __init__(self, transcriber, vad, *, min_chunk_s: float = config.LIVE_MIN_CHUNK_S,
                 cut_silence_s: float = config.LIVE_CUT_SILENCE_S, threshold: float = config.VAD_END_THRESHOLD,
                 clock=time.perf_counter):
        self.transcriber, self.vad, self.threshold, self._clock = transcriber, vad, threshold, clock
        self.window = config.VAD_WINDOW
        self._min_chunk = int(min_chunk_s * config.SAMPLE_RATE)
        self._cut_windows = max(1, round(cut_silence_s * config.SAMPLE_RATE / self.window))
        self.samples = 0                              # samples fed so far
        self._chunk: list[np.ndarray] = []            # audio since the last cut
        self._chunk_len = 0
        self._unscored = np.zeros(0, dtype=np.int16)  # the part of the last frame that does not fill a whole VAD window yet
        self._silent = 0                              # consecutive quiet windows at the end of what was scored
        self._heard = False                           # speech in the current piece?
        self.futures: list[Future] = []               # pieces queued for decoding, in order
        self.vad.reset()

    @property
    def pieces(self) -> int:
        return len(self.futures)

    def feed(self, frame: np.ndarray) -> None:
        frame = np.asarray(frame, dtype=np.int16)
        if not len(frame):
            return
        self._chunk.append(frame)
        self._chunk_len += len(frame)
        self.samples += len(frame)
        self._unscored = np.concatenate([self._unscored, frame])
        while len(self._unscored) >= self.window:
            window, self._unscored = self._unscored[: self.window], self._unscored[self.window:]
            if self.vad(window.astype(np.float32) / 32768.0) >= self.threshold:
                self._silent, self._heard = 0, True
            else:
                self._silent += 1
            if self._heard and self._silent == self._cut_windows and self._chunk_len >= self._min_chunk:
                self._cut()

    def _cut(self) -> None:
        """Queue the audio up to the middle of the current pause; keep the rest as the start of the next piece."""
        audio = np.concatenate(self._chunk)
        end_of_scored = len(audio) - len(self._unscored)            # samples in this piece that the VAD has judged
        at = end_of_scored - (self._silent * self.window) // 2      # the middle of the pause
        head, tail = audio[:at], audio[at:]
        self.futures.append(self.transcriber.submit(head))
        self._chunk, self._chunk_len = [tail], len(tail)
        self._silent, self._heard = 0, False

    def finish(self, clip: np.ndarray) -> Future | None:
        """The whole recording's text, with only the last piece left to decode. None when nothing was cut while recording
        (a short request: decoding it in one go is just as fast) or when `clip` is not exactly what was fed (the caller then
        decodes `clip` itself, so a mismatch can never lose words)."""
        if not self.futures or self.samples != len(clip):
            self.cancel()
            return None
        started = self._clock()
        parts = self.futures + [self.transcriber.submit(np.concatenate(self._chunk))]
        self.futures = []
        result: Future = Future()

        def join() -> None:
            try:
                transcripts = [f.result() for f in parts]
                result.set_result(join_transcripts(transcripts, self._clock() - started, len(clip) / config.SAMPLE_RATE))
            except BaseException as e:  # noqa: BLE001 - handed to whoever waits on the Future
                result.set_exception(e)

        threading.Thread(target=join, daemon=True, name="stt-join").start()
        return result

    def cancel(self) -> None:
        """Drop the pieces not started yet (the recording was a tap, or had no speech)."""
        for future in self.futures:
            future.cancel()
        self.futures = []

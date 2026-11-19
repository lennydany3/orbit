"""Speech-to-text providers and the router that picks between them.

  GroqSTT    Groq's OpenAI-compatible transcription endpoint (whisper-large-v3-turbo). Needs internet and GROQ_API_KEY.
  LocalSTT   the faster-whisper CPU setup from stt.py, unchanged.
  STTRouter  "auto": Groq first; local on no key, no internet, timeout, HTTP 429 or any other Groq failure.
             Every fallback is logged with its reason. It never raises: a failed turn comes back with empty text.

Every provider takes a 16 kHz mono WAV (bytes) and returns an STTResult. Only the VAD-trimmed speech clip of a turn
is ever passed in, and only after the wake word or hotkey fired. An empty clip is never sent anywhere.
The API key is read from the environment at call time, is never stored on an object, and never appears in a log or error.
"""
import io
import os
import re
import threading
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import httpx
import numpy as np

from . import config


class STTUnavailable(Exception):
    """A provider cannot be used right now. `reason` is short and safe to log."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class STTResult:
    text: str
    provider: str               # "groq" | "local" | "none" (nothing was transcribed)
    seconds: float              # wall time spent by the provider that produced this result
    fallback_reason: str = ""   # set when local was used because Groq could not be
    error: str = ""             # set when no provider produced text; the agent must not run


# ---- audio helpers ----
def clip_to_wav(clip: np.ndarray, sample_rate: int = config.SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(np.asarray(clip, dtype=np.int16).tobytes())
    return buffer.getvalue()


def wav_to_clip(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """(int16 mono samples, sample rate). Raises ValueError for anything that is not a 16-bit mono WAV."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise ValueError("expected a 16-bit mono WAV")
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate()
    except (wave.Error, EOFError) as e:
        raise ValueError(f"unreadable WAV: {e}") from e


def _scrub(text: str) -> str:
    """Belt and braces: anything shaped like a Groq key is masked before it can reach a log."""
    return re.sub(r"gsk_[A-Za-z0-9]+", "gsk_***", text)


# ---- providers ----
class GroqSTT:
    name = "groq"

    def __init__(self, model: str = config.GROQ_MODEL, url: str = config.GROQ_URL, timeout_s: float = config.GROQ_TIMEOUT_S,
                 prompt: str = config.WHISPER_PROMPT, key_env: str = config.GROQ_KEY_ENV,
                 api_key: str | None = None, client: httpx.Client | None = None):
        self.model, self.url, self.timeout_s, self.prompt, self._key_env = model, url, timeout_s, prompt, key_env
        self._api_key = api_key   # tests only; normally None, and the key comes from the environment at call time
        # One client for the whole run: keeps the connection alive, so later turns skip the TLS handshake.
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout_s, connect=min(3.0, timeout_s)))

    def __repr__(self) -> str:
        return f"GroqSTT(model={self.model!r})"

    def transcribe(self, wav_bytes: bytes, sample_rate: int = config.SAMPLE_RATE) -> STTResult:
        key = (self._api_key or os.environ.get(self._key_env, "")).strip()
        if not key:
            raise STTUnavailable(f"no API key ({self._key_env} is not set)")
        data = {"model": self.model, "language": "en", "temperature": "0", "response_format": "json"}
        if self.prompt:
            data["prompt"] = self.prompt
        start = time.perf_counter()
        try:
            response = self._client.post(self.url, headers={"Authorization": f"Bearer {key}"}, data=data,
                                         files={"file": ("clip.wav", wav_bytes, "audio/wav")})
        except httpx.TimeoutException:
            raise STTUnavailable(f"timeout (no answer within {self.timeout_s:g}s)") from None
        except httpx.TransportError:
            raise STTUnavailable("no internet (cannot reach Groq)") from None
        if response.status_code == 429:
            raise STTUnavailable("rate limited (HTTP 429)")
        if response.status_code != 200:
            raise STTUnavailable(f"HTTP {response.status_code}: {self._error_message(response)}")
        try:
            text = str(response.json()["text"]).strip()
        except (ValueError, KeyError, TypeError):
            raise STTUnavailable("unreadable answer from Groq") from None
        return STTResult(text, "groq", time.perf_counter() - start)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            return _scrub(str(response.json()["error"]["message"]))[:100]
        except (ValueError, KeyError, TypeError):
            return "no details"


class LocalSTT:
    name = "local"

    def __init__(self, model: str = config.WHISPER_MODEL, threads: int = config.WHISPER_THREADS):
        self.model, self.threads = model, threads
        self._engine = None
        self._lock = threading.Lock()

    @property
    def engine(self):
        """The faster-whisper Transcriber from stt.py, loaded on first use (or by the router's background preload)."""
        with self._lock:
            if self._engine is None:
                from .stt import Transcriber

                self._engine = Transcriber(self.model, threads=self.threads)
            return self._engine

    def transcribe(self, wav_bytes: bytes, sample_rate: int = config.SAMPLE_RATE) -> STTResult:
        clip, rate = wav_to_clip(wav_bytes)
        if rate != config.SAMPLE_RATE:
            raise ValueError(f"local speech-to-text needs {config.SAMPLE_RATE} Hz audio, got {rate} Hz")
        result = self.engine.transcribe(clip)
        return STTResult(result.text, "local", result.seconds)


# ---- router ----
class STTRouter:
    def __init__(self, provider: str = config.STT_PROVIDER, groq: GroqSTT | None = None, local: LocalSTT | None = None,
                 log=print, preload_local: bool = config.STT_PRELOAD_LOCAL):
        if provider not in ("groq", "local", "auto"):
            raise ValueError(f"stt provider must be groq, local or auto (got '{provider}')")
        self.provider, self.log = provider, log
        self.groq = groq or GroqSTT()
        self.local = local or LocalSTT()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")
        if preload_local and provider in ("local", "auto") and hasattr(self.local, "engine"):
            threading.Thread(target=self._preload, name="stt-preload", daemon=True).start()

    def _preload(self) -> None:
        try:
            self.local.engine
        except Exception as e:
            self.log(f"  [stt] local Whisper could not be loaded ({e})")

    def submit(self, wav_bytes: bytes, sample_rate: int = config.SAMPLE_RATE) -> "Future[STTResult]":
        """Non-blocking: transcribe in a worker thread so the mic loop never waits."""
        return self._pool.submit(self.transcribe, wav_bytes, sample_rate)

    def transcribe(self, wav_bytes: bytes, sample_rate: int = config.SAMPLE_RATE) -> STTResult:
        empty = self._empty_reason(wav_bytes)
        if empty:
            self.log(f"  [stt] {empty}: nothing sent, agent will not run")
            return STTResult("", "none", 0.0, error=empty)
        if self.provider == "local":
            return self._run_local(wav_bytes, sample_rate)
        start = time.perf_counter()
        try:
            return self.groq.transcribe(wav_bytes, sample_rate)
        except STTUnavailable as e:
            if self.provider == "groq":
                self.log(f"  [stt] Groq failed ({e.reason}); provider is 'groq', so there is no fallback")
                return STTResult("", "groq", time.perf_counter() - start, error=e.reason)
            self.log(f"  [stt] Groq unavailable ({e.reason}); using local Whisper")
            result = self._run_local(wav_bytes, sample_rate)
            result.fallback_reason = e.reason
            return result

    def _run_local(self, wav_bytes: bytes, sample_rate: int) -> STTResult:
        start = time.perf_counter()
        try:
            return self.local.transcribe(wav_bytes, sample_rate)
        except Exception as e:  # missing model, bad audio ...: report, never crash the voice loop
            reason = _scrub(f"local Whisper failed: {e}")
            self.log(f"  [stt] {reason}")
            return STTResult("", "local", time.perf_counter() - start, error=reason)

    @staticmethod
    def _empty_reason(wav_bytes: bytes) -> str:
        try:
            clip, rate = wav_to_clip(wav_bytes)
        except ValueError as e:
            return f"unreadable audio ({e})"
        if len(clip) / rate < config.STT_MIN_CLIP_S:
            return "empty clip (too short)"
        if not clip.any():
            return "empty clip (silence)"
        return ""

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        client = getattr(self.groq, "_client", None)
        if client is not None:
            client.close()

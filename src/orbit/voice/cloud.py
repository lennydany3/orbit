"""Sarvam AI speech: speech-to-text (Saarika) and its shared plumbing (API key, HTTP client, offline cool-down).

Only the speech clip that the local voice-activity detector already trimmed is ever uploaded. The wake word, the voice-activity
detector and the echo canceller stay on this PC, because they listen to the microphone all the time.
"""
import io
import os
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor

import httpx
import numpy as np

from . import config
from .stt import Transcript


def sarvam_key() -> str | None:
    return os.environ.get("SARVAM_API_KEY") or None


class CloudError(RuntimeError):
    """Sarvam could not be used (no network, bad key, rate limit). The caller falls back to the local engine."""


class SarvamSession:
    """One HTTP client with the key attached, plus a cool-down: after a failure the cloud is skipped for a while, so an
    offline PC does not wait out a network timeout on every single sentence."""

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None, timeout: float = config.SARVAM_TIMEOUT_S,
                 cooldown_s: float = config.SARVAM_COOLDOWN_S, clock=time.monotonic):
        key = api_key or sarvam_key()
        if not key:
            raise CloudError("SARVAM_API_KEY is not set. Put it in the .env file (see .env.example) and start Orbit again.")
        self._headers = {"api-subscription-key": key}
        self.client = client or httpx.Client(base_url=config.SARVAM_BASE_URL, timeout=timeout)
        self.cooldown_s, self._clock, self._down_until = cooldown_s, clock, 0.0

    def post(self, path: str, **kwargs) -> dict:
        if self._clock() < self._down_until:
            raise CloudError("Sarvam was unreachable a moment ago; using the local engine for now")
        try:
            resp = self.client.post(path, headers=self._headers, **kwargs)
        except httpx.HTTPError as e:
            self._down_until = self._clock() + self.cooldown_s
            raise CloudError(f"cannot reach Sarvam ({type(e).__name__})") from e
        if resp.status_code != 200:
            if resp.status_code in (401, 403):
                self._down_until = self._clock() + self.cooldown_s      # a bad key will not fix itself in the next second
                raise CloudError("Sarvam rejected the API key (check SARVAM_API_KEY)")
            if resp.status_code == 429 or resp.status_code >= 500:
                self._down_until = self._clock() + min(self.cooldown_s, 15.0)
            raise CloudError(f"Sarvam error {resp.status_code}: {_error_text(resp)}")
        try:
            return resp.json()
        except ValueError as e:
            raise CloudError("Sarvam sent a reply Orbit could not read") from e


def _error_text(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", resp.json())
        return str(err.get("message") or err)[:200] if isinstance(err, dict) else str(err)[:200]
    except (ValueError, AttributeError):
        return resp.text[:200]


def wav_bytes(clip: np.ndarray, rate: int = config.SAMPLE_RATE) -> bytes:
    """int16 mono samples -> an in-memory WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(clip, dtype=np.int16).tobytes())
    return buf.getvalue()


class SarvamTranscriber:
    """Same interface as stt.Transcriber (transcribe / submit / close), but the work happens at Sarvam."""
    name = "sarvam"

    def __init__(self, session: SarvamSession | None = None, model: str = config.SARVAM_STT_MODEL,
                 language: str = config.SARVAM_LANGUAGE):
        self.session, self.model, self.language = session or SarvamSession(), model, language
        self.model_name, self.load_s = model, 0.0
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt-cloud")

    def transcribe(self, clip: np.ndarray) -> Transcript:
        """Blocking. clip: int16 mono 16 kHz. Raises CloudError if Sarvam cannot be used."""
        start = time.perf_counter()
        data = self.session.post(
            "/speech-to-text",
            files={"file": ("speech.wav", wav_bytes(clip), "audio/wav")},
            data={"model": self.model, "language_code": self.language})
        text = (data.get("transcript") or "").strip()
        return Transcript(text, time.perf_counter() - start, len(clip) / config.SAMPLE_RATE)

    def submit(self, clip: np.ndarray) -> "Future[Transcript]":
        return self._pool.submit(self.transcribe, clip)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class FallbackTranscriber:
    """Sarvam first; if it cannot be used, the local Whisper model (loaded the first time it is needed, not at start-up)."""

    def __init__(self, primary, local_factory, log=print):
        self.primary, self._local_factory, self._local, self.log = primary, local_factory, None, log
        self.name, self.model_name, self.load_s = primary.name, primary.model_name, primary.load_s

    def transcribe(self, clip: np.ndarray) -> Transcript:
        try:
            return self.primary.transcribe(clip)
        except CloudError as e:
            self.log(f"  [voice] cloud speech-to-text unavailable ({e}); using local Whisper")
        if self._local is None:
            self._local = self._local_factory()
        return self._local.transcribe(clip)

    def submit(self, clip: np.ndarray) -> "Future[Transcript]":
        return self.primary._pool.submit(self.transcribe, clip)

    def close(self) -> None:
        self.primary.close()
        if self._local is not None:
            self._local.close()


def build_transcriber(provider: str = config.STT_PROVIDER, log=print):
    """Sarvam (with local Whisper as the fallback) when a key is set, otherwise local Whisper only."""
    from .stt import Transcriber

    if provider not in ("auto", "sarvam", "local"):
        raise ValueError(f"Unknown speech-to-text provider '{provider}'. Use auto, sarvam or local.")
    use_cloud = provider == "sarvam" or (provider == "auto" and sarvam_key())
    if not use_cloud:
        return Transcriber()
    return FallbackTranscriber(SarvamTranscriber(), Transcriber, log)

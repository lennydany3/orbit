"""Speech-to-text: faster-whisper on the CPU, loaded once, run in a worker thread so the mic loop never waits for it.

The default engine is now Parakeet (stt_fast.py, much faster on short commands); Whisper is the fallback when Parakeet's model is
not downloaded, and it still checks the wake word (wakeword.py). build_transcriber() below picks the engine.

ctranslate2 (the engine under faster-whisper) releases Python's GIL while it computes, so the mic/VAD loop in the
main thread keeps its 80 ms rhythm while a transcription runs. Process priority is lowered by monitor.lower_priority().
Only VAD-trimmed speech clips should ever be passed in, never silence (Whisper invents text for silence).
"""
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from . import config


@dataclass
class Transcript:
    text: str
    seconds: float    # wall time the transcription took
    audio_s: float    # length of the clip

    @property
    def realtime_factor(self) -> float:
        """Seconds of compute per second of audio (0.2 = five times faster than real time)."""
        return self.seconds / self.audio_s if self.audio_s else 0.0


class Transcriber:
    def __init__(
        self,
        model: str = config.WHISPER_MODEL,
        threads: int = config.WHISPER_THREADS,
        compute_type: str = config.WHISPER_COMPUTE,
        beam_size: int = config.WHISPER_BEAM,
        prompt: str = config.WHISPER_PROMPT,
    ):
        path = config.WHISPER_DIR / model
        if not (path / "model.bin").exists():
            raise FileNotFoundError(
                f"Whisper model '{model}' not found in {path}. Download it once with:\n"
                f"  python -m orbit.voice.fetch_models whisper {model}")
        from faster_whisper import WhisperModel  # heavy import: only when speech-to-text is actually used

        self.model_name, self.threads, self.beam_size, self.prompt = model, threads, beam_size, prompt
        start = time.perf_counter()
        self._model = WhisperModel(str(path), device="cpu", compute_type=compute_type, cpu_threads=threads, num_workers=1)
        self.transcribe(np.zeros(config.SAMPLE_RATE, dtype=np.int16))   # warm-up: the first call pays one-time setup costs
        self.load_s = time.perf_counter() - start                       # load + warm-up, paid once at start-up
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")

    def transcribe(self, clip: np.ndarray) -> Transcript:
        """Blocking. clip: int16 mono 16 kHz."""
        audio = clip.astype(np.float32) / 32768.0
        start = time.perf_counter()
        segments, _info = self._model.transcribe(
            audio, language="en", beam_size=self.beam_size, temperature=0.0,
            condition_on_previous_text=False, without_timestamps=True, vad_filter=False,
            initial_prompt=self.prompt or None)
        text = " ".join(s.text.strip() for s in segments).strip()   # the generator does the work: consume it inside the timer
        return Transcript(text, time.perf_counter() - start, len(clip) / config.SAMPLE_RATE)

    def submit(self, clip: np.ndarray) -> "Future[Transcript]":
        """Non-blocking: queue the clip for the worker thread and return a Future."""
        return self._pool.submit(self.transcribe, clip)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# ---- which engine turns speech into text ----
def build_transcriber(provider: str = config.STT_PROVIDER, log=print):
    """The speech-to-text engine. Everything runs on this PC; nothing is sent anywhere.

    auto     = Parakeet (fast, see stt_fast.py) when its model is downloaded, otherwise Whisper
    parakeet = Parakeet only (an error if it cannot load)
    whisper  = faster-whisper only (the engine Orbit used before)"""
    provider = {"local": "whisper"}.get(provider, provider)       # "local" was the old name for Whisper
    if provider not in ("auto", "parakeet", "whisper"):
        raise ValueError(f"Unknown speech-to-text engine '{provider}'. Use auto, parakeet or whisper.")
    if provider == "whisper":
        return Transcriber()
    from .stt_fast import ParakeetTranscriber

    if provider == "parakeet":
        return ParakeetTranscriber()
    try:
        return ParakeetTranscriber()
    except (FileNotFoundError, ImportError, RuntimeError) as e:
        log(f"  [voice] fast speech-to-text (Parakeet) unavailable ({str(e).splitlines()[0]}); using Whisper")
    return Transcriber()


# ---- vocabulary fixes for engines that cannot take a hint (Parakeet has no initial_prompt) ----
VOCAB_FIXES: tuple[tuple[str, str], ...] = (
    (r"\bget ?hub\b", "GitHub"),
    (r"\bgit ?hub\b", "GitHub"),
    (r"\byou ?tube\b", "YouTube"),
    (r"\bspot ?ify\b", "Spotify"),
    (r"\becho ?pop\b", "Echo Pop"),
    (r"\blow[ -]?fi\b", "lofi"),
    (r"\blo-fi\b", "lofi"),
    (r"^(hey )?orbits?\b", lambda m: ("Hey " if m.group(1) else "") + "Orbit"),      # "orbits, open notepad" -> "Orbit, ..."
)


def apply_vocab_fixes(text: str) -> str:
    """Spells the names Orbit cares about the way the rest of Orbit expects them ("get hub" -> "GitHub")."""
    for pattern, replacement in VOCAB_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


# ---- accuracy helpers (used by the benchmark and the fixture tests) ----
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    return str(n)


def normalize(text: str) -> list[str]:
    """Lower-case words with punctuation removed and small numbers spelled out, so "Volume 30." == "volume thirty"."""
    text = text.lower().replace("%", " percent ")
    text = re.sub(r"\d+", lambda m: " " + _number_words(int(m.group())) + " ", text)
    return re.sub(r"[^a-z' ]+", " ", text).split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """(substitutions + deletions + insertions) / reference words. 0.0 = perfect."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1] / len(ref)

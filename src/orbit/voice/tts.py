"""Text-to-speech: Sarvam AI (cloud) when a key is set, else Piper (neural, CPU, offline), with the built-in Windows voice (pyttsx3) last.

Piper yields one chunk of audio per sentence. A producer thread synthesizes the next sentence while the
current one is playing, so the first sentence starts after one sentence's worth of synthesis, not the whole reply.
`stop()` interrupts speech within about 100 ms (the hotkey barge-in in V6 will call it).
"""
import base64
import io
import queue
import re
import threading
import time
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import sounddevice as sd

from . import config
from .aec import ReferenceTimeline
from .audio_io import resolve_device

_BLOCK = 2048              # samples written to the sound card at a time (about 90 ms): bounds how long stop() can take
_SENTENCE_PAUSE_S = 0.12   # Piper adds no gap between sentences; this makes the cadence natural


@dataclass
class SpeakStats:
    engine: str                        # "piper", "windows" or "text" (nothing could speak)
    total_s: float
    first_audio_s: float | None = None  # speak() call -> first audio handed to the sound device (not counting Bluetooth/driver buffering)
    synth_s: float | None = None        # CPU time spent synthesizing, summed over sentences
    audio_s: float | None = None        # seconds of speech produced
    interrupted: bool = False

    @property
    def synth_realtime_factor(self) -> float | None:
        """Synthesis seconds per second of speech (0.1 = ten times faster than real time)."""
        return self.synth_s / self.audio_s if self.synth_s is not None and self.audio_s else None


class StreamingTTS:
    """Plays speech one sentence at a time. A producer thread fetches/synthesizes the next sentence while the current one plays.

    Every block handed to the sound card is also recorded in `reference` (16 kHz) for the echo canceller, so barge-in and
    "hey orbit" during speech work the same no matter which engine produced the audio. Subclasses set `sample_rate` and
    implement `sentences(text)`, which yields int16 mono arrays, one per sentence."""
    name = "tts"
    sample_rate = 22_050

    def _init_player(self, device, reference: ReferenceTimeline | None) -> None:
        self.reference = reference     # when set, every block played is also recorded here (16 kHz) for echo cancellation
        self.device = resolve_device(device, "output")
        self._stop = threading.Event()
        self._run = threading.Event()                 # cleared = speech paused (nothing more is written to the sound card)
        self._run.set()
        self.supports_pause = True

    def sentences(self, text: str) -> Iterator[np.ndarray]:
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()
        self._run.set()                                # release a paused speaker so it can notice the stop

    def pause(self) -> None:
        """Mute at once: the sound card plays out what it already has (about 0.1-0.3 s), then silence."""
        self._run.clear()

    def resume(self) -> None:
        self._run.set()

    def speak(self, text: str) -> SpeakStats:
        self._stop.clear()
        self._run.set()
        start = time.perf_counter()
        audio_q: queue.Queue = queue.Queue(maxsize=3)   # at most 3 sentences ready ahead of playback
        synth = {"s": 0.0, "error": None}

        def produce() -> None:
            try:
                sentences = self.sentences(text)
                while not self._stop.is_set():
                    t0 = time.perf_counter()
                    audio = next(sentences, None)
                    synth["s"] += time.perf_counter() - t0
                    if audio is None:
                        break
                    audio_q.put(audio)
            except Exception as e:  # reported by speak() after playback stops
                synth["error"] = e
            finally:
                audio_q.put(None)

        worker = threading.Thread(target=produce, name="tts-synth", daemon=True)
        worker.start()
        first_audio, played, interrupted, first_sentence = None, 0, False, True
        stream = sd.OutputStream(samplerate=self.sample_rate, channels=1, dtype="int16", device=self.device)
        stream.start()
        try:
            while True:
                audio = audio_q.get()
                if audio is None:
                    break
                if not first_sentence:
                    audio = np.concatenate([np.zeros(int(_SENTENCE_PAUSE_S * self.sample_rate), np.int16), audio])
                first_sentence = False
                ref16 = None
                if self.reference is not None:               # the sentence at 16 kHz, resampled as a whole (no seams between blocks)
                    from scipy.signal import resample_poly

                    ref16 = resample_poly(audio.astype(np.float32), config.SAMPLE_RATE, self.sample_rate)
                for i in range(0, len(audio), _BLOCK):
                    while not self._run.is_set() and not self._stop.is_set():
                        time.sleep(0.01)                        # paused
                    if self._stop.is_set():
                        interrupted = True
                        break
                    stream.write(audio[i:i + _BLOCK].reshape(-1, 1))
                    if ref16 is not None:
                        a = round(i * config.SAMPLE_RATE / self.sample_rate)
                        b = round(min(i + _BLOCK, len(audio)) * config.SAMPLE_RATE / self.sample_rate)
                        self.reference.add(ref16[a:b].astype(np.int16), time.perf_counter())
                    if first_audio is None:
                        first_audio = time.perf_counter() - start
                    played += min(_BLOCK, len(audio) - i)
                if interrupted:
                    break
        finally:
            self._stop.set()     # tells the producer to stop if we left early
            while worker.is_alive():   # unblock a producer waiting on a full queue
                try:
                    audio_q.get_nowait()
                except queue.Empty:
                    worker.join(timeout=0.05)
            (stream.abort if interrupted else stream.stop)()   # stop() lets the buffered audio finish; abort() cuts it
            stream.close()
        # An error before anything was heard lets the next engine speak the whole reply. After speech has started, repeating
        # it from the top would be worse than a cut-off sentence, so the stats are returned as they are.
        if synth["error"] is not None and played == 0:
            raise RuntimeError(f"{self.name} failed: {synth['error']}")
        return SpeakStats(self.name, time.perf_counter() - start, first_audio, synth["s"], played / self.sample_rate, interrupted)


class PiperTTS(StreamingTTS):
    name = "piper"

    def __init__(self, voice: str = config.PIPER_VOICE, device: str | int | None = config.SPEAKER_DEVICE,
                 threads: int = 2, length_scale: float = config.PIPER_LENGTH_SCALE, reference: ReferenceTimeline | None = None):
        model = config.PIPER_DIR / f"{voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(
                f"Piper voice '{voice}' not found in {config.PIPER_DIR}. Download it once with:\n"
                f"  python -m orbit.voice.fetch_models piper {voice}")
        import json

        import onnxruntime as ort
        from piper import PiperVoice, SynthesisConfig
        from piper.config import PiperConfig

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads    # Piper's own loader would use every core; keep it to a few
        options.inter_op_num_threads = 1
        self.voice_name = voice
        self._voice = PiperVoice(
            config=PiperConfig.from_dict(json.loads(Path(f"{model}.json").read_text(encoding="utf-8"))),
            session=ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"]),
        )
        self._length_scale = length_scale
        self._syn = SynthesisConfig(length_scale=length_scale)
        self.sample_rate = self._voice.config.sample_rate
        self._init_player(device, reference)
        list(self.synthesize("Ready."))   # warm-up: the first synthesis pays one-time setup costs

    def sentences(self, text: str) -> Iterator[np.ndarray]:
        return self.synthesize(text)

    def synthesize(self, text: str, **style) -> Iterator[np.ndarray]:
        """Int16 mono audio, one array per sentence. No sound is played (used for tests, timing and wake-word training).
        `style` may override length_scale (speed), noise_scale and noise_w_scale to vary how the voice sounds."""
        syn = self._syn
        if style:
            from piper import SynthesisConfig

            syn = SynthesisConfig(**{"length_scale": self._length_scale, **style})
        for chunk in self._voice.synthesize(text, syn):
            yield np.asarray(chunk.audio_int16_array).reshape(-1)


class SarvamTTS(StreamingTTS):
    """Sarvam's Bulbul voices over the network. Sentences are requested a few at a time in parallel, so the next one is usually
    ready before the current one finishes playing. Raises (so the next engine can speak) if Sarvam cannot be used."""
    name = "sarvam"

    def __init__(self, session=None, device: str | int | None = config.SPEAKER_DEVICE, reference: ReferenceTimeline | None = None,
                 speaker: str = config.SARVAM_SPEAKER, model: str = config.SARVAM_TTS_MODEL, rate: int = config.SARVAM_TTS_RATE,
                 pace: float = config.SARVAM_TTS_PACE, language: str = config.SARVAM_LANGUAGE, prefetch: int = 3):
        from .cloud import SarvamSession

        self.session = session or SarvamSession()
        self.speaker, self.model, self.pace, self.prefetch = speaker, model, pace, prefetch
        self.language = "en-IN" if language == "unknown" else language     # speech needs a concrete language
        self.sample_rate = rate
        self._pool = ThreadPoolExecutor(max_workers=prefetch, thread_name_prefix="tts-cloud")
        self._init_player(device, reference)

    def fetch(self, sentence: str) -> np.ndarray:
        """One sentence -> int16 mono audio at self.sample_rate. No sound is played."""
        data = self.session.post("/text-to-speech", json={
            "text": sentence, "target_language_code": self.language, "speaker": self.speaker, "model": self.model,
            "speech_sample_rate": self.sample_rate, "pace": self.pace})
        try:
            raw = base64.b64decode(data["audios"][0])
            with wave.open(io.BytesIO(raw)) as w:
                frames, channels, rate, width = w.readframes(w.getnframes()), w.getnchannels(), w.getframerate(), w.getsampwidth()
        except (KeyError, IndexError, ValueError, wave.Error, EOFError) as e:
            raise RuntimeError(f"Sarvam sent audio Orbit could not read ({e})") from e
        if width != 2:
            raise RuntimeError(f"Sarvam sent {8 * width}-bit audio; only 16-bit is supported")
        audio = np.frombuffer(frames, dtype=np.int16).reshape(-1, channels)[:, 0]
        if rate != self.sample_rate:                                      # the stream was opened at self.sample_rate: match it
            from scipy.signal import resample_poly

            audio = resample_poly(audio.astype(np.float32), self.sample_rate, rate).astype(np.int16)
        return np.ascontiguousarray(audio)

    def sentences(self, text: str) -> Iterator[np.ndarray]:
        parts = split_sentences(text)
        todo, pending = iter(parts), deque()

        def refill() -> None:
            while len(pending) < self.prefetch and (part := next(todo, None)) is not None:
                pending.append(self._pool.submit(self.fetch, part))

        refill()
        while pending:
            audio = pending.popleft().result()
            refill()
            yield audio


def split_sentences(text: str) -> list[str]:
    """Sentence-sized pieces (what Piper does internally), so the first one can be requested and played quickly."""
    return [p for p in re.split(r"(?<=[.!?।])\s+", text.strip()) if p]


class WindowsTTS:
    """The built-in Windows (SAPI) voice through pyttsx3. Robotic, but always there. No streaming, no timing detail."""
    name = "windows"

    def __init__(self):
        import pyttsx3

        pyttsx3.init().stop()   # fail now, not at the first reply, if SAPI is unusable
        self._engine = None

    def stop(self) -> None:
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass

    def speak(self, text: str) -> SpeakStats:
        import pyttsx3

        start = time.perf_counter()
        self._engine = pyttsx3.init()   # one engine per call: pyttsx3's event loop does not like being reused across threads
        voices = self._engine.getProperty("voices")
        female = next((v for v in voices if "zira" in v.name.lower() or "female" in v.name.lower()), None)
        if female:
            self._engine.setProperty("voice", female.id)
        self._engine.say(text)
        self._engine.runAndWait()
        self._engine = None
        return SpeakStats("windows", time.perf_counter() - start)


class FallbackTTS:
    """Tries each engine in turn; if every one fails, the reply stays text-only. Never raises: voice must not crash the REPL."""

    def __init__(self, engines: list, log=print):
        self.engines, self.log = engines, log

    @property
    def supports_pause(self) -> bool:
        return bool(self.engines) and getattr(self.engines[0], "supports_pause", False)

    def pause(self) -> None:
        if self.supports_pause:
            self.engines[0].pause()

    def resume(self) -> None:
        if self.supports_pause:
            self.engines[0].resume()

    @property
    def reference(self):
        """The echo-cancellation reference of the engine that will speak first (None for the Windows voice: it cannot report it)."""
        return getattr(self.engines[0], "reference", None) if self.engines else None

    def stop(self) -> None:
        for engine in self.engines:
            engine.stop()

    def speak(self, text: str) -> SpeakStats:
        start = time.perf_counter()
        for engine in self.engines:
            try:
                return engine.speak(text)
            except Exception as e:
                self.log(f"  [voice] {engine.name} could not speak ({e}); trying the next option")
        self.log("  [voice] no speech engine worked; showing the reply as text only")
        return SpeakStats("text", time.perf_counter() - start)


def make_tts(voice: str = config.PIPER_VOICE, device: str | int | None = config.SPEAKER_DEVICE, log=print,
             reference: ReferenceTimeline | None = None, provider: str = config.TTS_PROVIDER) -> FallbackTTS:
    """Sarvam first (when a key is set), then Piper, then the Windows voice. Whatever cannot load is skipped with a printed reason."""
    from .cloud import sarvam_key

    if provider not in ("auto", "sarvam", "local"):
        raise ValueError(f"Unknown text-to-speech provider '{provider}'. Use auto, sarvam or local.")
    builders = [lambda: PiperTTS(voice, device, reference=reference), WindowsTTS]
    if provider == "sarvam" or (provider == "auto" and sarvam_key()):
        builders.insert(0, lambda: SarvamTTS(device=device, reference=reference))
    engines = []
    for build in builders:
        try:
            engines.append(build())
        except Exception as e:
            log(f"  [voice] speech engine unavailable: {e}")
    return FallbackTTS(engines, log)

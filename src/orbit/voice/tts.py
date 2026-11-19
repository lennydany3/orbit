"""Text-to-speech: Piper (neural, CPU, offline) with the built-in Windows voice (pyttsx3) as a fallback.

Piper yields one chunk of audio per sentence. A producer thread synthesizes the next sentence while the
current one is playing, so the first sentence starts after one sentence's worth of synthesis, not the whole reply.
`stop()` interrupts speech within about 100 ms (the hotkey barge-in in V6 will call it).
"""
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import sounddevice as sd

from . import config
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


class PiperTTS:
    name = "piper"

    def __init__(self, voice: str = config.PIPER_VOICE, device: str | int | None = config.SPEAKER_DEVICE,
                 threads: int = 2, length_scale: float = config.PIPER_LENGTH_SCALE):
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
        self.device = resolve_device(device, "output")
        self._stop = threading.Event()
        list(self.synthesize("Ready."))   # warm-up: the first synthesis pays one-time setup costs

    def synthesize(self, text: str, **style) -> Iterator[np.ndarray]:
        """Int16 mono audio, one array per sentence. No sound is played (used for tests, timing and wake-word training).
        `style` may override length_scale (speed), noise_scale and noise_w_scale to vary how the voice sounds."""
        syn = self._syn
        if style:
            from piper import SynthesisConfig

            syn = SynthesisConfig(**{"length_scale": self._length_scale, **style})
        for chunk in self._voice.synthesize(text, syn):
            yield np.asarray(chunk.audio_int16_array).reshape(-1)

    def stop(self) -> None:
        self._stop.set()

    def speak(self, text: str) -> SpeakStats:
        self._stop.clear()
        start = time.perf_counter()
        audio_q: queue.Queue = queue.Queue(maxsize=3)   # at most 3 sentences synthesized ahead of playback
        synth = {"s": 0.0, "error": None}

        def produce() -> None:
            try:
                sentences = self.synthesize(text)
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
                for i in range(0, len(audio), _BLOCK):
                    if self._stop.is_set():
                        interrupted = True
                        break
                    stream.write(audio[i:i + _BLOCK].reshape(-1, 1))
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
        if synth["error"] is not None:
            raise RuntimeError(f"Piper failed: {synth['error']}")
        return SpeakStats("piper", time.perf_counter() - start, first_audio, synth["s"], played / self.sample_rate, interrupted)


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


def make_tts(voice: str = config.PIPER_VOICE, device: str | int | None = config.SPEAKER_DEVICE, log=print) -> FallbackTTS:
    """Piper first, then the Windows voice. Whatever cannot load is skipped with a printed reason."""
    engines = []
    for build in (lambda: PiperTTS(voice, device), WindowsTTS):
        try:
            engines.append(build())
        except Exception as e:
            log(f"  [voice] speech engine unavailable: {e}")
    return FallbackTTS(engines, log)

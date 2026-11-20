"""Fallback logic always runs (fake engines). The Piper synthesis test runs only if the voice is downloaded; it plays no sound."""
import pytest

from orbit.voice import config
from orbit.voice.tts import FallbackTTS, SpeakStats


class Broken:
    name = "broken"

    def speak(self, text):
        raise RuntimeError("no sound card")

    def stop(self):
        pass


class Working:
    name = "working"

    def __init__(self):
        self.said = []

    def speak(self, text):
        self.said.append(text)
        return SpeakStats("working", 0.1)

    def stop(self):
        pass


def test_falls_back_to_the_next_engine_and_says_why():
    logs, good = [], Working()
    stats = FallbackTTS([Broken(), good], log=logs.append).speak("hello")
    assert stats.engine == "working" and good.said == ["hello"]
    assert any("broken could not speak" in line for line in logs)


def test_never_raises_when_every_engine_fails():
    logs = []
    stats = FallbackTTS([Broken(), Broken()], log=logs.append).speak("hello")
    assert stats.engine == "text"
    assert any("text only" in line for line in logs)


@pytest.mark.skipif(not (config.PIPER_DIR / f"{config.PIPER_VOICE}.onnx").exists(), reason="Piper voice not downloaded")
def test_piper_makes_one_chunk_per_sentence():
    from orbit.voice.tts import PiperTTS

    tts = PiperTTS()
    chunks = list(tts.synthesize("Opened Spotify. Volume is thirty percent."))
    assert len(chunks) == 2
    seconds = sum(len(c) for c in chunks) / tts.sample_rate
    assert 1.5 < seconds < 8
    assert all(c.dtype.name == "int16" and c.ndim == 1 and abs(c).max() > 500 for c in chunks)


# ------------------------------------------------------------------ the face's level meter
def test_every_played_block_is_handed_to_the_audio_listener(monkeypatch):
    import threading

    import numpy as np

    from orbit.voice import tts as tts_module

    written = []

    class FakeStream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def write(self, block):
            written.append(len(block))

        def stop(self):
            pass

        def abort(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tts_module.sd, "OutputStream", FakeStream)

    class Engine(tts_module.StreamingTTS):
        name = "fake"

        def __init__(self):
            self.reference, self.device = None, None
            self._stop, self._run = threading.Event(), threading.Event()
            self._run.set()

        def sentences(self, text):
            yield np.full(5000, 1000, np.int16)

    engine = Engine()
    heard = []
    engine.on_audio = lambda block: heard.append(len(block))
    stats = engine.speak("hello")
    assert heard == written and sum(heard) == 5000 and stats.audio_s > 0

    engine.on_audio = None                                   # no listener: nothing changes
    written.clear()
    engine.speak("hello")
    assert sum(written) == 5000


def test_the_listener_is_given_to_engines_that_can_play_audio_and_skipped_for_the_rest():
    from orbit.voice.tts import StreamingTTS

    class Streaming(StreamingTTS):
        name = "streaming"

        def __init__(self):
            pass

    streaming, plain = Streaming(), Working()
    FallbackTTS([plain, streaming]).set_audio_listener(print)
    assert streaming.on_audio is print and not hasattr(plain, "on_audio")

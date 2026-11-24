"""The speaking side without a sound card: sentence splitting, the echo-canceller reference, falling back to the next engine."""
import numpy as np

from orbit.voice import tts
from orbit.voice.aec import ReferenceTimeline
from orbit.voice.tts import FallbackTTS, SpeakStats, StreamingTTS, split_sentences


class ToneTTS(StreamingTTS):
    """Speaks a tone per sentence (more words = longer), like Piper would, but with no model."""
    name = "tone"
    sample_rate = 22_050

    def __init__(self, reference=None, fail=False):
        self.fail = fail
        self._init_player(None, reference)

    def sentences(self, text):
        if self.fail:
            raise RuntimeError("voice model broke")
        for sentence in split_sentences(text):
            yield (np.sin(np.arange(2205 * len(sentence.split())) / 7) * 8000).astype(np.int16)


class FakeStream:
    def __init__(self, **kw):
        self.written = 0

    def start(self):
        pass

    def stop(self):
        pass

    def abort(self):
        pass

    def close(self):
        pass

    def write(self, block):
        self.written += len(block)


def test_split_sentences():
    assert split_sentences("Opened Spotify. Volume is 30 percent! Done?") == ["Opened Spotify.", "Volume is 30 percent!", "Done?"]
    assert split_sentences("  ") == []


def test_speaking_feeds_the_echo_canceller_reference(monkeypatch):
    streams = []
    monkeypatch.setattr(tts.sd, "OutputStream", lambda **kw: streams.append(FakeStream(**kw)) or streams[-1])
    reference = ReferenceTimeline()
    stats = ToneTTS(reference=reference).speak("Hello there. How are you today?")
    assert stats.engine == "tone" and not stats.interrupted and stats.first_audio_s is not None
    assert streams[0].written > 0
    heard = reference.get_index(0, int(stats.audio_s * 16_000) - 1600)
    assert reference.playing_until is not None and np.abs(heard).max() > 1000     # the canceller now knows what was played


def test_failure_before_any_audio_lets_the_next_engine_speak(monkeypatch):
    monkeypatch.setattr(tts.sd, "OutputStream", lambda **kw: FakeStream())
    spoken = []

    class Next:
        name = "next"

        def speak(self, text):
            spoken.append(text)
            return SpeakStats("next", 0.1)

        def stop(self):
            pass

    stats = FallbackTTS([ToneTTS(fail=True), Next()], log=lambda m: None).speak("Hello there.")
    assert stats.engine == "next" and spoken == ["Hello there."]


def test_make_tts_is_local_only_and_skips_what_cannot_load(monkeypatch):
    def no_piper(*a, **k):
        raise FileNotFoundError("no piper")

    def no_sapi():
        raise RuntimeError("no sapi")

    monkeypatch.setattr(tts, "PiperTTS", no_piper)
    monkeypatch.setattr(tts, "WindowsTTS", no_sapi)
    logs = []
    assert tts.make_tts(log=logs.append).engines == []
    assert len(logs) == 2
    assert FallbackTTS([], log=lambda m: None).speak("Hi.").engine == "text"      # nothing could speak: text only, no crash


def test_unknown_emotions_are_neutral():
    engine = ToneTTS()
    engine.set_emotion("excited")
    assert engine.emotion == "excited"
    engine.set_emotion("not-a-feeling")
    assert engine.emotion == "neutral"

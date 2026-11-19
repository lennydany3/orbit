"""Sarvam speech-to-text and text-to-speech against a fake HTTP server (no network, no key, no sound card)."""
import base64
import io
import json
import wave

import httpx
import numpy as np
import pytest

from orbit.voice import cloud, tts
from orbit.voice.aec import ReferenceTimeline
from orbit.voice.cloud import CloudError, FallbackTranscriber, SarvamSession, SarvamTranscriber, wav_bytes
from orbit.voice.stt import Transcript
from orbit.voice.tts import FallbackTTS, SarvamTTS, SpeakStats, split_sentences


def session(handler, clock=lambda: 0.0) -> SarvamSession:
    client = httpx.Client(base_url="https://sarvam.test", transport=httpx.MockTransport(handler))
    return SarvamSession(api_key="k", client=client, clock=clock)


def wav_b64(samples: np.ndarray, rate: int = 22_050) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def test_wav_bytes_roundtrip():
    clip = (np.arange(1600) % 200).astype(np.int16)
    with wave.open(io.BytesIO(wav_bytes(clip))) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16_000, 1600)


def test_transcribe_sends_the_clip_and_key_and_reads_the_transcript():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["key"], seen["body"] = request.url.path, request.headers["api-subscription-key"], request.content
        return httpx.Response(200, json={"transcript": " open spotify ", "language_code": "en-IN"})

    result = SarvamTranscriber(session(handler)).transcribe(np.zeros(16_000, np.int16))
    assert result.text == "open spotify" and result.audio_s == 1.0
    assert seen["path"] == "/speech-to-text" and seen["key"] == "k"
    assert b"saarika" in seen["body"] and b"en-IN" in seen["body"] and b"RIFF" in seen["body"]


def test_offline_raises_cloud_error_then_cools_down_then_retries():
    calls, now = [], [0.0]

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("offline")

    s = session(handler, clock=lambda: now[0])
    with pytest.raises(CloudError, match="cannot reach"):
        s.post("/speech-to-text")
    with pytest.raises(CloudError, match="a moment ago"):          # skipped without touching the network
        s.post("/speech-to-text")
    assert len(calls) == 1
    now[0] = 61.0                                                   # cool-down over: it tries again
    with pytest.raises(CloudError, match="cannot reach"):
        s.post("/speech-to-text")
    assert len(calls) == 2


def test_rejected_key_is_explained():
    with pytest.raises(CloudError, match="API key"):
        session(lambda r: httpx.Response(403, json={"message": "no"})).post("/x")


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    with pytest.raises(CloudError, match="SARVAM_API_KEY"):
        SarvamSession()


class Local:
    name, model_name, load_s = "local", "small.en", 0.0

    def transcribe(self, clip):
        return Transcript("local words", 0.1, 1.0)

    def close(self):
        pass


def test_transcriber_falls_back_to_local_whisper_only_when_needed():
    logs, built = [], []

    def make_local():
        built.append(1)
        return Local()

    broken = SarvamTranscriber(session(lambda r: httpx.Response(500, json={"message": "down"})))
    fb = FallbackTranscriber(broken, make_local, log=logs.append)
    assert fb.transcribe(np.zeros(16_000, np.int16)).text == "local words"
    assert any("using local Whisper" in line for line in logs) and built == [1]

    fine = SarvamTranscriber(session(lambda r: httpx.Response(200, json={"transcript": "cloud words"})))
    built.clear()
    assert FallbackTranscriber(fine, make_local, log=logs.append).transcribe(np.zeros(16_000, np.int16)).text == "cloud words"
    assert built == []                                              # Whisper is not even loaded while the cloud works


def test_build_transcriber_uses_the_key_to_choose(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "k")
    assert cloud.build_transcriber("auto").name == "sarvam"
    with pytest.raises(ValueError):
        cloud.build_transcriber("nonsense")


# ---- text-to-speech ----
def test_split_sentences():
    assert split_sentences("Opened Spotify. Volume is 30 percent! Done?") == ["Opened Spotify.", "Volume is 30 percent!", "Done?"]
    assert split_sentences("  ") == []


def make_tts_engine(handler, **kw) -> SarvamTTS:
    return SarvamTTS(session=session(handler), device=None, **kw)


def tts_handler(request: httpx.Request) -> httpx.Response:
    text = json.loads(request.content)["text"]
    tone = (np.sin(np.arange(2205 * len(text.split())) / 7) * 8000).astype(np.int16)      # more words = longer audio
    return httpx.Response(200, json={"audios": [wav_b64(tone)]})


def test_sarvam_tts_returns_sentences_in_order_as_int16():
    engine = make_tts_engine(tts_handler)
    chunks = list(engine.sentences("One. Two words here. Three words are here now."))
    assert [len(c) for c in chunks] == [2205, 2205 * 3, 2205 * 5] and all(c.dtype == np.int16 for c in chunks)


def test_sarvam_tts_resamples_to_the_stream_rate():
    handler = lambda r: httpx.Response(200, json={"audios": [wav_b64(np.ones(8000), rate=8000)]})   # noqa: E731
    (chunk,) = list(make_tts_engine(handler).sentences("Hello."))
    assert abs(len(chunk) - 22_050) < 50                           # one second either way


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


def test_speaking_feeds_the_echo_canceller_reference(monkeypatch):
    streams = []
    monkeypatch.setattr(tts.sd, "OutputStream", lambda **kw: streams.append(FakeStream(**kw)) or streams[-1])
    reference = ReferenceTimeline()
    engine = make_tts_engine(tts_handler, reference=reference)
    stats = engine.speak("Hello there. How are you today?")
    assert stats.engine == "sarvam" and not stats.interrupted and stats.first_audio_s is not None
    assert streams[0].written > 0
    heard = reference.get_index(0, int(stats.audio_s * 16_000) - 1600)
    assert reference.playing_until is not None and np.abs(heard).max() > 1000     # the canceller now knows what was played


def test_failure_before_any_audio_lets_the_next_engine_speak(monkeypatch):
    monkeypatch.setattr(tts.sd, "OutputStream", lambda **kw: FakeStream())
    broken = make_tts_engine(lambda r: httpx.Response(500, json={"message": "down"}))
    spoken = []

    class Next:
        name = "next"

        def speak(self, text):
            spoken.append(text)
            return SpeakStats("next", 0.1)

        def stop(self):
            pass

    stats = FallbackTTS([broken, Next()], log=lambda m: None).speak("Hello there.")
    assert stats.engine == "next" and spoken == ["Hello there."]


def test_make_tts_puts_sarvam_first_only_when_a_key_exists(monkeypatch):
    def no_piper(*a, **k):
        raise FileNotFoundError("no piper")

    def no_sapi():
        raise RuntimeError("no sapi")

    monkeypatch.setattr(tts, "PiperTTS", no_piper)
    monkeypatch.setattr(tts, "WindowsTTS", no_sapi)
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    assert tts.make_tts(provider="auto", log=lambda m: None).engines == []
    monkeypatch.setenv("SARVAM_API_KEY", "k")
    assert [e.name for e in tts.make_tts(provider="auto", log=lambda m: None).engines] == ["sarvam"]


def test_emotion_shapes_the_sarvam_request_pace_and_expressiveness():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"audios": [wav_b64(np.ones(2205))]})

    engine = make_tts_engine(handler)
    engine.fetch("Okay.")                                           # no emotion: Sarvam's own defaults for expressiveness
    engine.fetch("Wow!", "excited")
    engine.fetch("I'm here.", "caring")
    assert "temperature" not in bodies[0] and bodies[0]["pace"] == 1.0
    assert bodies[1]["pace"] > 1.0 and bodies[1]["temperature"] > 0.9
    assert bodies[2]["pace"] < 1.0 and 0 < bodies[2]["temperature"] <= 1


def test_the_emotion_is_fixed_for_the_whole_reply_and_unknown_names_are_neutral():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["pace"])
        return httpx.Response(200, json={"audios": [wav_b64(np.ones(2205))]})

    engine = make_tts_engine(handler)
    engine.set_emotion("excited")
    chunks = engine.sentences("One. Two.")
    next(chunks)
    engine.set_emotion("sad")                                       # changed while the reply is already being fetched
    list(chunks)
    assert len(set(seen)) == 1 and seen[0] > 1.0
    engine.set_emotion("not-a-feeling")
    assert engine.emotion == "neutral"

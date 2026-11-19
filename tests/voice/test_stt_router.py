"""Groq / local speech-to-text routing. Network failures are simulated with httpx.MockTransport: no real Groq call, no model."""
import httpx
import numpy as np
import pytest

from orbit.voice import config
from orbit.voice.stt_router import GroqSTT, STTResult, STTRouter, STTUnavailable, clip_to_wav

SECRET = "gsk_TESTSECRETKEY1234567890"


def speech_wav(seconds: float = 1.0) -> bytes:
    t = np.arange(int(config.SAMPLE_RATE * seconds)) / config.SAMPLE_RATE
    return clip_to_wav((8000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16))


def groq_with(handler, **kwargs) -> GroqSTT:
    kwargs.setdefault("api_key", SECRET)
    return GroqSTT(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"text": " Open Spotify. "})


class FakeLocal:
    name = "local"

    def __init__(self):
        self.calls = 0

    def transcribe(self, wav_bytes, sample_rate=16000):
        self.calls += 1
        return STTResult("local words", "local", 0.2)


class CountingGroq:
    name = "groq"

    def __init__(self, error: str | None = None):
        self.calls, self.error = 0, error

    def transcribe(self, wav_bytes, sample_rate=16000):
        self.calls += 1
        if self.error:
            raise STTUnavailable(self.error)
        return STTResult("groq words", "groq", 0.3)


# ---- GroqSTT on its own ----
def test_groq_success_sends_the_right_request():
    seen = {}

    def handler(request):
        seen["auth"], seen["body"], seen["url"] = request.headers["authorization"], request.content, str(request.url)
        return httpx.Response(200, json={"text": " Open Spotify. "})

    result = groq_with(handler).transcribe(speech_wav())
    assert (result.text, result.provider) == ("Open Spotify.", "groq")
    assert seen["auth"] == f"Bearer {SECRET}" and seen["url"] == config.GROQ_URL
    for field in (b"whisper-large-v3-turbo", b'name="language"', b'filename="clip.wav"', b"RIFF"):
        assert field in seen["body"]


def test_no_key_is_reported_not_sent(monkeypatch):
    monkeypatch.delenv(config.GROQ_KEY_ENV, raising=False)
    calls = []
    groq = groq_with(lambda r: calls.append(r) or httpx.Response(200, json={"text": "x"}), api_key=None)
    with pytest.raises(STTUnavailable, match="no API key"):
        groq.transcribe(speech_wav())
    assert calls == []


@pytest.mark.parametrize("raised, expected", [
    (httpx.ConnectError("dns failed"), "no internet"),
    (httpx.ReadTimeout("slow"), "timeout"),
    (httpx.ConnectTimeout("slow"), "timeout"),
])
def test_network_problems_become_a_reason(raised, expected):
    def handler(request):
        raise raised

    with pytest.raises(STTUnavailable, match=expected):
        groq_with(handler).transcribe(speech_wav())


@pytest.mark.parametrize("status, body, expected", [
    (429, {"error": {"message": "rate limit"}}, "HTTP 429"),
    (401, {"error": {"message": "Invalid API Key"}}, "HTTP 401: Invalid API Key"),
    (503, {}, "HTTP 503"),
])
def test_http_errors_become_a_reason(status, body, expected):
    with pytest.raises(STTUnavailable, match=expected):
        groq_with(lambda r: httpx.Response(status, json=body)).transcribe(speech_wav())


def test_unreadable_answer():
    with pytest.raises(STTUnavailable, match="unreadable"):
        groq_with(lambda r: httpx.Response(200, text="<html>not json</html>")).transcribe(speech_wav())


# ---- the router ----
def test_auto_uses_groq_when_it_works():
    groq, local, logs = CountingGroq(), FakeLocal(), []
    result = STTRouter("auto", groq, local, log=logs.append, preload_local=False).transcribe(speech_wav())
    assert (result.provider, result.text, result.fallback_reason) == ("groq", "groq words", "")
    assert local.calls == 0 and logs == []


@pytest.mark.parametrize("reason", ["no API key (GROQ_API_KEY is not set)", "no internet (cannot reach Groq)",
                                    "timeout (no answer within 8s)", "rate limited (HTTP 429)"])
def test_auto_falls_back_to_local_and_logs_why(reason):
    local, logs = FakeLocal(), []
    result = STTRouter("auto", CountingGroq(reason), local, log=logs.append, preload_local=False).transcribe(speech_wav())
    assert (result.provider, result.text, result.fallback_reason) == ("local", "local words", reason)
    assert any(reason in line and "local Whisper" in line for line in logs)


def test_groq_only_mode_never_falls_back():
    local, logs = FakeLocal(), []
    result = STTRouter("groq", CountingGroq("rate limited (HTTP 429)"), local, log=logs.append, preload_local=False).transcribe(speech_wav())
    assert result.text == "" and result.error == "rate limited (HTTP 429)" and local.calls == 0


def test_local_only_mode_never_touches_groq():
    groq = CountingGroq()
    result = STTRouter("local", groq, FakeLocal(), preload_local=False).transcribe(speech_wav())
    assert result.provider == "local" and groq.calls == 0


@pytest.mark.parametrize("wav", [clip_to_wav(np.zeros(0, np.int16)),                      # nothing recorded
                                 clip_to_wav(np.full(1000, 5000, np.int16)),               # 62 ms: too short to be speech
                                 clip_to_wav(np.zeros(16000, np.int16)),                   # one second of digital silence
                                 b"not a wav file"])
def test_empty_clip_is_never_sent_and_never_transcribed(wav):
    groq, local, logs = CountingGroq(), FakeLocal(), []
    result = STTRouter("auto", groq, local, log=logs.append, preload_local=False).transcribe(wav)
    assert result.text == "" and result.error and result.provider == "none"
    assert groq.calls == 0 and local.calls == 0


def test_local_failure_is_reported_not_raised():
    class Broken(FakeLocal):
        def transcribe(self, wav_bytes, sample_rate=16000):
            raise FileNotFoundError("model missing")

    result = STTRouter("local", CountingGroq(), Broken(), log=lambda s: None, preload_local=False).transcribe(speech_wav())
    assert result.text == "" and "model missing" in result.error


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        STTRouter("openai", CountingGroq(), FakeLocal(), preload_local=False)


def test_the_api_key_never_appears_in_logs_results_or_reprs():
    logs, results = [], []
    for status in (401, 429, 500):
        groq = groq_with(lambda r, s=status: httpx.Response(s, json={"error": {"message": f"bad key {SECRET}"}}))
        router = STTRouter("auto", groq, FakeLocal(), log=logs.append, preload_local=False)
        results.append(router.transcribe(speech_wav()))
    router = STTRouter("groq", groq_with(lambda r: httpx.Response(401, json={"error": {"message": f"bad key {SECRET}"}})),
                       FakeLocal(), log=logs.append, preload_local=False)
    results.append(router.transcribe(speech_wav()))
    everything = "\n".join(logs) + repr(results) + repr(router.groq)
    assert SECRET not in everything and "gsk_TESTSECRET" not in everything

"""SpeechSegmenter logic, with a fake VAD (loud = speech) so no model or microphone is needed."""
import numpy as np
import pytest

from orbit.voice import config
from orbit.voice.vad import SpeechSegmenter

SR = config.SAMPLE_RATE


class LoudnessVAD:
    """Stand-in for SileroVAD: probability 1.0 for a loud window, 0.0 for a quiet one."""

    def reset(self):
        pass

    def __call__(self, window):
        return 1.0 if float(np.abs(window).max()) > 0.1 else 0.0


def audio(seconds: float, loud: bool) -> np.ndarray:
    n = int(SR * seconds)
    return np.full(n, 8000 if loud else 0, dtype=np.int16)


def run(*parts: tuple[float, bool], **kwargs):
    """Feed the parts in 80 ms frames; return all events."""
    seg = SpeechSegmenter(LoudnessVAD(), **kwargs)
    signal = np.concatenate([audio(s, loud) for s, loud in parts])
    events = []
    for i in range(0, len(signal) - config.FRAME_SAMPLES + 1, config.FRAME_SAMPLES):
        events += seg.feed(signal[i:i + config.FRAME_SAMPLES])
    return events


def test_silence_gives_no_events():
    assert run((5.0, False)) == []


def test_utterance_start_then_end_after_silence():
    events = run((1.0, False), (1.5, True), (2.0, False))
    assert [e.kind for e in events] == ["start", "end"]
    start, end = events
    assert start.t == pytest.approx(1.0, abs=0.1)
    assert end.speech_s == pytest.approx(1.5, abs=0.1)
    assert end.reason == "silence"
    # clip = preroll (0.3 s) + speech (1.5 s) + kept silence tail (0.3 s); the other 0.6 s of silence is trimmed
    assert len(end.clip) / SR == pytest.approx(0.3 + 1.5 + 0.3, abs=0.15)


def test_end_waits_for_the_configured_silence():
    short_gap = run((0.5, False), (1.0, True), (0.5, False), (1.0, True), (2.0, False))
    assert [e.kind for e in short_gap] == ["start", "end"]   # a 0.5 s pause does not split the utterance
    assert short_gap[1].speech_s == pytest.approx(2.0, abs=0.15)
    long_gap = run((0.5, False), (1.0, True), (1.2, False), (1.0, True), (2.0, False))
    assert [e.kind for e in long_gap] == ["start", "end", "start", "end"]   # a 1.2 s pause does


def test_tiny_blip_never_starts():
    assert run((1.0, False), (0.05, True), (2.0, False)) == []


def test_short_burst_is_ignored_not_transcribed():
    events = run((1.0, False), (0.15, True), (2.0, False))
    assert [e.kind for e in events] == ["start", "ignored"]
    assert events[1].clip is None


def test_max_utterance_cuts_long_speech():
    events = run((0.5, False), (6.0, True), (2.0, False), max_utterance_s=3.0)
    ends = [e for e in events if e.kind == "end"]
    assert ends and ends[0].reason == "max_length"
    assert len(ends[0].clip) / SR <= 3.1


def test_frames_of_any_size_are_accepted():
    seg = SpeechSegmenter(LoudnessVAD())
    signal = np.concatenate([audio(0.5, False), audio(1.0, True), audio(1.5, False)])
    events = []
    for i in range(0, len(signal), 1000):   # 1000 samples is not a multiple of the 512-sample window
        events += seg.feed(signal[i:i + 1000])
    assert [e.kind for e in events] == ["start", "end"]

"""The fast speech-to-text path (stt_fast.py): engine choice, live decoding while you hold the key, vocabulary fixes.
No model, no microphone: a loudness "VAD" and a transcriber that writes down what it was given stand in for them."""
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
import pytest

from orbit.voice import config, stt, stt_fast
from orbit.voice.hotkey import KeyEvent
from orbit.voice.state import State
from orbit.voice.stt import Transcript, apply_vocab_fixes
from orbit.voice.stt_fast import LiveDecode

from tests.voice.test_loop import LOUD, QUIET, LoudnessVAD, build

SR = config.SAMPLE_RATE


class PieceTranscriber:
    """Decodes nothing: each piece becomes "piece<N>", and the audio it was given is kept."""
    name, model_name, load_s, live_chunking = "pieces", "fake", 0.0, True

    def __init__(self):
        self.clips: list[np.ndarray] = []
        self._pool = ThreadPoolExecutor(max_workers=1)

    def transcribe(self, clip):
        self.clips.append(np.asarray(clip))
        return Transcript(f"piece{len(self.clips)}", 0.01, len(clip) / SR)

    def submit(self, clip) -> Future:
        return self._pool.submit(self.transcribe, clip)


def frames(pattern):
    """[("loud", seconds), ("quiet", seconds), ...] -> 80 ms mic frames."""
    out = []
    for kind, seconds in pattern:
        out += [LOUD if kind == "loud" else QUIET] * round(seconds * 1000 / config.FRAME_MS)
    return out


def live(transcriber, **kw):
    kw.setdefault("min_chunk_s", 2.0)
    kw.setdefault("cut_silence_s", 0.3)
    return LiveDecode(transcriber, LoudnessVAD(), **kw)


# ---------------------------------------------------------------- live decoding
def test_a_long_recording_is_decoded_in_pieces_cut_at_pauses_and_nothing_is_lost():
    t = PieceTranscriber()
    session = live(t)
    recorded = frames([("loud", 3.0), ("quiet", 0.8), ("loud", 2.4), ("quiet", 0.8), ("loud", 1.0)])
    for f in recorded:
        session.feed(f)
    assert session.pieces == 2                                  # both pauses came after 2 s of audio: cut while still recording
    clip = np.concatenate(recorded)
    result = session.finish(clip).result(timeout=5)
    assert result.text == "piece1 piece2 piece3"
    assert np.array_equal(np.concatenate(t.clips), clip)        # every sample decoded exactly once, in order
    assert result.audio_s == pytest.approx(len(clip) / SR)


def test_cuts_land_in_the_silence_never_in_speech():
    t = PieceTranscriber()
    session = live(t)
    recorded = frames([("loud", 3.0), ("quiet", 0.8), ("loud", 1.0)])
    for f in recorded:
        session.feed(f)
    session.finish(np.concatenate(recorded)).result(timeout=5)
    first, rest = t.clips[0], t.clips[1]
    assert np.abs(first[-512:]).max() == 0 and np.abs(rest[:512]).max() == 0     # both sides of the cut are quiet
    assert np.abs(first[: 3 * SR]).min() > 0                                       # all of the first speech is in piece 1


def test_a_short_request_is_left_for_one_whole_decode():
    t = PieceTranscriber()
    session = live(t)
    recorded = frames([("loud", 1.5), ("quiet", 0.5), ("loud", 0.5)])      # the pause comes before 2 s: no cut
    for f in recorded:
        session.feed(f)
    assert session.pieces == 0
    assert session.finish(np.concatenate(recorded)) is None and t.clips == []


def test_a_clip_that_is_not_what_was_fed_is_never_patched_together():
    t = PieceTranscriber()
    session = live(t)
    recorded = frames([("loud", 3.0), ("quiet", 0.8), ("loud", 1.0)])
    for f in recorded:
        session.feed(f)
    assert session.finish(np.concatenate(recorded[:-1])) is None           # the caller decodes its own clip instead


def test_silence_before_any_speech_does_not_cut():
    t = PieceTranscriber()
    session = live(t)
    for f in frames([("quiet", 3.0), ("quiet", 1.0)]):
        session.feed(f)
    assert session.pieces == 0


def test_a_failing_piece_fails_the_whole_text():
    class Broken(PieceTranscriber):
        def transcribe(self, clip):
            raise RuntimeError("decoder crashed")

    session = live(Broken())
    recorded = frames([("loud", 3.0), ("quiet", 0.8), ("loud", 1.0)])
    for f in recorded:
        session.feed(f)
    with pytest.raises(RuntimeError, match="decoder crashed"):
        session.finish(np.concatenate(recorded)).result(timeout=5)


def test_hold_to_talk_uses_the_pieces_decoded_while_you_talked():
    t = PieceTranscriber()
    sim, loop, keys, tts, _, out = build(live_decode=lambda: live(t))
    loop.transcriber = t
    plan = iter([("talk", 40), ("quiet", 8), ("talk", 12)])           # 3.2 s of speech, a 0.64 s pause, 1 s more
    state = {"quiet": 0}

    def next_step():
        step = next(plan, None)
        if step is None:
            keys.events.append(KeyEvent("up", 5.0, True))
        elif step[0] == "talk":
            sim.talk = step[1]
        else:
            state["quiet"] = sim.reads + step[1]

    sim.when(lambda: sim.reads > 5 and loop.st.is_(State.IDLE) and loop.turns == 0,
             lambda: (keys.events.append(KeyEvent("down")), next_step()))
    sim.when(lambda: loop.mode == "ptt" and sim.talk == 0 and sim.reads > 8, next_step)               # after the first talk: pause
    sim.when(lambda: state["quiet"] and sim.reads >= state["quiet"], next_step)                       # after the pause: talk
    sim.when(lambda: state["quiet"] and sim.reads > state["quiet"] + 2 and sim.talk == 0, next_step)  # done: let go
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert loop.agent.calls == ["piece1 piece2"]                       # two pieces: one cut while talking, then the last one
    assert len(t.clips) == 2


# ---------------------------------------------------------------- choosing the engine
def test_auto_uses_whisper_when_parakeet_cannot_load(monkeypatch):
    class NoParakeet:
        def __init__(self, *a, **k):
            raise FileNotFoundError("Parakeet model not found")

    made = []
    monkeypatch.setattr(stt_fast, "ParakeetTranscriber", NoParakeet)
    monkeypatch.setattr(stt, "Transcriber", lambda *a, **k: made.append("whisper") or "whisper-engine")
    logs = []
    assert stt.build_transcriber("auto", log=logs.append) == "whisper-engine"
    assert made == ["whisper"] and "Parakeet" in logs[0]
    with pytest.raises(FileNotFoundError):
        stt.build_transcriber("parakeet")                              # asked for by name: no silent switch
    assert stt.build_transcriber("whisper") == "whisper-engine"
    assert stt.build_transcriber("local") == "whisper-engine"          # the old name still works
    with pytest.raises(ValueError):
        stt.build_transcriber("sarvam")


def test_auto_prefers_parakeet(monkeypatch):
    monkeypatch.setattr(stt_fast, "ParakeetTranscriber", lambda *a, **k: "parakeet-engine")
    monkeypatch.setattr(stt, "Transcriber", lambda *a, **k: pytest.fail("Whisper must not load"))
    assert stt.build_transcriber("auto") == "parakeet-engine"


def test_parakeet_needs_its_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PARAKEET_DIR", tmp_path)
    assert not stt_fast.parakeet_ready("m")
    with pytest.raises(FileNotFoundError, match="fetch_models parakeet m"):
        stt_fast.ParakeetTranscriber("m")
    (tmp_path / "m").mkdir()
    for name in stt_fast.PARAKEET_FILES:
        (tmp_path / "m" / name).write_bytes(b"x")
    assert stt_fast.parakeet_ready("m")


# ---------------------------------------------------------------- vocabulary
@pytest.mark.parametrize("heard, expected", [
    ("open get hub", "open GitHub"),
    ("play lo-fi on spot ify", "play lofi on Spotify"),
    ("search you tube for cats", "search YouTube for cats"),
    ("play it on my echo pop", "play it on my Echo Pop"),
    ("orbits open notepad", "Orbit open notepad"),
    ("hey orbit, volume 30", "Hey Orbit, volume 30"),
    ("the orbit of the moon", "the orbit of the moon"),               # only the first word is the name
])
def test_names_are_spelled_the_way_orbit_expects(heard, expected):
    assert apply_vocab_fixes(heard) == expected


def test_both_engines_name_themselves_for_the_start_up_line():
    """run_voice prints the engine's name, model and load time: both real engines must have them (Whisper once did not)."""
    for engine in (stt.Transcriber, stt_fast.ParakeetTranscriber):
        assert isinstance(engine.name, str) and engine.name
    assert stt.Transcriber.name == "whisper" and stt_fast.ParakeetTranscriber.name == "parakeet"

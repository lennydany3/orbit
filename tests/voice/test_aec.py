"""Echo cancellation on a simulated room: a known echo path (delay + decaying reverb) is learned and removed, while a
"user" speaking over it is kept. No microphone, no models."""
import numpy as np
import pytest

from orbit.voice.aec import EchoCanceller, ReferenceTimeline

SR = 16_000


def speechlike(seconds: float, seed: int, level: float = 6000) -> np.ndarray:
    """Noise shaped and gated like speech: low-pass, bursts of syllables and pauses. Good enough to excite the filter."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    x = np.convolve(rng.standard_normal(n), np.hanning(9) / np.hanning(9).sum(), mode="same")
    envelope = np.interp(np.arange(n), np.linspace(0, n, int(seconds * 4)), np.abs(rng.standard_normal(int(seconds * 4))) ** 1.5)
    x = x * envelope
    return (x / np.abs(x).max() * level).astype(np.float32)


def room(delay_ms: float = 120, rt_ms: float = 90, seed: int = 1, gain: float = 0.6) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(rt_ms / 1000 * SR)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, 7, n))
    ir[0] = 1.0
    ir *= gain / np.linalg.norm(ir)
    return np.concatenate([np.zeros(int(delay_ms / 1000 * SR)), ir]).astype(np.float32)


def run(ref, mic, aec=None):
    aec = aec or EchoCanceller()
    n = len(mic) // 1280 * 1280
    return np.concatenate([aec.process(mic[i:i + 1280], ref[i:i + 1280]) for i in range(0, n, 1280)]).astype(np.float32), aec


def erle_db(mic, out, start_s, stop_s):
    a, b = int(start_s * SR), int(stop_s * SR)
    return 10 * np.log10((mic[a:b] ** 2).sum() / ((out[a:b] ** 2).sum() + 1e-9))


@pytest.mark.parametrize("delay_ms, rt_ms", [(40, 60), (120, 90), (250, 120)])
def test_the_echo_of_a_known_sound_is_removed(delay_ms, rt_ms):
    ref = speechlike(8, seed=3)
    mic = np.convolve(ref, room(delay_ms, rt_ms), mode="full")[: len(ref)]
    out, aec = run(ref, mic)
    assert erle_db(mic, out, 4, 8) > 18, f"only {erle_db(mic, out, 4, 8):.1f} dB of echo removed after learning"
    assert aec.erle_db > 12


def test_a_person_talking_over_the_echo_is_kept():
    ref = speechlike(10, seed=5)
    echo = np.convolve(ref, room(100, 80), mode="full")[: len(ref)]
    user = np.zeros_like(echo)
    a, b = int(6.0 * SR), int(7.2 * SR)                                   # the user speaks for 1.2 s, after the filter has learned
    user[a:b] = speechlike(1.2, seed=9, level=5000)
    mic = echo + user
    out, _ = run(ref, mic)
    kept = np.corrcoef(out[a:b], user[a:b])[0, 1]
    assert kept > 0.85, f"the user's voice came out with correlation {kept:.2f}"
    assert 10 * np.log10((user[a:b] ** 2).sum() / (((out[a:b] - user[a:b]) ** 2).sum() + 1e-9)) > 8   # user-to-residual ratio
    assert erle_db(echo, out, 8, 10) > 14                                 # and the echo is still removed after the user stopped


def test_without_a_reference_the_microphone_passes_through_untouched():
    mic = speechlike(2, seed=2)
    out, aec = run(np.zeros_like(mic), mic)
    assert np.allclose(out, np.round(mic[: len(out)]), atol=1) and aec.active_blocks == 0


def test_the_learned_room_is_remembered_for_the_next_sentence():
    aec = EchoCanceller()
    path = room(110, 80)
    ref1 = speechlike(6, seed=11)
    out1, aec = run(ref1, np.convolve(ref1, path, mode="full")[: len(ref1)], aec)
    aec.reset_history()
    ref2 = speechlike(3, seed=12)
    mic2 = np.convolve(ref2, path, mode="full")[: len(ref2)]
    out2, _ = run(ref2, mic2, aec)
    assert erle_db(mic2, out2, 0.5, 3) > 15                               # already converged: no second learning period


def test_it_is_fast_enough_for_real_time():
    import time

    ref = speechlike(4, seed=4)
    mic = np.convolve(ref, room(), mode="full")[: len(ref)]
    t0 = time.perf_counter()
    run(ref, mic)
    per_frame_ms = (time.perf_counter() - t0) / (len(ref) // 1280) * 1000
    assert per_frame_ms < 15, f"{per_frame_ms:.1f} ms per 80 ms frame"


# ---------------------------------------------------------------- the shared time axis
def test_reference_blocks_are_placed_back_to_back_even_when_written_in_a_burst():
    tl = ReferenceTimeline()
    block = np.full(1000, 100, np.int16)
    for _ in range(3):
        tl.add(block, t=10.0)                                             # three blocks handed over at once
    got = tl.get(10.0, 3000)
    assert (got == 100).all()                                             # contiguous, not overwritten on top of each other


def test_a_gap_between_sentences_is_silence_on_the_time_axis():
    tl = ReferenceTimeline()
    tl.add(np.full(1600, 50, np.int16), t=5.0)                            # 0.1 s of sound
    tl.add(np.full(1600, 70, np.int16), t=5.5)                            # the next sentence is handed over 0.4 s after the first ended
    assert (tl.get(5.0, 1600) == 50).all()
    assert (tl.get(5.1, 6400) == 0).all()
    assert (tl.get(5.5, 1600) == 70).all() and tl.get(4.0, 100).sum() == 0 and tl.get(99.0, 100).sum() == 0
    assert tl.playing_until == pytest.approx(5.6)


def test_an_empty_timeline_is_silence():
    assert ReferenceTimeline().get(1.0, 500).sum() == 0


# ---------------------------------------------------------------- alignment and safety (lessons from real hardware)
from orbit.voice.aec import ReferenceAligner  # noqa: E402


def _play(tl, ref, t0):
    """Hand the reference to the timeline in 2048-sample blocks, the way the voice does while it plays."""
    for i in range(0, len(ref), 2048):
        tl.add(ref[i:i + 2048].astype(np.int16), t=t0)                    # written in a burst: placed back to back


def test_cancellation_survives_the_real_microphone_timestamp_jitter():
    rng = np.random.default_rng(3)
    ref = speechlike(9, seed=31)
    echo = np.convolve(ref, room(150, 100, seed=8), mode="full")[: len(ref)]
    tl = ReferenceTimeline()
    t0 = 1000.0
    _play(tl, ref, t0)
    aligner, aec, outs = ReferenceAligner(tl), EchoCanceller(), []
    n = len(echo) // 1280
    for k in range(n):
        nominal = t0 + k * 0.08 + 0.0                                         # frame start on the shared clock
        jittery = nominal + rng.uniform(-0.015, 0.015)                        # PortAudio's stamps here wander by +-15 ms
        outs.append(aec.process(echo[k * 1280:(k + 1) * 1280], aligner.next(1280, jittery)))
    out = np.concatenate(outs).astype(np.float32)
    assert erle_db(echo, out, 5, 9) > 14, f"{erle_db(echo, out, 5, 9):.1f} dB with jittery timestamps"


def test_a_new_utterance_realigns_without_restarting_the_microphone_counter():
    tl = ReferenceTimeline()
    aligner = ReferenceAligner(tl)
    _play(tl, np.full(1280 * 3, 100, np.int16), t0 := 50.0)
    got = np.concatenate([aligner.next(1280, t0 + k * 0.08) for k in range(3)])
    assert (got == 100).all()
    tl.reset()                                                                  # the next sentence
    assert aligner.next(1280, 60.0).sum() == 0                                  # nothing playing yet
    _play(tl, np.full(1280 * 2, 70, np.int16), 61.0)
    got2 = np.concatenate([aligner.next(1280, 61.0 + k * 0.08) for k in range(2)])
    assert (got2 == 70).all()


@pytest.mark.parametrize("coupling", [0.0, 0.002, 0.01])
def test_it_never_returns_something_worse_than_the_microphone(coupling):
    """A microphone that hardly hears the speaker, or hears it non-linearly, must come out unchanged (or better), never noisier."""
    rng = np.random.default_rng(4)
    ref = speechlike(10, seed=6, level=30000)
    mic = coupling * np.convolve(ref, room(90, 60), mode="full")[: len(ref)] + rng.normal(0, 3.0, len(ref)).astype(np.float32)
    out, aec = run(ref, mic)
    rms = lambda x: float(np.sqrt((x ** 2).mean()))
    assert rms(out) <= rms(mic) * 1.05 + 0.5, f"{rms(mic):.1f} in, {rms(out):.1f} out"


def test_a_non_linear_microphone_is_passed_through_and_the_filter_resets_itself():
    ref = speechlike(12, seed=7, level=25000)
    mic = np.tanh(np.convolve(ref, room(100, 80), mode="full")[: len(ref)] / 2500) * 3000       # a clipping, compressing microphone
    out, aec = run(ref, mic)
    rms = lambda x: float(np.sqrt((x ** 2).mean()))
    assert rms(out) <= rms(mic) * 1.05

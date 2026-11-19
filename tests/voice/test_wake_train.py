"""Training helpers for the custom wake word: labels, the saved classifier, the trigger rule. No models, no audio devices."""
import random

import numpy as np

from orbit.voice import config
from orbit.voice.wake_train import FEATURES, FRAME, Scorer, build_stream, speech_end, trigger_frames


def tone(seconds: float, amplitude: int = 8000) -> np.ndarray:
    return np.full(int(config.SAMPLE_RATE * seconds), amplitude, np.int16)


def test_speech_end_ignores_trailing_silence():
    clip = np.concatenate([tone(1.0), np.zeros(8000, np.int16)])
    assert abs(speech_end(clip) - 16000) < 400


def test_labels_mark_the_frames_right_after_the_wake_word_ends():
    clip = np.concatenate([tone(1.0), np.zeros(8000, np.int16)])
    stream = build_stream([(clip, speech_end(clip)), (tone(1.0), None)], random.Random(0))
    assert len(stream.audio) % FRAME == 0 and len(stream.labels) == len(stream.audio) // FRAME
    assert len(stream.ends) == 1
    end = stream.ends[0]
    assert list(stream.labels[end:end + 3]) == [1, 1, 1]
    assert list(stream.labels[end - 2:end]) == [-1, -1] and list(stream.labels[end + 3:end + 5]) == [-1, -1]
    assert (stream.labels[:16] == -1).all()                 # the extractor's start-up frames are never trained on
    assert stream.labels[end + 6:].max() == 0               # the second clip (no wake word) is all negatives
    assert (stream.labels == 1).sum() == 3


def test_scorer_round_trips_through_a_file(tmp_path):
    rng = np.random.default_rng(0)
    layers = [(rng.standard_normal((FEATURES, 4)).astype(np.float32), np.zeros(4, np.float32)),
              (rng.standard_normal((4, 1)).astype(np.float32), np.zeros(1, np.float32))]
    scorer = Scorer(np.zeros(FEATURES, np.float32), np.ones(FEATURES, np.float32), layers, threshold=0.7)
    path = tmp_path / "word.npz"
    scorer.save(path)
    loaded = Scorer.load(path)
    x = rng.standard_normal((3, FEATURES)).astype(np.float32)
    assert np.allclose(scorer(x), loaded(x)) and loaded.threshold == 0.7
    assert ((loaded(x) >= 0) & (loaded(x) <= 1)).all()


def test_trigger_rule_matches_the_live_one():
    scores = np.array([0.0, 0.9, 0.0, 0.9, 0.9, 0.9, 0.9] + [0.0] * 20 + [0.9, 0.9])
    assert trigger_frames(scores, 0.5, hits=2, refractory=19) == [4, 28]     # a lone spike never counts; frames 5-6 fall inside the pause

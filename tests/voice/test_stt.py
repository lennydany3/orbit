"""Text helpers always run. The model test runs only when the Whisper model and your recorded fixtures exist."""
import wave
from pathlib import Path

import numpy as np
import pytest

from orbit.voice import config
from orbit.voice.stt import normalize, word_error_rate

FIXTURES = Path(__file__).parent / "fixtures"


def test_normalize_spells_out_numbers_and_drops_punctuation():
    assert normalize("Volume 30.") == normalize("volume thirty") == ["volume", "thirty"]
    assert normalize("Set it to 45%!") == ["set", "it", "to", "forty", "five", "percent"]


def test_word_error_rate():
    assert word_error_rate("open spotify", "Open Spotify.") == 0.0
    assert word_error_rate("open spotify", "open") == 0.5
    assert word_error_rate("open spotify", "please open spotify") == 0.5
    assert word_error_rate("open spotify", "close youtube") == 1.0
    assert word_error_rate("", "") == 0.0


def _fixtures():
    sentences = FIXTURES / "sentences.txt"
    if not sentences.exists():
        return []
    pairs = [line.split("|", 1) for line in sentences.read_text(encoding="utf-8").splitlines() if "|" in line]
    return [(name, text) for name, text in pairs if (FIXTURES / f"{name}.wav").exists()]


@pytest.mark.skipif(not (config.WHISPER_DIR / config.WHISPER_MODEL / "model.bin").exists() or not _fixtures(),
                    reason="needs the Whisper model and clips recorded with: python -m orbit.voice.run record")
def test_recorded_clips_are_transcribed_accurately():
    from orbit.voice.stt import Transcriber

    transcriber = Transcriber()
    errors = []
    for name, expected in _fixtures():
        with wave.open(str(FIXTURES / f"{name}.wav")) as w:
            clip = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        heard = transcriber.transcribe(clip).text
        errors.append(word_error_rate(expected, heard))
    assert sum(errors) / len(errors) <= 0.15, f"average word error rate {sum(errors) / len(errors):.0%}"

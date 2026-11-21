"""What a ringing alarm understands from what you say, the two sound files, and the player that loops them (no sound card is touched)."""
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from orbit.voice import alarm
from orbit.voice.alarm import SOUNDS_DIR, AlarmPlayer, Verdict, judge, load_sound, sound_kind


# ------------------------------------------------------------------ what you said
@pytest.mark.parametrize("said", [
    "ok", "OK.", "Okay", "okay okay", "o.k.", "fine", "alright", "all right", "thanks", "Thank you.", "thank you orbit", "thx", "ty", "cheers",
    "Thanks for reminding me!", "ok thanks for the reminder", "I will do it later", "thank you for reminding", "got it", "I got it", "gotcha", "noted", "understood",
    "I know", "yeah", "yes", "yep", "sure", "cool", "good", "great", "done", "I heard you", "yeah yeah thanks orbit", "ok ok ok i heard you thanks",
    "stop", "Stop it please", "enough", "that's enough", "dismiss", "silence", "be quiet", "shut up", "turn it off", "turn off", "cancel",
])
def test_these_mean_i_have_heard_it(said):
    assert judge(said).kind == "dismiss", said


@pytest.mark.parametrize("said, thanks", [("thank you", True), ("thx", True), ("thanks for reminding me", True), ("cheers", True), ("ok", False), ("fine", False), ("stop", False)])
def test_only_thanks_gets_a_youre_welcome(said, thanks):
    assert judge(said) == Verdict("dismiss", 0, thanks)


@pytest.mark.parametrize("said, minutes", [
    ("snooze", 5), ("snooze for ten minutes", 10), ("Snooze 15 minutes", 15), ("remind me again in 10 minutes", 10), ("remind me again", 5),
    ("give me five more minutes", 5), ("five more minutes", 5), ("ring again in an hour", 60), ("not now", 5), ("ask me again later", 5),
    ("remind me in 20 minutes", 20), ("snooze for 30 seconds", 1), ("snooze for 9 hours", 120), ("ok snooze", 5), ("thanks, remind me in 2 minutes", 2),
])
def test_these_mean_ring_again_later(said, minutes):
    assert judge(said) == Verdict("snooze", minutes), said


@pytest.mark.parametrize("said", [
    "", "   ", "...", "no", "what is the weather today", "open spotify", "hey orbit", "wait", "hold on",
    "so I was telling my friend about the thing and he said okay and then we went to the shop",     # long, and not said to the alarm
    "mmm", "uh", "what time is it",
])
def test_everything_else_leaves_it_ringing(said):
    assert judge(said) == Verdict("ignore"), said


def test_a_long_sentence_is_ignored_unless_it_says_stop():
    long = "so I was telling my friend about the thing and he said okay and then we went to the shop"
    assert judge(long).kind == "ignore"
    assert judge(long + " please stop").kind == "dismiss"


def test_a_word_is_not_found_inside_another_word():
    for said in ("a kayak", "the market", "I am looking", "stopwatch", "a fine dining place with seventeen courses and a lot of other things in it"):
        assert judge(said).kind == "ignore", said


# ------------------------------------------------------------------ the two sounds
def wav_info(name):
    with wave.open(str(SOUNDS_DIR / name), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return data, w.getframerate(), w.getnchannels(), w.getsampwidth()


def db(x):
    return 20 * np.log10(max(1e-9, float(x)))


@pytest.mark.parametrize("name", ["reminder.wav", "timer.wav"])
def test_a_sound_is_a_short_mono_16_bit_clip_that_starts_and_ends_in_silence(name):
    data, rate, channels, width = wav_info(name)
    assert (channels, width, rate) == (1, 2, 22_050)
    assert 1.5 <= len(data) / rate <= 8.0, "long enough to be a sound, short enough to loop often"
    assert np.abs(data[:20]).max() < 800 and np.abs(data[-20:]).max() < 800, "faded in and out: no click where it loops"
    assert np.abs(data).max() < 32767 * 0.95, "never clipping"


def test_the_two_sounds_are_different_and_equally_loud():
    reminder, _, _, _ = wav_info("reminder.wav")
    timer, _, _, _ = wav_info("timer.wav")
    assert len(reminder) != len(timer) and not np.array_equal(reminder[:2000], timer[:2000])
    rms = [db(np.sqrt(np.mean((x.astype(np.float64) / 32768) ** 2))) for x in (reminder, timer)]
    assert abs(rms[0] - rms[1]) < 2.0, f"loudness differs by {abs(rms[0] - rms[1]):.1f} dB"
    assert -26 < rms[0] < -12 and -26 < rms[1] < -12, "audible, but not at full scale"


def test_each_kind_has_its_sound_and_an_alarm_clock_rings_like_a_timer():
    assert alarm.SOUND_FOR == {"reminder": "reminder.wav", "timer": "timer.wav", "alarm": "timer.wav"}
    assert sound_kind(["reminder"]) == "reminder" and sound_kind(["reminder", "timer"]) == "timer" and sound_kind(["alarm"]) == "timer"
    for kind in ("reminder", "timer", "alarm"):
        data, rate = load_sound(kind)
        assert data.dtype == np.int16 and data.ndim == 1 and rate == 22_050
    assert load_sound("whatever")[0] is load_sound("reminder")[0], "an unknown kind rings like a reminder"


def test_a_missing_sound_is_an_error_the_caller_can_catch(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_sound("reminder", tmp_path)


def test_the_sounds_ship_with_the_package():
    text = (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert "alarms/*.wav" in text
    assert (SOUNDS_DIR / "README.md").exists()


# ------------------------------------------------------------------ the player
class FakeStream:
    """Stands in for sounddevice's OutputStream: a thread asks the callback for 1024 samples about every 5 ms while it is started."""
    made: list = []

    def __init__(self, samplerate, channels, dtype, device, callback):
        self.rate, self.callback, self.blocks, self.started, self.closed = samplerate, callback, [], False, False
        self._thread = None
        FakeStream.made.append(self)

    def start(self):
        self.started = True

        def pump():
            while self.started:
                out = np.zeros((1024, 1), np.int16)
                self.callback(out, 1024, None, None)
                self.blocks.append(out[:, 0].copy())
                time.sleep(0.003)

        self._thread = threading.Thread(target=pump, daemon=True)
        self._thread.start()

    def stop(self):
        self.started = False
        self._thread.join(1.0)

    def close(self):
        self.closed = True


@pytest.fixture
def player(monkeypatch):
    import sounddevice
    from orbit.voice import audio_io

    FakeStream.made = []
    monkeypatch.setattr(sounddevice, "OutputStream", FakeStream)
    monkeypatch.setattr(audio_io, "resolve_device", lambda spec, kind: None if spec in (None, "") else 7)
    return AlarmPlayer(None, 1.0)


def test_the_sound_loops_without_a_gap_and_stops_with_a_fade(player):
    clip, rate = load_sound("reminder")
    player.start("reminder")
    assert player.playing and player.kind == "reminder"
    time.sleep(0.15)
    player.stop()
    stream = FakeStream.made[0]
    assert not player.playing and stream.closed and stream.rate == rate
    played = np.concatenate(stream.blocks)
    n = min(len(clip), len(played) - 4096)                      # (the very end is the fade-out, checked below)
    assert n > 10_000 and np.array_equal(played[:n], clip[:n]), "it plays the file from its first sample"
    fade = int(alarm.FADE_OUT_S * rate)
    assert np.abs(played[-900:]).max() == 0, "and it ends in silence: the fade-out ran to zero before the sound card stopped"
    ramp = np.abs(played[-1024 - fade: -1024 + 78]).astype(np.float64)           # the fade-out itself: one block, falling to nothing
    assert ramp[:200].max() > ramp[-200:].max(), "it faded, it was not cut"


def test_the_sound_wraps_round_to_the_start(player):
    clip, _ = load_sound("reminder")
    player._clip, player._pos = clip, len(clip) - 100
    out = np.zeros((300, 1), np.int16)
    player._fill(out, 300, None, None)
    assert np.array_equal(out[:100, 0], clip[-100:]) and np.array_equal(out[100:, 0], clip[:200])
    assert player._pos == 200


def test_the_volume_scales_it_and_is_kept_between_0_and_1(player):
    clip, _ = load_sound("reminder")
    player._clip, player._pos = clip, 5000
    player.set_volume(0.5)
    out = np.zeros((256, 1), np.int16)
    player._fill(out, 256, None, None)
    assert np.allclose(out[:, 0], (clip[5000:5256] * 0.5).astype(np.int16), atol=1)
    player.set_volume(7)
    assert player.gain == 1.0
    player.set_volume(-1)
    assert player.gain == 0.0
    out[:] = 1
    player._fill(out, 256, None, None)
    assert not out.any(), "volume 0 is silent"


def test_the_face_is_given_every_block_that_plays(player):
    heard = []
    player.on_audio = heard.append
    player.start("timer")
    time.sleep(0.05)
    player.stop()
    assert heard and all(b.dtype == np.int16 and len(b) == 1024 for b in heard)


def test_a_face_that_breaks_never_breaks_the_sound(player):
    def broken(block):
        raise RuntimeError("face is gone")

    player.on_audio = broken
    player.start("reminder")
    time.sleep(0.05)
    player.stop()
    assert len(np.concatenate(FakeStream.made[0].blocks)) > 1024


def test_starting_again_restarts_from_the_beginning_and_stop_is_safe_twice(player):
    player.start("reminder")
    time.sleep(0.03)
    player.start("timer")
    assert player.kind == "timer" and len(FakeStream.made) == 2 and FakeStream.made[0].closed
    player.stop()
    player.stop()
    assert not player.playing


def test_it_rings_for_a_whole_number_of_plays_so_a_sound_is_never_cut_in_half(player):
    for kind in ("reminder", "timer"):
        clip, rate = load_sound(kind)
        once = len(clip) / rate
        assert player.ring_seconds(kind, 4.0) == pytest.approx(np.ceil(4.0 / once) * once)
        assert player.ring_seconds(kind, 0.0) == pytest.approx(once), "at least one play"
        assert player.ring_seconds(kind, once * 3) == pytest.approx(once * 3)


def test_the_speaker_can_be_changed(player):
    assert player.set_device("Echo") is True and player.device == 7
    assert player.set_device("Echo") is False
    assert player.set_device(None) is True and player.device is None

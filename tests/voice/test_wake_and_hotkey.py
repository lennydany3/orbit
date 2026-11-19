"""Wake-word trigger rules and hold-to-talk rules, tested without a microphone, a keyboard or any model."""
import time
from collections import deque

import numpy as np
from pynput import keyboard

from orbit.voice import config
from orbit.voice.hotkey import Hotkeys
from orbit.voice.wakeword import WakeWord, sounds_like_orbit, strip_wake_phrase

FRAME = np.zeros(config.FRAME_SAMPLES, dtype=np.int16)


class _NoModel:
    def reset(self):
        pass


def wake_with_scores(scores, **kwargs) -> tuple[WakeWord, list]:
    """A WakeWord whose model returns the given scores one frame at a time."""
    ww = WakeWord.__new__(WakeWord)
    ww.name, ww.threshold, ww.hits, ww.refractory_s = "hey_jarvis", 0.5, kwargs.get("hits", 2), kwargs.get("refractory_s", 1.5)
    ww._model, ww.verifier, ww._ring = _NoModel(), None, deque(maxlen=30)
    ww.reset()
    it = iter(scores)
    ww.predict = lambda frame: next(it)
    return ww, [ww.process(FRAME).triggered for _ in scores]


def test_needs_consecutive_frames_above_threshold():
    _, triggers = wake_with_scores([0.0, 0.9, 0.1, 0.9, 0.2, 0.9, 0.9])
    assert triggers == [False, False, False, False, False, False, True]   # lone spikes are ignored, two in a row count


def test_one_wake_is_not_reported_twice():
    _, triggers = wake_with_scores([0.9] * 10)          # 10 frames = 0.8 s, inside the 1.5 s refractory period
    assert triggers.count(True) == 1


def test_wakes_again_after_the_refractory_period():
    _, triggers = wake_with_scores([0.9] * 3 + [0.0] * 20 + [0.9] * 3)   # 23 frames = 1.84 s later
    assert triggers.count(True) == 2


def test_strip_wake_phrase():
    assert strip_wake_phrase("Hey Jarvis, open Spotify.", "hey_jarvis") == "open Spotify."
    assert strip_wake_phrase("Hey Mycroft, open Spotify.", "hey_mycroft") == "open Spotify."
    assert strip_wake_phrase("Hey, Jarvis open spotify", "hey_jarvis") == "open spotify"
    assert strip_wake_phrase("Alexa volume thirty", "alexa") == "volume thirty"
    assert strip_wake_phrase("open spotify", "hey_jarvis") == "open spotify"
    assert strip_wake_phrase("Hey Jarvis", "hey_jarvis") == "Hey Jarvis"   # nothing left: keep the text rather than send an empty request


def _keys(**kwargs) -> Hotkeys:
    return Hotkeys("ctrl_r", min_hold_s=kwargs.get("min_hold_s", 0.05))


def test_a_real_hold_is_valid_and_key_repeat_is_ignored():
    k = _keys()
    for _ in range(5):                                  # Windows repeats key-down while a key is held
        k._on_press(keyboard.Key.ctrl_r)
    time.sleep(0.1)
    k._on_release(keyboard.Key.ctrl_r)
    events = k.poll()
    assert [e.kind for e in events] == ["down", "up"] and events[1].valid and events[1].held_s >= 0.1


def test_a_quick_tap_is_not_talking():
    k = _keys(min_hold_s=0.3)
    k._on_press(keyboard.Key.ctrl_r)
    k._on_release(keyboard.Key.ctrl_r)
    assert k.poll()[-1].valid is False


def test_a_shortcut_like_ctrl_c_is_not_talking():
    k = _keys()
    k._on_press(keyboard.Key.ctrl_r)
    k._on_press(keyboard.KeyCode.from_char("c"))
    time.sleep(0.1)
    k._on_release(keyboard.Key.ctrl_r)
    assert k.poll()[-1].valid is False


def test_kill_switch_sets_the_flag_and_queues_an_event():
    k = _keys()
    k._on_kill()
    assert k.killed.is_set() and [e.kind for e in k.poll()] == ["kill"]


def test_sounds_like_orbit():
    for heard in ["Hey Orbit.", "hey, orbit", "Hey orbit open spotify", "hey or bit", "Hey Orbits"]:
        assert sounds_like_orbit(heard), heard
    for heard in ["Hey, are you there?", "a little bit", "Hey Robert", "the robot is here", "", "Hey Mycroft"]:
        assert not sounds_like_orbit(heard), heard


def test_a_missed_key_release_is_recovered_when_windows_says_the_key_is_up():
    k = _keys()
    k._on_press(keyboard.Key.ctrl_r)
    time.sleep(0.1)
    k.physically_down = lambda: False          # the release event was dropped, but the OS knows the key is up
    events = k.poll()
    assert [e.kind for e in events] == ["down", "up"] and events[1].valid and events[1].held_s >= 0.1
    assert k.poll() == []                      # and it is only reported once


def test_a_key_that_is_really_still_down_is_left_alone():
    k = _keys()
    k._on_press(keyboard.Key.ctrl_r)
    k.physically_down = lambda: True
    assert [e.kind for e in k.poll()] == ["down"]

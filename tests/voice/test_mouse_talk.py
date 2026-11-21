"""A mouse button as the hold-to-talk key: it only counts once it has been held a moment, so an ordinary click never wakes Orbit."""
import time

import pytest
from pynput import keyboard, mouse

from orbit.voice import hotkey
from orbit.voice.hotkey import Hotkeys, parse_key


@pytest.fixture(autouse=True)
def no_real_buttons(monkeypatch):
    monkeypatch.setattr(Hotkeys, "physically_down", lambda self: None)       # no real button is held in a test


def keys(name="mouse5", hold=0.05):
    return Hotkeys(name, min_hold_s=hold)


def test_the_five_buttons_are_the_ones_the_window_names():
    assert [parse_key(f"mouse{n}") for n in range(1, 6)] == [mouse.Button.left, mouse.Button.right, mouse.Button.middle, mouse.Button.x1, mouse.Button.x2]
    assert keys("mouse5")._vk == 0x06 and keys("mouse1")._vk == 0x01 and keys("mouse3")._vk == 0x04
    with pytest.raises(ValueError):
        parse_key("mouse6")


def test_a_button_held_long_enough_is_a_hold_to_talk():
    k = keys()
    k._on_click(0, 0, mouse.Button.x2, True)
    assert k.poll() == [], "not yet: it may be a plain click"
    time.sleep(0.08)
    assert [e.kind for e in k.poll()] == ["down"]
    k._on_click(0, 0, mouse.Button.x2, False)
    [up] = k.poll()
    assert up.kind == "up" and up.valid and up.held_s >= 0.05


def test_an_ordinary_click_leaves_no_trace_at_all():
    k = keys(hold=0.5)
    k._on_click(0, 0, mouse.Button.left, True)
    k._on_click(0, 0, mouse.Button.left, False)
    assert k.poll() == [] and k._down_at is None
    k = keys("mouse1", hold=0.5)
    for _ in range(3):
        k._on_click(0, 0, mouse.Button.left, True)
        k._on_click(0, 0, mouse.Button.left, False)
    assert k.poll() == []


def test_a_click_that_ends_after_the_hold_is_heard_even_if_nobody_polled_in_between():
    k = keys()
    k._on_click(0, 0, mouse.Button.x2, True)
    time.sleep(0.08)
    k._on_click(0, 0, mouse.Button.x2, False)          # let go before the loop's next poll: the hold still counts
    assert [e.kind for e in k.poll()] == ["down", "up"] and k._down_at is None


def test_the_other_buttons_and_the_keyboard_do_nothing():
    k = keys("mouse5")
    k._on_click(0, 0, mouse.Button.left, True)
    k._on_press(keyboard.Key.ctrl_r)
    time.sleep(0.08)
    assert k.poll() == [] and k._down_at is None


def test_a_key_pressed_while_the_button_is_held_makes_it_a_shortcut_not_talking():
    k = keys()
    k._on_click(0, 0, mouse.Button.x2, True)
    time.sleep(0.08)
    k.poll()
    k._on_press(keyboard.Key.shift)
    k._on_click(0, 0, mouse.Button.x2, False)
    [up] = k.poll()
    assert up.kind == "up" and up.valid is False


def test_a_keyboard_talk_key_still_starts_at_once():
    k = keys("ctrl_r")
    k._on_press(keyboard.Key.ctrl_r)
    assert [e.kind for e in k.poll()] == ["down"]
    k._on_release(keyboard.Key.ctrl_r)
    assert [e.kind for e in k.poll()] == ["up"]


class Stub:
    made, stopped = [], []

    def __init__(self, **kwargs):
        Stub.made.append(self)

    def start(self):
        pass

    def stop(self):
        Stub.stopped.append(self)


def test_the_mouse_is_only_watched_while_the_talk_key_is_a_button(monkeypatch):
    Stub.made, Stub.stopped = [], []
    monkeypatch.setattr(hotkey.mouse, "Listener", Stub)
    k = keys("f9")
    k._started = True
    k._sync_mouse_listener()
    assert Stub.made == [], "a keyboard key never installs a mouse hook"
    assert k.set_ptt_key("mouse4") is True
    assert len(Stub.made) == 1 and k._mouse_listener is Stub.made[0]
    assert k.set_ptt_key("mouse5") is True and len(Stub.made) == 1, "one hook serves every button"
    assert k.set_ptt_key("f8") is True and Stub.stopped == Stub.made and k._mouse_listener is None


def test_changing_to_another_key_while_the_button_is_held_lets_go_first(monkeypatch):
    k = keys("mouse5")
    k._on_click(0, 0, mouse.Button.x2, True)
    time.sleep(0.08)
    k.poll()
    assert k.set_ptt_key("f8") is True
    assert [e.kind for e in k.poll()] == ["up"] and k._down_at is None

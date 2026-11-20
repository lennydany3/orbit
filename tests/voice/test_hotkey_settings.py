"""The hold-to-talk key follows the Settings page: read at start, and changed while Orbit runs, with no restart."""
import json
import os
import time

from pynput import keyboard

from orbit.voice import hotkey
from orbit.voice.hotkey import Hotkeys


def save(path, talk):
    path.write_text(json.dumps({"hotkeys": {"talk": talk}}), encoding="utf-8")
    os.utime(path, ns=(time.time_ns() + save.n * 1_000_000_000,) * 2)
    save.n += 3


save.n = 3


def test_the_key_comes_from_the_settings_file(tmp_path):
    file = tmp_path / "s.json"
    save(file, "f9")
    keys = Hotkeys(settings_file=file)
    assert keys.ptt_name == "f9" and keys.ptt_key == keyboard.Key.f9


def test_no_settings_file_means_right_ctrl(tmp_path):
    assert Hotkeys(settings_file=tmp_path / "none.json").ptt_name == "ctrl_r"


def test_a_key_given_in_code_wins_and_is_never_changed_by_the_file(tmp_path, monkeypatch):
    file = tmp_path / "s.json"
    save(file, "f9")
    keys = Hotkeys("ctrl_l", settings_file=file)
    monkeypatch.setattr(hotkey, "SETTINGS_CHECK_S", 0.0)
    save(file, "f8")
    keys.poll()
    assert keys.ptt_name == "ctrl_l"


def test_a_new_key_saved_in_the_window_applies_on_the_next_look(tmp_path, monkeypatch):
    monkeypatch.setattr(hotkey, "SETTINGS_CHECK_S", 0.0)
    monkeypatch.setattr(Hotkeys, "physically_down", lambda self: None)       # no real key is held in a test
    file = tmp_path / "s.json"
    save(file, "f9")
    keys = Hotkeys(settings_file=file, min_hold_s=0.0)
    save(file, "f8")
    keys.poll()
    assert keys.ptt_name == "f8" and keys._vk == keyboard.Key.f8.value.vk
    keys._on_press(keyboard.Key.f9)
    assert keys.poll() == [], "the old key no longer starts a turn"
    keys._on_press(keyboard.Key.f8)
    assert [e.kind for e in keys.poll()] == ["down"], "the new one does"


def test_changing_the_key_while_it_is_held_lets_go_first_so_nothing_keeps_listening(tmp_path, monkeypatch):
    monkeypatch.setattr(hotkey, "SETTINGS_CHECK_S", 0.0)
    monkeypatch.setattr(Hotkeys, "physically_down", lambda self: None)
    file = tmp_path / "s.json"
    save(file, "f9")
    keys = Hotkeys(settings_file=file, min_hold_s=0.0)
    keys._on_press(keyboard.Key.f9)
    keys.poll()
    save(file, "f8")
    events = keys.poll()
    assert [e.kind for e in events] == ["up"] and keys._down_at is None


def test_a_broken_file_or_an_unknown_key_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(hotkey, "SETTINGS_CHECK_S", 0.0)
    file = tmp_path / "s.json"
    save(file, "f9")
    keys = Hotkeys(settings_file=file)
    file.write_text("{{{", encoding="utf-8")
    os.utime(file, ns=(time.time_ns() + 50_000_000_000,) * 2)
    keys.poll()
    assert keys.ptt_name == "ctrl_r", "a broken file means the default key, never no key"
    assert keys.set_ptt_key("banana") is False and keys.ptt_name == "ctrl_r"


def test_the_file_is_looked_at_only_about_once_a_second(tmp_path):
    file = tmp_path / "s.json"
    save(file, "f9")
    keys = Hotkeys(settings_file=file)
    save(file, "f8")
    keys.poll()
    assert keys.ptt_name == "f9", "polled again straight away: not checked yet"

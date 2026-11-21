"""The settings file: what Orbit accepts, and what it quietly leaves out so a bad entry never stops it."""
import json
from pathlib import Path

import pytest

from orbit import settings

FIXTURE = Path(__file__).parent / "fixtures" / "settings_example.json"


def parsed(data) -> settings.Settings:
    return settings.parse(data)


def command(**kw) -> dict:
    return {"name": "Work", "phrases": ["work mode"], "steps": [{"kind": "open", "target": "Spotify"}]} | kw


def test_no_file_means_the_defaults(tmp_path):
    s = settings.load(tmp_path / "nope.json")
    assert s.hotkeys == settings.DEFAULT_HOTKEYS and s.commands == [] and s.problems == []


def test_the_example_file_the_window_writes_is_read_as_written():
    s = settings.load(FIXTURE)
    assert s.problems == []
    assert s.hotkeys == {"orb": "ctrl+alt+o", "yes": "ctrl+alt+y", "no": "ctrl+alt+n", "talk": "f9"}
    work, hello = s.commands
    assert (work.id, work.name, work.enabled, work.phrases) == ("work-mode", "Work mode", True, ("work mode", "start working"))
    assert [(x.kind, x.value, x.seconds) for x in work.steps] == [
        ("open", "Spotify", 0.0), ("open", "https://github.com", 0.0), ("wait", "", 1.5), ("keys", "ctrl+shift+s", 0.0)]
    assert not hello.enabled and hello.steps[0].value == "Hello there!"
    assert work.sends_keys and hello.sends_keys
    assert s.sounds == settings.Sounds(enabled=False, volume=65)
    assert s.audio == settings.Audio(mic="USB Microphone", speaker="", volume=89)
    assert s.voice == settings.VoiceOptions(wake_word=True, keep_listening=False)


def test_broken_json_falls_back_to_the_defaults_and_says_so(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{not json", encoding="utf-8")
    s = settings.load(f)
    assert s.hotkeys == settings.DEFAULT_HOTKEYS and "not valid JSON" in s.problems[0]


def test_a_file_saved_with_a_byte_order_mark_still_reads(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"hotkeys": {"talk": "f8"}}), encoding="utf-8-sig")
    assert settings.load(f).talk_key == "f8"


def test_something_that_is_not_an_object_is_ignored():
    for data in ([], "x", 3, None):
        assert "not a JSON object" in parsed(data).problems[0]


@pytest.mark.parametrize("key", ["ctrl_r", "f9", "caps_lock", "alt_gr", "z", "5"])
def test_these_hold_to_talk_keys_are_accepted(key):
    assert parsed({"hotkeys": {"talk": key}}).talk_key == key


@pytest.mark.parametrize("key", ["banana", "", "ctrl", "ctrl+r", 5, None, " ", "ab", ["f9"]])
def test_a_bad_talk_key_keeps_the_default(key):
    s = parsed({"hotkeys": {"talk": key}})
    assert s.talk_key == "ctrl_r"
    if key is not None:
        assert "talk key" in s.problems[0]


def test_every_talk_key_name_exists_in_pynput():
    from pynput import keyboard, mouse
    missing = [k for k in settings.TALK_KEYS if not hasattr(keyboard.Key, k) and k not in settings.MOUSE_TALK]
    assert missing == []
    assert all(hasattr(mouse.Button, button) for button in settings.MOUSE_TALK.values())


def test_the_window_checks_the_other_chords_so_here_they_only_need_to_be_text():
    s = parsed({"hotkeys": {"orb": " Ctrl+Alt+P ", "yes": 5, "no": ""}})
    assert s.hotkeys["orb"] == "ctrl+alt+p" and s.hotkeys["yes"] == "ctrl+alt+y" and s.hotkeys["no"] == "ctrl+alt+n"


def test_a_command_is_phrases_plus_steps_and_phrases_are_made_comparable():
    c = parsed({"commands": [command(phrases=["  Work   MODE!  ", "Let's work", "work mode"])]}).commands[0]
    assert c.phrases == ("work mode", "lets work"), "lowercase, no punctuation, no repeats"
    assert c.id == "work" and c.enabled


def test_a_command_missing_a_name_steps_or_phrases_is_dropped_with_a_reason():
    s = parsed({"commands": [command(name=""), command(steps=[]), command(phrases=[]), command(phrases=["!!!"]), "x", 4]})
    assert s.commands == []
    assert len(s.problems) == 6 and any("no steps" in p for p in s.problems) and any("nothing to say" in p for p in s.problems)


@pytest.mark.parametrize("step, why", [
    ({"kind": "open"}, "nothing to open"), ({"kind": "open", "target": "  "}, "nothing to open"),
    ({"kind": "keys", "keys": "ctrl+banana"}, "unknown key"), ({"kind": "keys"}, "no keys"),
    ({"kind": "type", "text": ""}, "no text"), ({"kind": "type", "text": "x" * 501}, "longer than"),
    ({"kind": "wait", "seconds": 0}, "wait must be"), ({"kind": "wait", "seconds": 31}, "wait must be"),
    ({"kind": "wait", "seconds": True}, "wait must be"), ({"kind": "wait", "seconds": "2"}, "wait must be"),
    ({"kind": "run", "program": "calc.exe"}, "unknown step 'run'"), ("open Spotify", "not an object"),
])
def test_a_command_with_one_bad_step_is_dropped_whole_rather_than_run_half(step, why):
    s = parsed({"commands": [command(steps=[{"kind": "open", "target": "Spotify"}, step])]})
    assert s.commands == [] and why in s.problems[0]


def test_a_keys_step_is_stored_in_its_one_canonical_form():
    c = parsed({"commands": [command(steps=[{"kind": "keys", "keys": "Win+Shift+S"}])]}).commands[0]
    assert c.steps[0].value == "shift+win+s"


def test_two_commands_cannot_share_a_phrase_the_first_one_keeps_it():
    s = parsed({"commands": [command(name="A", phrases=["go"]), command(name="B", phrases=["go", "other"])]})
    assert [c.phrases for c in s.commands] == [("go",), ("other",)]
    assert "'go' already belongs to 'A'" in s.problems[0]


def test_a_switched_off_command_does_not_hold_on_to_its_phrase():
    s = parsed({"commands": [command(name="A", phrases=["go"], enabled=False), command(name="B", phrases=["go"])]})
    assert s.commands[1].phrases == ("go",) and s.problems == []


def test_limits_keep_the_file_small():
    many = [command(name=f"C{i}", phrases=[f"p{i}"]) for i in range(settings.MAX_COMMANDS + 5)]
    s = parsed({"commands": many})
    assert len(s.commands) == settings.MAX_COMMANDS and "only the first" in s.problems[-1]
    c = parsed({"commands": [command(phrases=[f"phrase {i}" for i in range(20)])]}).commands[0]
    assert len(c.phrases) == settings.MAX_PHRASES
    assert parsed({"commands": [command(steps=[{"kind": "wait", "seconds": 1}] * (settings.MAX_STEPS + 1))]}).commands == []


def test_a_command_says_what_it_will_do_in_plain_words():
    s = settings.load(FIXTURE)
    assert s.commands[0].describe() == "open Spotify, then open https://github.com, then wait 1.5 seconds, then press ctrl+shift+s"
    assert s.commands[1].describe() == 'type "Hello there!"'


def test_the_settings_path_can_be_moved_with_an_environment_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBIT_SETTINGS", str(tmp_path / "mine.json"))
    assert settings.path() == tmp_path / "mine.json"
    monkeypatch.delenv("ORBIT_SETTINGS")
    assert settings.path().name == "settings.json"


# ---------------------------------------------------------------- rules shared with the window (the Rust side reads the same files)
FIXTURES = Path(__file__).parent / "fixtures"


def test_key_combinations_are_read_the_way_the_window_reads_them():
    from orbit.tools.os_layer import keyboard
    for case in json.loads((FIXTURES / "key_combos.json").read_text(encoding="utf-8")):
        if case.get("error"):
            with pytest.raises(keyboard.KeyError_):
                keyboard.parse_chord(case["in"])
        else:
            assert str(keyboard.parse_chord(case["in"])) == case["out"], case["in"]


def test_phrases_are_compared_the_way_the_window_compares_them():
    for said, expected in json.loads((FIXTURES / "phrases.json").read_text(encoding="utf-8")):
        assert settings.normalize_phrase(said) == expected, said


SOUNDS = json.loads((FIXTURES / "sounds.json").read_text(encoding="utf-8"))


def test_no_sounds_entry_means_on_and_quiet():
    s = parsed({})
    assert s.sounds == settings.Sounds(True, 40) and s.sounds.audible and s.problems == []
    assert not settings.Sounds(True, 0).audible and not settings.Sounds(False, 90).audible
    assert SOUNDS["default_volume"] == settings.DEFAULT_VOLUME


@pytest.mark.parametrize("case", SOUNDS["volume"], ids=lambda c: repr(c["in"]))
def test_the_sound_volume_is_read_the_way_the_window_reads_it(case):
    expected = case.get("out")
    assert settings.parse_volume(case["in"]) == expected
    s = parsed({"sounds": {"volume": case["in"]}})
    assert s.sounds.volume == (settings.DEFAULT_VOLUME if expected is None else expected)
    assert len(s.problems) == (expected is None)


@pytest.mark.parametrize("case", SOUNDS["enabled"], ids=lambda c: repr(c["in"]))
def test_the_sounds_switch_is_read_the_way_the_window_reads_it(case):
    s = parsed({"sounds": {"enabled": case["in"]}})
    assert s.sounds.enabled == case.get("out", True)
    assert len(s.problems) == ("out" not in case)


@pytest.mark.parametrize("raw", [True, 5, "loud", [], None])
def test_a_sounds_entry_that_is_not_an_object_is_ignored_with_a_reason(raw):
    s = parsed({"sounds": raw})
    assert s.sounds == settings.Sounds() and "not an object" in s.problems[0]


def test_each_half_of_the_sounds_entry_stands_alone():
    s = parsed({"sounds": {"volume": 70}})
    assert s.sounds == settings.Sounds(True, 70) and s.problems == []
    assert parsed({"sounds": {"enabled": False}}).sounds == settings.Sounds(False, 40)


def test_the_sounds_never_stop_the_rest_of_the_file_being_read():
    s = parsed({"sounds": 7, "hotkeys": {"talk": "f8"}, "commands": [command()]})
    assert s.talk_key == "f8" and len(s.commands) == 1 and len(s.problems) == 1


def test_the_talk_keys_the_window_offers_are_the_ones_orbit_accepts():
    keys = json.loads((FIXTURES / "talk_keys.json").read_text(encoding="utf-8"))
    assert all(settings.valid_talk_key(k) for k in keys["valid"]), [k for k in keys["valid"] if not settings.valid_talk_key(k)]
    assert not any(settings.valid_talk_key(k) for k in keys["invalid"]), [k for k in keys["invalid"] if settings.valid_talk_key(k)]


# ---- mouse buttons as the talk key ----

@pytest.mark.parametrize("key", ["mouse1", "mouse2", "mouse3", "mouse4", "mouse5"])
def test_a_mouse_button_can_be_the_talk_key(key):
    s = parsed({"hotkeys": {"talk": key}})
    assert s.talk_key == key and s.problems == []


@pytest.mark.parametrize("key", ["mouse0", "mouse6", "Mouse1", "mouse", "mouse10"])
def test_a_mouse_button_that_does_not_exist_is_refused(key):
    s = parsed({"hotkeys": {"talk": key}})
    assert s.talk_key == "ctrl_r" and "talk key" in s.problems[0]


def test_the_mouse_buttons_are_named_the_way_the_window_names_them():
    keys = json.loads((FIXTURES / "talk_keys.json").read_text(encoding="utf-8"))
    assert keys["mouse"] == settings.MOUSE_TALK


# ---- audio and voice, read the way the window reads them (tests/fixtures/audio_voice.json) ----

CASES = json.loads((FIXTURES / "audio_voice.json").read_text(encoding="utf-8"))


def test_the_speaking_volume_is_read_the_way_the_window_reads_it():
    for case in CASES["volume"]:
        s = parsed({"audio": {"volume": case["in"]}})
        if "out" in case:
            assert (s.audio.volume, s.problems) == (case["out"], []), case
        else:
            assert s.audio.volume == CASES["default_volume"] and "speaking volume" in s.problems[0], case
    assert settings.DEFAULT_SPEAKING_VOLUME == CASES["default_volume"]


@pytest.mark.parametrize("name", ["mic", "speaker"])
def test_a_device_name_is_read_the_way_the_window_reads_it(name):
    for case in CASES["device"]:
        s = parsed({"audio": {name: case["in"]}})
        if "out" in case:
            assert (getattr(s.audio, name), s.problems) == (case["out"], []), case
        else:
            assert getattr(s.audio, name) == "" and "not a device name" in s.problems[0], case


@pytest.mark.parametrize("name", ["wake_word", "keep_listening"])
def test_a_voice_switch_is_read_the_way_the_window_reads_it(name):
    for case in CASES["flag"]:
        s = parsed({"voice": {name: case["in"]}})
        if "out" in case:
            assert (getattr(s.voice, name), s.problems) == (case["out"], []), case
        else:
            assert getattr(s.voice, name) is True and "true or false" in s.problems[0], case


def test_no_audio_or_voice_entry_means_the_way_orbit_always_worked():
    s = parsed({})
    assert s.audio == settings.Audio("", "", 100) and s.voice == settings.VoiceOptions(True, True) and s.problems == []


def test_an_audio_or_voice_entry_that_is_not_an_object_is_ignored_with_a_reason():
    for key in ("audio", "voice"):
        for raw in (True, 5, "loud", [], None):
            s = parsed({key: raw, "hotkeys": {"talk": "f8"}})
            assert s.talk_key == "f8" and "not an object" in s.problems[0], (key, raw)


def test_each_part_of_the_audio_stands_alone():
    s = parsed({"audio": {"mic": "USB Mic", "volume": "loud"}})
    assert (s.audio.mic, s.audio.speaker, s.audio.volume, len(s.problems)) == ("USB Mic", "", 100, 1)

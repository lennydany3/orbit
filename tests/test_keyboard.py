"""Key combinations and typed text: parsing, and exactly which key events would be sent (nothing real is pressed)."""
import pytest
from pynput import keyboard as pynput_keyboard

from orbit.tools.os_layer import keyboard as kb

KEYUP, EXT, UNI = kb.KEYEVENTF_KEYUP, kb.KEYEVENTF_EXTENDEDKEY, kb.KEYEVENTF_UNICODE


def test_a_combination_is_modifiers_plus_one_key_in_a_fixed_order():
    assert str(kb.parse_chord("Win + Shift + S")) == "shift+win+s"
    assert str(kb.parse_chord("ctrl+alt+delete")) == "ctrl+alt+delete"
    assert str(kb.parse_chord("F5")) == "f5"
    assert str(kb.parse_chord("Control+Return")) == "ctrl+enter"


@pytest.mark.parametrize("bad, why", [
    ("", "write keys"), ("ctrl+", "write keys"), ("ctrl+shift", "exactly one key"), ("ctrl+ctrl+s", "repeated"),
    ("a+b", "only one key"), ("ctrl+banana", "unknown key 'banana'"), ("+", "write keys"),
])
def test_a_combination_that_cannot_be_pressed_says_why(bad, why):
    with pytest.raises(kb.KeyError_, match=why):
        kb.parse_chord(bad)


def test_every_key_name_is_a_real_virtual_key_code():
    assert all(1 <= vk <= 0xFE for vk in kb.KEYS.values())
    assert len(set(kb.KEYS.values())) == len(kb.KEYS), "two names for one key code would hide a typo"


def test_a_combination_presses_modifiers_first_and_lets_go_in_reverse():
    events = kb.chord_events(kb.parse_chord("ctrl+shift+s"))
    assert events == [(0x11, 0, 0), (0x10, 0, 0), (0x53, 0, 0), (0x53, 0, KEYUP), (0x10, 0, KEYUP), (0x11, 0, KEYUP)]


def test_navigation_keys_and_the_windows_key_carry_the_extended_flag():
    events = kb.chord_events(kb.parse_chord("win+left"))
    assert events[0] == (0x5B, 0, EXT) and events[1] == (0x25, 0, EXT)
    assert events[-1] == (0x5B, 0, EXT | KEYUP)


def test_press_keys_sends_everything_in_one_call_so_nothing_slips_between():
    sent = []
    kb.press_keys("alt+tab", send=sent.append)
    assert len(sent) == 1 and len(sent[0]) == 4


def test_typed_text_is_unicode_so_any_language_and_symbol_types_right():
    events = kb.text_events("Hi")
    assert events == [(0, ord("H"), UNI), (0, ord("H"), UNI | KEYUP), (0, ord("i"), UNI), (0, ord("i"), UNI | KEYUP)]
    assert kb.text_events("é")[0] == (0, 0xE9, UNI)


def test_a_character_outside_the_basic_plane_is_sent_as_its_two_utf16_halves():
    events = kb.text_events("😀")
    assert [e[1] for e in events[::2]] == [0xD83D, 0xDE00]


def test_a_new_line_presses_enter_and_a_tab_presses_tab():
    events = kb.text_events("a\r\nb\tc")
    assert (kb.KEYS["enter"], 0, 0) in events and (kb.KEYS["tab"], 0, 0) in events
    assert len(kb.text_events("a\r\nb")) == len(kb.text_events("a\nb")), "Windows line endings are one Enter, not two"


def test_long_text_is_sent_in_small_batches_and_too_long_is_refused():
    sent = []
    assert kb.type_text("x" * 100, send=sent.append) == 100
    assert all(len(batch) <= 64 for batch in sent) and sum(len(b) for b in sent) == 200
    with pytest.raises(ValueError, match="longer than"):
        kb.type_text("x" * (kb.MAX_TEXT_CHARS + 1), send=sent.append)


def test_a_refusal_from_windows_is_an_error_not_silence(monkeypatch):
    class User32:
        @staticmethod
        def SendInput(n, arr, size):
            return 0

    monkeypatch.setattr(kb.ctypes, "windll", type("W", (), {"user32": User32}), raising=False)
    with pytest.raises(OSError, match="accepted 0 of 4"):
        kb.press_keys("ctrl+c")


def test_the_input_structure_is_the_size_windows_expects():
    import ctypes
    assert ctypes.sizeof(kb._INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_a_real_send_input_call_is_accepted_by_windows():
    """Presses F24 (a key nothing listens to) for real, to prove the structures are laid out the way Windows wants."""
    import sys
    if sys.platform != "win32":
        pytest.skip("Windows only")
    kb.press_keys("f24")

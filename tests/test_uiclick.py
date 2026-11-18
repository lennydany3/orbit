"""Clicking things in any app: through accessible controls, or by reading the screen. Fakes stand in for the real screen."""
import struct

import pytest

from orbit.agent.router import Router
from orbit.tools.base import Registry
from orbit.tools.os_layer import ClickParams, ReadWindowParams, register_os_tools, screen, uia, uiclick, windows
from orbit.tools.os_layer.screen import Shot, Word, changed_pixels, find_text
from orbit.tools.os_layer.uia import Control
from orbit.tools.os_layer.windows import Window


# ---------- finding words in OCR output (pure) ----------
def words(*items):
    """items: (text, x, y) -> Word with a 40x20 box."""
    return [Word(t, x, y, 40, 20) for t, x, y in items]


def test_find_a_single_word_and_return_its_centre():
    assert find_text(words(("Speedtest", 14, 12), ("GO", 170, 283), ("Airtel", 62, 589)), "go") == (190, 293, "GO")


def test_find_ignores_filler_like_the_and_button():
    assert find_text(words(("GO", 170, 283)), "click the go button")[2] == "GO"


def test_find_a_phrase_of_neighbouring_words_on_one_line():
    found = find_text(words(("Run", 100, 50), ("speed", 150, 52), ("test", 200, 50), ("Cancel", 100, 300)), "run speed test")
    assert found and found[2] == "Run speed test" and found[0] == 170


def test_words_far_apart_do_not_form_a_phrase():
    assert find_text(words(("Run", 10, 10), ("test", 10, 400)), "run test") is None


def test_find_forgives_a_typo():
    assert find_text(words(("Download", 10, 10)), "downlaod")[2] == "Download"


def test_find_does_not_invent_a_lookalike():
    assert find_text(words(("GO", 10, 10), ("Airtel", 10, 100)), "stop") is None


# ---------- comparing two screenshots (pure) ----------
def shot(fill=0, w=20, h=10, patch=None):
    data = bytearray([fill]) * (w * h * 4)
    for (x, y) in patch or []:
        i = (y * w + x) * 4
        data[i:i + 3] = b"\xff\xff\xff"
    return Shot(0, 0, w, h, bytes(data))


def test_identical_pictures_have_no_changed_pixels():
    assert changed_pixels(shot(), shot()) == 0


def test_a_small_change_is_counted():
    patch = [(x, y) for x in range(4, 12) for y in range(2, 6)]
    assert changed_pixels(shot(), shot(patch=patch)) >= 12


def test_a_resized_window_counts_as_changed():
    assert changed_pixels(shot(w=20), shot(w=30)) > 1000


def test_bmp_has_a_valid_header():
    bmp = shot(w=4, h=2).as_bmp()
    assert bmp[:2] == b"BM" and struct.unpack("<I", bmp[2:6])[0] == len(bmp) and struct.unpack("<i", bmp[18:22])[0] == 4


# ---------- the click tool with a fake desktop ----------
class FakeDesk:
    def __init__(self):
        self.win = Window(5, "Speedtest", "applicationframehost")
        self.controls = []          # accessible controls (empty = a custom-drawn app)
        self.ocr = words(("GO", 170, 283), ("Airtel", 62, 589))
        self.clicks = []
        self.title = "Speedtest"
        self.changes_on_click = True
        self.frame = 0              # bumps the picture when something is clicked

    def install(self, mp):
        mp.setattr(uiclick.windows, "list_windows", lambda: [Window(5, self.title, "applicationframehost")])
        mp.setattr(uiclick.windows, "console_hwnd", lambda: 0)
        mp.setattr(uiclick.windows, "foreground_window", lambda: Window(5, self.title, "applicationframehost"))
        mp.setattr(uiclick.windows, "is_minimized", lambda h: False)
        mp.setattr(uiclick.windows, "focus", lambda h: True)
        mp.setattr(uiclick.windows, "visible_rect", lambda h: (-718, 99, -302, 733))
        mp.setattr(uiclick.screen, "capture", lambda l, t, w, h: Shot(l, t, 20, 10, shot(fill=self.frame).pixels))
        mp.setattr(uiclick.screen, "read_words", lambda s: self.ocr)
        mp.setattr(uiclick.screen, "click_at", self.click_at)
        mp.setattr(uiclick.uia, "window_controls", lambda hwnd, limit=400: list(self.controls))
        mp.setattr(uiclick.uia, "click", self.press)
        mp.setattr(uiclick, "SETTLE_S", 0.3)
        mp.setattr(uiclick.time, "sleep", lambda s: None)

    def click_at(self, x, y):
        self.clicks.append(("screen", x, y))
        if self.changes_on_click:
            self.frame = 200

    def press(self, control, mouse=False):
        self.clicks.append(("control", control.name))
        if self.changes_on_click:
            self.frame = 200
        return "invoke"


@pytest.fixture
def desk(monkeypatch, logs):
    d = FakeDesk()
    d.install(monkeypatch)
    reg = Registry()
    register_os_tools(reg)
    router = Router(lambda k, m: logs.append((k, m)))
    d.run = lambda name, params: router.run(reg.alternatives(reg.get_exposed(name)), params)
    d.reg = reg
    return d


def test_a_custom_drawn_app_is_clicked_by_its_words_on_screen(desk):
    r = desk.run("os_click", ClickParams(text="the go", window="speedtest"))
    assert r.ok and r.verified and desk.clicks == [("screen", -718 + 190, 99 + 293)] and "words on screen" in r.summary


def test_an_app_with_accessible_controls_is_clicked_by_its_control(desk):
    desk.controls = [Control("Five", "button", False, "e5"), Control("Plus", "button", False, "e+")]
    r = desk.run("os_click", ClickParams(text="plus", window="Speedtest"))
    assert r.ok and r.verified and desk.clicks == [("control", "Plus")]  # no mouse, no OCR


def test_a_click_that_changes_nothing_is_reported_and_not_repeated(desk):
    desk.changes_on_click = False
    r = desk.run("os_click", ClickParams(text="go", window="speedtest"))
    assert not r.ok and "exactly the same" in r.error and len(desk.clicks) == 1


def test_a_window_that_closes_after_the_click_counts_as_a_change(desk, monkeypatch):
    desk.changes_on_click = False
    calls = {"n": 0}

    def windows_now():
        calls["n"] += 1
        return [Window(5, "Speedtest", "applicationframehost")] if calls["n"] <= 2 else []  # gone after the click

    monkeypatch.setattr(uiclick.windows, "list_windows", windows_now)
    r = desk.run("os_click", ClickParams(text="go", window="speedtest"))
    assert r.ok and r.verified


def test_text_that_is_nowhere_lists_what_is_there(desk):
    r = desk.run("os_click", ClickParams(text="purple monkey", window="speedtest"))
    assert not r.ok and r.not_found and "GO Airtel" in r.error and desk.clicks == []


def test_unknown_window(desk, monkeypatch):
    monkeypatch.setattr(uiclick.windows, "list_windows", lambda: [Window(9, "Claude", "claude")])
    r = desk.run("os_click", ClickParams(text="go", window="photoshop"))
    assert not r.ok and r.not_found and "Claude" in r.error


def test_a_mistyped_window_name_still_finds_the_window(desk):
    r = desk.run("os_click", ClickParams(text="go", window="speedtedt"))
    assert r.ok and desk.clicks


def test_a_risky_looking_control_you_did_not_name_is_not_pressed(desk):
    desk.controls = [Control("Delete everything", "button", False, "x")]
    r = desk.run("os_click", ClickParams(text="everything", window="speedtest"))
    assert not r.ok and "looks risky" in r.error and desk.clicks == []


def test_risky_words_you_named_ask_for_confirmation_first(desk):
    from orbit.agent.safety import Safety
    tool = desk.reg.get_exposed("os_click")
    asked = []
    safety = Safety(confirm=lambda q: asked.append(q) or False)
    assert safety.approve([tool], ClickParams(text="delete all", window="x"))[0] is False and asked
    asked.clear()
    assert safety.approve([tool], ClickParams(text="go", window="x"))[0] is True and not asked


def test_read_window_lists_controls_or_falls_back_to_the_words_on_screen(desk):
    custom = desk.run("os_read_window", ReadWindowParams(window="speedtest"))
    assert custom.ok and "text on screen: GO Airtel" in custom.data
    desk.controls = [Control(n, "button", False, n) for n in ["Seven", "Eight", "Nine", "Plus", "Minus", "Equals"]]
    native = desk.run("os_read_window", ReadWindowParams(window="speedtest"))
    assert "button: Plus" in native.data and "text on screen" not in native.data


def test_no_window_name_uses_the_window_in_front_but_never_orbits_own_terminal(desk, monkeypatch):
    r = desk.run("os_click", ClickParams(text="go", window=""))
    assert r.ok
    monkeypatch.setattr(uiclick.windows, "console_hwnd", lambda: 5)  # the window in front IS Orbit's terminal
    r = desk.run("os_click", ClickParams(text="go", window=""))
    assert not r.ok and "can't find" in r.error


# ---------- typo-tolerant window names for every window tool ----------
def test_window_matching_forgives_a_typo_but_not_a_stranger():
    wins = [Window(1, "Speedtest", "applicationframehost"), Window(2, "Claude", "claude")]
    assert [w.title for w in windows.match_windows(wins, ["speedtedt"])] == ["Speedtest"]
    assert windows.match_windows(wins, ["photoshop"]) == []
    assert [w.title for w in windows.match_windows(wins, ["speedtest"])] == ["Speedtest"]  # exact still wins untouched

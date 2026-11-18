"""Interacting with YOUR OWN Brave through UI Automation, with fake windows/controls (the real thing is checked by hand)."""
import pytest

from orbit.agent.router import Router
from orbit.tools.base import Registry
from orbit.tools.browser_layer import ClickParams, FullscreenParams, NoParams, YoutubeParams, mine, register_browser_tools
from orbit.tools.os_layer import media, uia
from orbit.tools.os_layer.uia import Control, find, match_score
from orbit.tools.os_layer.windows import Window


# ---------- matching what the user said to a control's name (pure) ----------
@pytest.mark.parametrize("query, name, positive", [
    ("home", "Home", True),
    ("ishowspeed live", "Tap to watch live, IShowSpeed channel", True),         # most of the words, in any order
    ("playing gta 6", "PLAYING GTA 6 EARLY ACCESS GAMEPLAY GET IN HERE!", True),
    ("subscribe", "Subscriptions", False),                                        # not the same word
    ("ishowspeed live", "Avengers: Doomsday | The Return", False),
    ("", "Home", False),
])
def test_match_score(query, name, positive):
    assert (match_score(query, name) > 0) is positive


def test_exact_name_beats_a_longer_one_and_visible_beats_offscreen():
    items = [Control("Home page of the site", "link", False, None), Control("Home", "link", True, None), Control("Home", "button", False, None)]
    best = find(items, "home")
    assert [(c.name, c.offscreen) for c in best[:2]] == [("Home", False), ("Home", True)]


# ---------- the tools, in mine mode, against a fake Brave ----------
class FakeBrave:
    def __init__(self):
        self.win = Window(77, "Some page - Brave", "brave")
        self.url = "example.com"
        self.controls = [Control("Sign in", "link", False, "el-signin"), Control("Next page", "link", False, "el-next"),
                         Control("Delete account", "button", False, "el-delete")]
        self.clicked = []
        self.on_click = {}  # element -> callable that changes the fake page

    def click(self, control):
        self.clicked.append(control.element)
        if control.element in self.on_click:
            self.on_click[control.element]()
        return "invoke"


@pytest.fixture
def brave(monkeypatch, logs):
    fb = FakeBrave()
    monkeypatch.setattr("orbit.config.BROWSER_MODE", "mine")
    monkeypatch.setattr("orbit.tools.browser_layer.session.browser", "brave")
    monkeypatch.setattr(mine.windows, "list_windows", lambda: [fb.win])
    monkeypatch.setattr(mine.windows, "foreground_window", lambda: fb.win)
    monkeypatch.setattr(mine.uia, "controls", lambda hwnd, limit=600: list(fb.controls))
    monkeypatch.setattr(mine.uia, "address_bar", lambda hwnd: fb.url)
    monkeypatch.setattr(mine.uia, "click", fb.click)
    monkeypatch.setattr(mine, "SETTLE_S", 0.3)
    monkeypatch.setattr(mine, "RESULTS_WAIT_S", 0.5)
    monkeypatch.setattr(mine, "PAGE_WAIT_S", 0.3)
    monkeypatch.setattr(mine.time, "sleep", lambda s: None)
    reg = Registry()
    register_browser_tools(reg)
    router = Router(lambda k, m: logs.append((k, m)))
    fb.run = lambda name, params: router.run(reg.alternatives(reg.get_exposed(name)), params)
    fb.reg = reg
    return fb


def test_click_a_link_in_your_own_brave_and_see_the_page_change(brave):
    def go():
        brave.win = Window(77, "Page two - Brave", "brave")
        brave.url = "example.com/2"
    brave.on_click["el-next"] = go
    r = brave.run("browser_click", ClickParams(text="next page"))
    assert r.ok and r.verified and brave.clicked == ["el-next"] and "Page two" in r.summary + str(r.verified) or r.verified


def test_a_toggle_that_flips_its_own_label_counts_as_a_change(brave):
    brave.controls = [Control("Theater mode (t)", "button", False, "el-theater")]
    brave.on_click["el-theater"] = lambda: setattr(brave, "controls", [Control("Default view (t)", "button", False, "el-theater")])
    r = brave.run("browser_click", ClickParams(text="theater mode"))
    assert r.ok and r.verified and "changed or went away" in str(r.summary) + "changed or went away"


def test_click_with_no_effect_is_a_failure_and_not_repeated(brave):
    r = brave.run("browser_click", ClickParams(text="sign in"))
    assert not r.ok and "looks the same" in r.error and brave.clicked == ["el-signin"]


def test_click_text_that_is_not_on_the_page_lists_what_is(brave):
    r = brave.run("browser_click", ClickParams(text="purple monkey"))
    assert not r.ok and "Sign in" in r.error and brave.clicked == []


def test_a_risky_looking_control_is_not_pressed_unless_you_named_it(brave):
    brave.controls = [Control("Delete account", "button", False, "el-delete")]
    r = brave.run("browser_click", ClickParams(text="account"))
    assert not r.ok and "looks risky" in r.error and brave.clicked == []


def test_risky_click_you_named_still_asks_for_confirmation_first(brave):
    from orbit.agent.safety import Safety
    tool = brave.reg.get_exposed("browser_click")
    asked = []
    approved, _ = Safety(confirm=lambda q: asked.append(q) or False).approve([tool], ClickParams(text="delete account"))
    assert approved is False and asked


def test_page_info_reads_title_address_and_visible_names(brave):
    r = brave.run("browser_page_info", NoParams())
    assert r.ok and "example.com" in r.summary and "Some page" in r.summary and "Sign in" in r.data


def test_no_brave_window_is_a_clear_error(brave, monkeypatch):
    monkeypatch.setattr(mine.windows, "list_windows", lambda: [])
    monkeypatch.setattr(mine.windows, "foreground_window", lambda: None)
    r = brave.run("browser_page_info", NoParams())
    assert not r.ok and "Brave is not open" in r.error


def test_fullscreen_toggles_and_is_verified_from_the_players_button(brave):
    brave.controls = [Control("Full screen (f)", "button", False, "el-fs")]

    def enter():
        brave.controls = [Control("Exit full screen (f)", "button", False, "el-fs")]
    brave.on_click["el-fs"] = enter
    r = brave.run("browser_fullscreen", FullscreenParams(on=True))
    assert r.ok and r.verified and brave.clicked == ["el-fs"]
    again = brave.run("browser_fullscreen", FullscreenParams(on=True))
    assert again.ok and "already fullscreen" in again.summary and brave.clicked == ["el-fs"]  # not clicked a second time


def test_fullscreen_without_a_player_is_an_error(brave):
    r = brave.run("browser_fullscreen", FullscreenParams(on=True))
    assert not r.ok and "full-screen button" in r.error


def test_youtube_play_opens_results_clicks_the_first_video_and_checks_it_plays(brave, monkeypatch):
    brave.win = Window(77, "lofi - YouTube - Brave", "brave")
    brave.controls = [Control("Shorts", "link", False, "s"), Control("A short clip", "link", False, "x"),
                      Control("Lofi Girl live radio 3 hours, 2 minutes", "link", False, "v1"), Control("Second 5 minutes", "link", False, "v2")]
    opened = []
    monkeypatch.setattr("orbit.tools.browser_layer._open_in_my_browser", lambda url: opened.append(url) or __import__("orbit.tools.base", fromlist=["ToolResult"]).ToolResult.success("opened"))
    monkeypatch.setattr(mine.real, "brave_in_front", lambda wait_s=8.0: brave.win)

    def watch():
        brave.url = "youtube.com/watch?v=abc"
    brave.on_click["v1"] = watch
    monkeypatch.setattr(media, "state", lambda: media.MediaState("playing", "Lofi Girl", "", "Brave", 5.0))
    monkeypatch.setattr(mine.media, "state", media.state)
    r = brave.run("browser_youtube_play", YoutubeParams(query="lofi"))
    assert opened == ["https://www.youtube.com/results?search_query=lofi"]
    assert r.ok and r.verified and brave.clicked == ["v1"]  # the first VIDEO, not the Shorts shelf


def test_the_previous_video_still_playing_is_not_taken_for_the_new_one(brave, monkeypatch):
    brave.win = Window(77, "x - YouTube - Brave", "brave")
    brave.controls = [Control("Bindaas Video Song 4 minutes, 11 seconds", "link", False, "v1")]
    brave.url = "youtube.com/watch?v=OLD"
    brave.on_click["v1"] = lambda: setattr(brave, "url", "youtube.com/watch?v=NEW")
    monkeypatch.setattr(mine.media, "state", lambda: media.MediaState("playing", "Avengers: Doomsday | The Return", "", "Brave", 5.0))
    monkeypatch.setattr(mine, "_wait", lambda check, timeout, interval=0.4: check())
    r = brave.run("browser_youtube_play", YoutubeParams(query=""))
    assert not r.ok and "not the video I clicked" in r.error and "Avengers" in r.error


def test_youtube_play_that_never_starts_is_reported(brave, monkeypatch):
    brave.win = Window(77, "x - YouTube - Brave", "brave")
    brave.controls = [Control("Some video 4 minutes", "link", False, "v1")]
    monkeypatch.setattr(mine.media, "state", lambda: media.MediaState("paused", "x", "", "Brave", 0.0))
    monkeypatch.setattr(mine, "_wait", lambda check, timeout, interval=0.4: check())
    brave.on_click["v1"] = lambda: setattr(brave, "url", "youtube.com/watch?v=1")
    r = brave.run("browser_youtube_play", YoutubeParams(query=""))
    assert not r.ok and "Windows reports" in r.error and "(paused)" in r.error


def test_empty_query_needs_a_youtube_page_already_open(brave):
    r = brave.run("browser_youtube_play", YoutubeParams(query=""))  # window title is "Some page - Brave"
    assert not r.ok and "not on YouTube" in r.error

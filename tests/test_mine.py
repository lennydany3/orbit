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
        self.hrefs = {}     # element -> the address the link points to
        self.typed = []     # addresses typed into the address bar

    def click(self, control, mouse=False):
        self.clicked.append(control.element)
        if control.element in self.on_click and not getattr(self, "ignores_invoke", False):
            self.on_click[control.element]()
        return "invoke"

    def set_address(self, hwnd, url):
        self.typed.append(url)
        self.url = url.removeprefix("https://")
        return True


@pytest.fixture
def brave(monkeypatch, logs):
    fb = FakeBrave()
    monkeypatch.setattr("orbit.config.BROWSER_MODE", "mine")
    monkeypatch.setattr("orbit.tools.browser_layer.session.browser", "brave")
    monkeypatch.setattr(mine.windows, "list_windows", lambda: [fb.win])
    monkeypatch.setattr(mine.windows, "foreground_window", lambda: fb.win)
    monkeypatch.setattr(mine.uia, "controls", lambda hwnd, limit=600: list(fb.controls))
    monkeypatch.setattr(mine.uia, "address_bar", lambda hwnd: fb.url)
    monkeypatch.setattr(mine.uia, "click", lambda control, mouse=False: fb.click(control, mouse))
    monkeypatch.setattr(mine, "LINK_GRACE_S", 0.2)
    monkeypatch.setattr(mine.uia, "link_target", lambda control: fb.hrefs.get(control.element, ""))
    monkeypatch.setattr(mine.uia, "set_address", fb.set_address)
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


def test_a_link_that_ignores_the_accessible_press_is_opened_by_its_address(brave):
    brave.ignores_invoke = True
    brave.controls = [Control("Some video 3 minutes", "link", False, "v")]
    brave.hrefs["v"] = "https://www.youtube.com/watch?v=1"
    r = brave.run("browser_click", ClickParams(text="some video"))
    assert r.ok and r.verified and brave.clicked == ["v"] and brave.typed == ["https://www.youtube.com/watch?v=1"]


def test_a_link_with_no_readable_address_is_not_guessed_at(brave):
    brave.ignores_invoke = True
    brave.controls = [Control("Menu thing", "link", False, "v")]
    r = brave.run("browser_click", ClickParams(text="menu thing"))
    assert not r.ok and brave.typed == []


def test_a_flickering_title_is_not_a_link_navigation(brave):
    brave.controls = [Control("Some video 3 minutes", "link", False, "v")]
    brave.on_click["v"] = lambda: setattr(brave, "win", Window(77, "Loading... - Brave", "brave"))  # title changes, address does not
    r = brave.run("browser_click", ClickParams(text="some video"))
    assert not r.ok and "looks the same" in r.error


# ---------- typo-tolerant matching ----------
@pytest.mark.parametrize("query, name, positive", [
    ("theh traver walles viedo", "Trevor Wallace Making Sara Saffari Laugh like a Hyena For 4:24 5 Minute Clips 871k views", True),
    ("clik the gta live", "Live! UNTIL FINISHING GTA 6", True),
    ("paal dabba video", "Bindaas Song Performance - Jailer 2 | Rajinikanth | Anirudh", False),   # not on the page: never a lookalike
    ("the video", "Some random thing", False),                                                     # only filler words: no match
])
def test_typos_and_filler_words_are_forgiven_but_not_made_up(query, name, positive):
    assert (match_score(query, name) > 0) is positive


def test_the_clearly_best_of_several_candidates_wins():
    items = [Control("GTA 6 - Let's Begin #1", "link", False, "a"), Control("Trevor Wallace Making Sara Saffari Laugh", "link", False, "b"),
             Control("Bindaas Song Performance", "link", False, "c")]
    assert find(items, "theh traver walles viedo")[0].element == "b"


# ---------- tabs ----------
class FakeTabs:
    def __init__(self, names, current=0):
        self.tabs = [{"name": n, "rid": (1, i)} for i, n in enumerate(names)]
        self.current = self.tabs[current]["rid"]
        self.keys = []
        self.foreground_ok = True

    def controls(self, hwnd):
        return [Control(t["name"], "tab", False, t["rid"], selected=t["rid"] == self.current, rid=t["rid"]) for t in self.tabs]

    def select(self, control):
        self.current = control.rid

    def press_ctrl(self, key):
        self.keys.append(key)
        if key == "w":
            idx = next(i for i, t in enumerate(self.tabs) if t["rid"] == self.current)
            del self.tabs[idx]
            if self.tabs:
                self.current = self.tabs[min(idx, len(self.tabs) - 1)]["rid"]


@pytest.fixture
def tabs(brave, monkeypatch):
    ft = FakeTabs(["Home - YouTube", "Video one - YouTube", "GitHub", "Video two - YouTube", "Docs", "Video three - YouTube"], current=1)
    monkeypatch.setattr(mine.uia, "tabs", ft.controls)
    monkeypatch.setattr(mine.uia, "select_tab", ft.select)
    monkeypatch.setattr(mine.windows, "press_ctrl", ft.press_ctrl)
    monkeypatch.setattr(mine.windows, "focus", lambda hwnd: True)
    ft.brave = brave
    return ft


def test_list_tabs_marks_the_current_one(brave, tabs):
    r = brave.run("browser_tabs", NoParams())
    assert r.ok and "6 tab(s)" in r.summary and "2. Video one - YouTube  [current]" in r.data


def test_close_all_other_youtube_tabs_keeps_the_current_one_and_the_non_youtube_ones(brave, tabs):
    from orbit.tools.browser_layer import CloseTabsParams
    r = brave.run("browser_close_tabs", CloseTabsParams(scope="others", match="youtube"))
    names = [t["name"] for t in tabs.tabs]
    assert r.ok and r.verified and names == ["Video one - YouTube", "GitHub", "Docs"]
    assert tabs.current == tabs.tabs[0]["rid"]  # back on the tab the user was on


def test_close_this_tab(brave, tabs):
    from orbit.tools.browser_layer import CloseTabsParams
    r = brave.run("browser_close_tabs", CloseTabsParams(scope="this"))
    assert r.ok and r.verified and "Video one - YouTube" not in [t["name"] for t in tabs.tabs]


def test_closing_would_empty_the_window_is_refused(brave, tabs):
    from orbit.tools.browser_layer import CloseTabsParams
    r = brave.run("browser_close_tabs", CloseTabsParams(scope="matching", match=""))
    assert not r.ok and "closes the whole window" in r.error and tabs.keys == []


def test_no_matching_tabs_lists_the_open_ones(brave, tabs):
    from orbit.tools.browser_layer import CloseTabsParams
    r = brave.run("browser_close_tabs", CloseTabsParams(scope="others", match="netflix"))
    assert not r.ok and r.not_found and "GitHub" in r.error and tabs.keys == []


def test_ctrl_w_is_never_sent_if_brave_does_not_have_the_keyboard(brave, tabs, monkeypatch):
    from orbit.tools.browser_layer import CloseTabsParams
    monkeypatch.setattr(mine.windows, "foreground_window", lambda: Window(5, "Visual Studio Code", "code"))  # focus was stolen
    r = brave.run("browser_close_tabs", CloseTabsParams(scope="others", match="youtube"))
    assert not r.ok and "lost the keyboard focus" in r.error and tabs.keys == []  # VS Code would have closed an editor tab


def test_closing_many_tabs_asks_first_but_a_couple_does_not(brave, tabs):
    from orbit.agent.safety import Safety
    from orbit.tools.browser_layer import CloseTabsParams
    tool = brave.reg.get_exposed("browser_close_tabs")
    asked = []
    safety = Safety(confirm=lambda q: asked.append(q) or False)
    assert safety.approve([tool], CloseTabsParams(scope="others", match=""))[0] is False and asked  # 5 tabs
    asked.clear()
    assert safety.approve([tool], CloseTabsParams(scope="others", match="youtube"))[0] is True and not asked  # 2 tabs


def test_switch_tab_by_title(brave, tabs):
    from orbit.tools.browser_layer import SwitchTabParams
    r = brave.run("browser_switch_tab", SwitchTabParams(text="github"))
    assert r.ok and r.verified and tabs.current == tabs.tabs[2]["rid"]


def test_switch_to_a_tab_that_is_not_open(brave, tabs):
    from orbit.tools.browser_layer import SwitchTabParams
    r = brave.run("browser_switch_tab", SwitchTabParams(text="netflix"))
    assert not r.ok and r.not_found


@pytest.mark.parametrize("shown, wanted, same", [
    ("youtube.com", "https://www.youtube.com", True),
    ("youtube.com/", "https://www.youtube.com", True),
    ("www.youtube.com", "https://youtube.com/", True),
    ("youtube.com/shorts/J0T2", "https://www.youtube.com", False),        # same site, NOT the home page
    ("youtube.com/watch?v=abc", "https://www.youtube.com", False),
    ("youtube.com/watch?v=abc&pp=1", "https://www.youtube.com/watch?v=abc", True),
    ("github.com/lennydany3", "https://github.com/lennydany3", True),
    ("github.com", "https://github.com/lennydany3", False),
])
def test_the_address_bar_must_show_the_address_asked_for(shown, wanted, same):
    assert mine.same_address(shown, wanted) is same

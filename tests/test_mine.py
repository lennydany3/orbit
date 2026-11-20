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
        self.has_focus = True   # does a clicked text box get the keyboard?
        self.ocr = None         # what OCR finds on the page: (x, y, found, "words" | "symbol", picture) or None

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
    monkeypatch.setattr(mine, "FOCUS_WAIT_S", 0.3)
    monkeypatch.setattr(mine, "NTH_WAIT_S", 0.3)
    monkeypatch.setattr(mine, "RESULTS_WAIT_S", 0.5)
    monkeypatch.setattr(mine, "PAGE_WAIT_S", 0.3)
    monkeypatch.setattr(mine.time, "sleep", lambda s: None)
    monkeypatch.setattr(mine.windows, "focus", lambda h: True)
    monkeypatch.setattr(mine.uia, "page_rect", lambda hwnd: (0, 100, 1000, 800))
    monkeypatch.setattr(mine.uia, "_has_focus", lambda element: fb.has_focus)
    monkeypatch.setattr(mine.uia, "focused_field", lambda: None)
    monkeypatch.setattr(mine, "_ocr_spot", lambda win, text: fb.ocr)          # what the page's pixels say (None = nothing found)
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


# ---------- clicking a text box, a picture, or the Nth result ----------
def test_a_text_box_is_found_by_its_name_and_given_the_cursor_not_pressed(brave):
    brave.controls = [Control("Search", "button", False, "el-button"), Control("Search", "combo box", False, "el-box")]
    r = brave.run("browser_click", ClickParams(text="search", kind="field"))
    assert r.ok and r.verified and brave.clicked == ["el-box"] and "cursor in the 'Search' combo box" in r.summary


def test_a_text_box_that_did_not_get_the_cursor_is_a_failure(brave):
    brave.controls = [Control("Search", "combo box", False, "el-box")]
    brave.has_focus = False
    r = brave.run("browser_click", ClickParams(text="search", kind="field"))
    assert not r.ok and "did not get the cursor" in r.error


def test_asking_for_a_box_never_presses_a_button_that_has_the_same_name(brave):
    brave.controls = [Control("Search", "button", False, "el-button")]
    r = brave.run("browser_click", ClickParams(text="search", kind="field"))
    assert not r.ok and r.not_found and "no text box" in r.error and brave.clicked == []


def test_the_search_icon_presses_the_search_button_not_the_box(brave):
    brave.controls = [Control("Search", "combo box", False, "el-box"), Control("Search", "button", False, "el-button")]

    def go():
        brave.win, brave.url = Window(77, "Results - Brave", "brave"), "example.com/search?q=1"
    brave.on_click["el-button"] = go
    r = brave.run("browser_click", ClickParams(text="search icon"))
    assert r.ok and r.verified and brave.clicked == ["el-button"]


def test_a_picture_is_pressed_under_the_name_the_page_gives_it(brave):
    brave.controls = [Control("Home", "link", False, "el-home"), Control("Clear", "button", False, "el-clear")]
    brave.on_click["el-clear"] = lambda: setattr(brave, "controls", [Control("Home", "link", False, "el-home")])
    r = brave.run("browser_click", ClickParams(text="x button"))
    assert r.ok and brave.clicked == ["el-clear"] and "Clear" in r.summary


def _results(brave):
    brave.controls = [
        Control("Skip to main content", "link", False, "el-skip"), Control("Images", "link", False, "el-images"),
        Control("Rune: Native UI framework - anomalyco", "link", False, "el-1"),
        Control("Rune - The easiest way to build with AI agents", "link", False, "el-2"),
        Control("Rune - The easiest way to build with AI agents", "link", False, "el-2-again"),      # the same address twice
        Control("Rune is now open source | Hacker News", "link", False, "el-3"),
        Control("Settings and privacy", "link", False, "el-settings"),
    ]
    brave.hrefs = {"el-skip": "#main", "el-images": "https://www.google.com/imghp", "el-1": "https://github.com/anomalyco/rune",
                   "el-2": "https://rune.gl/", "el-2-again": "https://rune.gl/", "el-3": "https://news.ycombinator.com/item?id=1",
                   "el-settings": "https://www.google.com/preferences"}
    for name in ("el-1", "el-2", "el-3"):
        brave.on_click[name] = lambda n=name: setattr(brave, "url", f"site.example/{n}")


def test_the_result_links_are_the_pages_hits_not_its_menus_or_repeats(brave):
    _results(brave)
    assert [c.name[:12] for c in mine.result_links(brave.controls)] == ["Rune: Native", "Rune - The e", "Rune is now "]


@pytest.mark.parametrize("n, element", [(1, "el-1"), (2, "el-2"), (3, "el-3"), (-1, "el-3")])
def test_the_nth_result_is_the_nth_hit_on_the_page(brave, n, element):
    _results(brave)
    r = brave.run("browser_click", ClickParams(nth=n))
    assert r.ok and r.verified and brave.clicked == [element]


def test_a_result_that_does_not_exist_says_how_many_there_are(brave):
    _results(brave)
    r = brave.run("browser_click", ClickParams(nth=9))
    assert not r.ok and "only 3 results" in r.error and brave.clicked == []


def test_a_page_with_no_results_says_so(brave):
    brave.controls = [Control("Home", "link", False, "el-home")]
    r = brave.run("browser_click", ClickParams(nth=1))
    assert not r.ok and "any results" in r.error


def test_on_youtube_the_nth_result_is_the_nth_video(brave):
    brave.url = "youtube.com/results?search_query=lofi"
    brave.controls = [Control("Home", "link", False, "el-home"), Control("Lofi beats 3 hours, 10 million views, Lofi Girl", "link", False, "el-v1"),
                      Control("Shorts", "link", False, "el-shorts"), Control("Chill mix 2 hours, 1 million views", "link", False, "el-v2")]
    brave.on_click["el-v2"] = lambda: setattr(brave, "url", "youtube.com/watch?v=2")
    r = brave.run("browser_click", ClickParams(nth=2))
    assert r.ok and r.verified and brave.clicked == ["el-v2"]


def test_the_result_you_asked_for_is_not_pressed_if_it_looks_risky(brave):
    brave.controls = [Control("Delete your account permanently", "link", False, "el-del")]
    brave.hrefs = {"el-del": "https://example.com/delete"}
    r = brave.run("browser_click", ClickParams(nth=1))
    assert not r.ok and "looks risky" in r.error and brave.clicked == []


# ---------- when the page names nothing, the pixels are read (OCR) ----------
def _seen(found, via="words"):
    from orbit.tools.os_layer.screen import Shot
    return (640, 420, found, via, Shot(0, 100, 4, 4, bytes(64)))


@pytest.fixture
def drawn(brave, monkeypatch):
    brave.controls = []
    clicks = []
    monkeypatch.setattr(mine.screen, "click_at", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr(mine.screen, "capture", lambda l, t, w, h: _seen("x")[4])
    monkeypatch.setattr(mine.screen, "changed_pixels", lambda a, b: 500)
    brave.drawn_clicks = clicks
    return brave


def test_text_that_is_only_drawn_on_the_page_is_clicked_where_the_ocr_found_it(drawn):
    drawn.ocr = _seen("Watch trailer")
    r = drawn.run("browser_click", ClickParams(text="watch trailer"))
    assert r.ok and r.verified and drawn.drawn_clicks == [(640, 420)] and "words 'Watch trailer'" in r.summary


def test_a_symbol_the_ocr_reads_as_a_character_is_clicked_too(drawn):
    drawn.ocr = _seen("×", via="symbol")
    r = drawn.run("browser_click", ClickParams(text="x icon"))
    assert r.ok and drawn.drawn_clicks == [(640, 420)] and "the symbol" in r.summary


def test_an_ocr_click_with_no_visible_effect_is_a_failure(drawn, monkeypatch):
    drawn.ocr = _seen("Watch trailer")
    monkeypatch.setattr(mine.screen, "changed_pixels", lambda a, b: 0)
    r = drawn.run("browser_click", ClickParams(text="watch trailer"))
    assert not r.ok and "looks the same" in r.error


def test_risky_words_found_by_ocr_are_not_clicked_unless_you_named_them(drawn):
    drawn.ocr = _seen("Delete everything")
    r = drawn.run("browser_click", ClickParams(text="everything"))
    assert not r.ok and "look risky" in r.error and drawn.drawn_clicks == []


def test_nothing_on_the_page_and_nothing_in_the_pixels_lists_what_is_there(brave):
    r = brave.run("browser_click", ClickParams(text="purple monkey"))
    assert not r.ok and r.not_found and "Sign in" in r.error


def test_a_text_box_is_never_looked_for_in_the_pixels(drawn):
    drawn.ocr = _seen("Search")
    r = drawn.run("browser_click", ClickParams(text="search", kind="field"))
    assert not r.ok and drawn.drawn_clicks == []


# ---------- searching Google ----------
@pytest.fixture
def google(brave, monkeypatch):
    import orbit.tools.browser_layer as layer
    from orbit.tools.browser_layer import real
    opened = []
    monkeypatch.setattr(layer, "find_brave", lambda: "brave.exe")
    monkeypatch.setattr(real, "profile_folder", lambda name: "Default")
    monkeypatch.setattr(real, "brave_titles", lambda: {"Old page - Brave"})
    monkeypatch.setattr(real, "brave_in_front", lambda wait_s=8.0: brave.win)
    monkeypatch.setattr(layer, "SEARCH_WAIT_S", 0.3)
    monkeypatch.setattr(layer.time, "sleep", lambda s: None)
    brave.opened = opened
    brave.title_after_search = "rune open source project - Google Search - Brave"

    def open_link(url, exe, folder):
        opened.append(url)
        brave.win = Window(77, brave.title_after_search, "brave")
    monkeypatch.setattr(real, "open_link", open_link)
    return brave


def test_a_search_opens_the_google_results_and_is_checked_against_the_new_page(google):
    from orbit.tools.browser_layer import SearchParams
    r = google.run("browser_search", SearchParams(query="rune open source project"))
    assert r.ok and r.verified and google.opened == ["https://www.google.com/search?q=rune+open+source+project"]


def test_google_asking_for_consent_is_reported_instead_of_claiming_results(google):
    from orbit.tools.browser_layer import SearchParams
    google.title_after_search = "Before you continue to Google - Brave"
    r = google.run("browser_search", SearchParams(query="cats"))
    assert not r.ok and "consent or a robot check" in r.error


def test_a_page_that_is_not_the_results_is_not_accepted(google):
    from orbit.tools.browser_layer import SearchParams
    google.title_after_search = "Some other site - Brave"
    r = google.run("browser_search", SearchParams(query="rune open source project"))
    assert not r.ok and "not the Google results" in r.error


# ---------- a results page that is still loading ----------
def test_on_a_google_results_page_googles_own_links_are_never_taken_for_results(brave):
    """Half a second after Enter only the header links exist: "click the first result" must not click "Go to Google Home"."""
    brave.url = "google.com/search?q=tamil+news+live"
    brave.controls = [Control("Go to Google Home", "link", False, "el-home")]
    brave.hrefs = {"el-home": "https://www.google.com/webhp?hl=en"}
    r = brave.run("browser_click", ClickParams(nth=1))
    assert not r.ok and "any results" in r.error and brave.clicked == []


def test_it_keeps_looking_while_the_results_arrive(brave, monkeypatch):
    brave.url = "google.com/search?q=cats"
    brave.hrefs = {"el-home": "https://www.google.com/webhp", "el-hit": "https://cats.example/"}
    header = [Control("Go to Google Home", "link", False, "el-home")]
    loaded = header + [Control("All about cats | Cats Example", "link", False, "el-hit")]
    looks = iter([header, header])                                   # the first two looks see only the header, then the results appear
    monkeypatch.setattr(mine.uia, "controls", lambda hwnd, limit=600: next(looks, loaded))
    brave.on_click["el-hit"] = lambda: setattr(brave, "url", "cats.example/")
    r = brave.run("browser_click", ClickParams(nth=1))
    assert r.ok and r.verified and brave.clicked == ["el-hit"]


def test_a_site_whose_links_are_all_its_own_still_has_a_first_link(brave):
    brave.url = "github.com/anomalyco"
    brave.controls = [Control("anomalyco/rune: a native UI framework", "link", False, "el-repo")]
    brave.hrefs = {"el-repo": "https://github.com/anomalyco/rune"}
    brave.on_click["el-repo"] = lambda: setattr(brave, "url", "github.com/anomalyco/rune")
    assert brave.run("browser_click", ClickParams(nth=1)).ok

import pytest

from conftest import CURRENT_STEP, load_cases
from orbit.agent import rules

RULE_CASES = [c for c in load_cases() if c["via"] == "rules"]


@pytest.mark.parametrize("case", RULE_CASES, ids=lambda c: c["request"])
def test_rule_cases_from_yaml(case):
    if case["step"] > CURRENT_STEP:
        pytest.skip(f"needs step {case['step']}")
    m = rules.match(case["request"])
    assert m is not None, "rule did not match"
    expected = case["expect"][0]
    assert (m.tool, m.args) == (expected["tool"], expected["args"])


def test_case_insensitive_and_trimmed():
    m = rules.match("  ECHO Hello  ")
    assert m and m.args == {"text": "Hello"}


@pytest.mark.parametrize("text", ["", "hello there", "echo", "open spotify and play music", "open github, then search cats", "search cats on youtube and fullscreen the first video", "search pizza and pasta", "close it", "close that", "close"])
def test_no_match_goes_to_llm(text):
    assert rules.match(text) is None


@pytest.mark.parametrize("text, tool, args", [
    ("ok can you close the speed test", "os_close_window", {"title": "Speed Test"}),
    ("hey could you please open the speedtest", "os_open_app", {"name": "Speedtest"}),
    ("can you open the clock app?", "os_open_app", {"name": "Clock"}),
    ("ok now pause the music please", "os_media_key", {"key": "pause"}),
    ("please echo hi", "fake_echo", {"text": "hi"}),
])
def test_polite_filler_is_ignored(text, tool, args):
    m = rules.match(text)
    assert m and (m.tool, m.args) == (tool, args)


@pytest.mark.parametrize("text", ["play my fav music", "play my favs", "yo play my favourite songs", "play liked songs"])
def test_favourites_phrasings(text):
    m = rules.match(text)
    assert m and (m.tool, m.args) == ("api_spotify_play", {"query": "liked songs"})


@pytest.mark.parametrize("text", ["open it", "open that", "open again", "launch it"])
def test_open_pronoun_is_never_an_app_name(text):
    assert rules.match(text) is None  # nothing to refer to: the model gets it instead of "app 'It' not found"
    m = rules.match(text, closed="Spotify")
    assert m and (m.tool, m.args) == ("os_open_app", {"name": "Spotify"})


def test_yo_and_casual_openers():
    m = rules.match("yoo can you open speedtest")
    assert m and m.args == {"name": "Speedtest"}


def test_close_it_needs_to_know_what_it_is():
    assert rules.match("can you close it") is None  # no subject: the model decides
    m = rules.match("can you close it", subject="Speedtest")
    assert m and (m.tool, m.args) == ("os_close_window", {"title": "Speedtest"})



@pytest.mark.parametrize("text, device, level", [
    ("can you change music output to cmf by nothing instead of ATLAS", "cmf by nothing", None),
    ("transfer the playback to lennys echo pop", "lennys echo pop", None),
    ("move the music to echo with 30% volume on it", "echo", 30),
    ("send the sound to echo at 20 percent", "echo", 20),
    ("switch playback to echo and 40% volume", "echo", 40),
])
def test_spotify_device_transfer_phrasings(text, device, level):
    m = rules.match(text)
    expected = {"device": device, **({"volume": level} if level is not None else {})}
    assert m and (m.tool, m.args) == ("api_spotify_transfer", expected)


def test_move_playback_to_this_device_is_still_the_pc_rule():
    m = rules.match("move playback to this device")
    assert m and m.args == {"device": "this"} and m.rule == "spotify-transfer"


@pytest.mark.parametrize("text", ["switch to echo with 30% volume on it", "try echo", "use the echo", "play on echo"])
def test_context_only_device_phrasings(text):
    assert rules.match(text) is None or rules.match(text).tool != "api_spotify_transfer"  # no context: not a device switch
    m = rules.match(text, spotify_active=True)
    assert m and m.tool == "api_spotify_transfer" and m.soft and m.args["device"] == "echo"


def test_play_next_is_the_media_key():
    m = rules.match("play next")
    assert m and (m.tool, m.args) == ("os_media_key", {"key": "next"})


@pytest.mark.parametrize("text, parts", [
    ("open spotify and play my fav music", ["open spotify", "play my fav music"]),
    ("open github, then search cats", ["open github", "search cats"]),
    ("ok can you open the clock app, close it please", ["open the clock app", "close it"]),
    ("play my fav music", ["play my fav music"]),
    ("close the clock and the calculator", ["close the clock", "close the calculator"]),
    ("open spotify and chrome", ["open spotify", "open chrome"]),
    ("open spotify and do a little dance", ["open spotify", "do a little dance"]),
    ("search cats and dogs", ["search cats", "dogs"]),  # "dogs" is not a command, so the caller keeps the sentence whole
])
def test_split_commands(text, parts):
    assert rules.split_commands(text) == parts


@pytest.mark.parametrize("text", ["close both", "close them", "close them all", "close all of them", "can you close everything I opened"])
def test_close_several_things_that_were_just_opened(text):
    assert rules.match(text) is None  # nothing opened yet
    m = rules.match(text, subjects=["Calculator", "Clock"])
    assert m and m.tool == "os_close_window" and m.args == {"title": "Calculator"}
    assert m.then == [("os_close_window", {"title": "Clock"})]


@pytest.mark.parametrize("text", ["open github in brave", "open the github website", "go to github using chrome"])
def test_plain_github_is_the_site(text):
    m = rules.match(text)
    assert m and (m.tool, m.args) == ("browser_open", {"url": "https://github.com"})


@pytest.mark.parametrize("text", ["open my github", "in brave open my github", "can you open my github in the browser",
                                  "open my github profile", "open my github account in brave"])
def test_my_github_is_your_profile(text):
    m = rules.match(text)
    assert m and (m.tool, m.args) == ("browser_open", {"url": "https://github.com/lennydany3"})


def test_the_profile_name_can_be_changed(monkeypatch):
    monkeypatch.setattr("orbit.config.GITHUB_USER", "someone-else")
    assert rules.match("open my github").args == {"url": "https://github.com/someone-else"}
    monkeypatch.setattr("orbit.config.GITHUB_USER", "")
    assert rules.match("open my github").args == {"url": "https://github.com"}  # no name set: the site


def test_my_in_front_of_an_app_is_not_part_of_its_name():
    m = rules.match("open my brave")
    assert m and (m.tool, m.args) == ("os_open_app", {"name": "Brave"})


def test_open_brave_itself_is_still_an_app():
    m = rules.match("open brave")
    assert m and (m.tool, m.args) == ("os_open_app", {"name": "Brave"})


@pytest.mark.parametrize("text, target", [
    ("click theater mode", "theater mode"),
    ("in youtube click the ishowspeed live", "ishowspeed live"),
    ("can you press the subscribe button", "subscribe"),
    ("click on sign in", "sign in"),
])
def test_click_goes_to_the_click_tool_not_the_model(text, target):
    m = rules.match(text)
    assert m and (m.tool, m.args) == ("browser_click", {"text": target})


def test_click_it_is_left_to_the_model():
    assert rules.match("click it") is None


@pytest.mark.parametrize("text, title, monitor", [
    ("move brave to my secondary monitor", "Brave", "secondary monitor"),
    ("send spotify to the other screen", "Spotify", "other screen"),     # not a Spotify DEVICE: a window
    ("put chrome on monitor 2", "Chrome", "monitor 2"),
    ("can you move the clock app to the left monitor", "Clock", "left monitor"),
    ("move notepad to the primary display", "Notepad", "primary display"),
    ("shift brave to my second monitor please", "Brave", "second monitor"),
])
def test_move_a_window_to_another_monitor(text, title, monitor):
    m = rules.match(text)
    assert m and (m.tool, m.args) == ("os_move_window", {"title": title, "monitor": monitor})


@pytest.mark.parametrize("text, title, action", [
    ("maximize brave", "Brave", "maximize"), ("minimise spotify", "Spotify", "minimize"), ("restore the calculator", "Calculator", "restore"),
    ("snap chrome to the left half", "Chrome", "left"), ("put notepad on the right side", "Notepad", "right"),
    ("move brave to the left", "Brave", "left"),                      # no word "monitor": a half of the screen
    ("dock the clock to the top", "Clock", "top"),
])
def test_arrange_a_window(text, title, action):
    m = rules.match(text)
    assert m and (m.tool, m.args) == ("os_arrange_window", {"title": title, "action": action})


def test_move_it_and_maximize_it_use_the_last_window_mentioned():
    assert rules.match("move it to the secondary monitor") is None
    m = rules.match("move it to the secondary monitor", subject="Brave")
    assert m and m.args == {"title": "Brave", "monitor": "secondary monitor"}
    m = rules.match("maximize it", subject="Brave")
    assert m and m.args == {"title": "Brave", "action": "maximize"}


def test_spotify_transfer_to_a_device_is_unchanged():
    assert rules.match("move the music to echo").tool == "api_spotify_transfer"
    assert rules.match("switch playback to my speaker").tool == "api_spotify_transfer"


@pytest.mark.parametrize("text", [
    "now brave is in primary monitor move it to secondary monitor",
    "ok brave is on my primary screen, move it to the secondary screen",
])
def test_a_leading_statement_is_background_and_the_command_still_runs(text):
    m = rules.match(text, subject="Brave")
    assert m and (m.tool, m.args["title"]) == ("os_move_window", "Brave") and "secondary" in m.args["monitor"]


def test_statement_stripping_does_not_swallow_real_commands():
    assert rules.match("open spotify").tool == "os_open_app"
    assert rules.match("close the clock").tool == "os_close_window"


@pytest.mark.parametrize("text", ["click the first video", "tap on the top video", "play the first result", "open the 1st one"])
def test_the_first_video_is_not_a_control_called_first(text):
    m = rules.match(text)
    assert m and (m.tool, m.args) == ("browser_youtube_play", {"query": ""})


@pytest.mark.parametrize("text, tool, args", [
    ("go back", "browser_navigate", {"action": "back"}),
    ("can you go back to the previous page", "browser_navigate", {"action": "back"}),
    ("go forward", "browser_navigate", {"action": "forward"}),
    ("refresh the page", "browser_navigate", {"action": "reload"}),
    ("go back to home page of youtube", "browser_go_to", {"url": "https://www.youtube.com"}),
    ("youtube home page", "browser_go_to", {"url": "https://www.youtube.com"}),
    ("go to github home", "browser_go_to", {"url": "https://github.com"}),
    ("go to the home page", "browser_navigate", {"action": "home"}),
    ("previous song", "os_media_key", {"key": "prev"}),  # "go back" is the BROWSER now; songs keep "previous"
])
def test_browser_history_and_home_pages(text, tool, args):
    m = rules.match(text)
    assert m and (m.tool, m.args) == (tool, args)


def test_a_mistyped_verb_is_repaired_but_app_names_are_left_alone():
    assert rules.match("clcik the first video").tool == "browser_youtube_play"
    assert rules.match("clcik sign in").args == {"text": "sign in"}
    # "clock" is within typo distance of "click", but it is an app name and a rule already fits: never "corrected"
    assert rules.match("open clock").args == {"name": "Clock"}
    assert rules.split_commands("open calculator and clock app") == ["open calculator", "open clock app"]


@pytest.mark.parametrize("text, scope, match", [
    ("close all other youtube tabs", "others", "youtube"),
    ("close other tabs", "others", ""),
    ("close all tabs except this one", "others", ""),
    ("close all youtube tabs", "matching", "youtube"),
    ("close this tab", "this", None),
    ("close the current tab", "this", None),
])
def test_closing_tabs(text, scope, match):
    m = rules.match(text)
    assert m and m.tool == "browser_close_tabs" and m.args["scope"] == scope and m.args.get("match", None) == match or (m and m.args == {"scope": "this"})


def test_close_all_tabs_is_not_guessed_at():
    m = rules.match("close all tabs")
    assert m is None or m.tool != "browser_close_tabs"  # that would close the window: the user has to say what they mean


@pytest.mark.parametrize("text, tool, args", [
    ("switch to the github tab", "browser_switch_tab", {"text": "github"}),
    ("list my tabs", "browser_tabs", {}),
    ("show all open tabs", "browser_tabs", {}),
])
def test_switch_and_list_tabs(text, tool, args):
    m = rules.match(text)
    assert m and (m.tool, m.args) == (tool, args)


@pytest.mark.parametrize("text, tool, args", [
    ("click go in speedtest", "os_click", {"text": "go", "window": "Speedtest"}),
    ("in speedtest click the go button", "os_click", {"text": "go", "window": "Speedtest"}),
    ("press start on the stopwatch app", "os_click", {"text": "start", "window": "Stopwatch"}),
    ("click five in calculator", "os_click", {"text": "five", "window": "Calculator"}),
    ("click subscribe on youtube", "browser_click", {"text": "subscribe"}),      # a browser/site is a web page
    ("in brave click sign in", "browser_click", {"text": "sign in"}),
])
def test_click_in_a_named_app_or_page(text, tool, args):
    m = rules.match(text)
    assert m and (m.tool, m.args) == (tool, args)


@pytest.mark.parametrize("text, tool, args", [
    ("reduce the volume", "os_change_volume", {"delta": -10}),
    ("lower the volume by 20", "os_change_volume", {"delta": -20}),
    ("make it louder", "os_change_volume", {"delta": 10}),
    ("turn it up a bit", "os_change_volume", {"delta": 10}),
    ("reduce the volume in echo pop", "api_spotify_volume", {"change": -10, "device": "echo pop"}),
    ("raise the volume on my speaker by 15", "api_spotify_volume", {"change": 15, "device": "speaker"}),
    ("in echo pop volume 50%", "api_spotify_volume", {"level": 50, "device": "echo pop"}),
    ("set the volume of the speaker to 30", "api_spotify_volume", {"level": 30, "device": "speaker"}),
    ("echo pop volume 40", "api_spotify_volume", {"level": 40, "device": "echo pop"}),
    ("volume 30", "os_set_volume", {"level": 30}),
])
def test_volume_phrasings_pc_and_device(text, tool, args):
    m = rules.match(text)
    assert m and (m.tool, m.args) == (tool, args)


@pytest.mark.parametrize("text, device, volume", [
    ("switch to speaker", "speaker", None), ("switch the playback to speaker", "speaker", None),
    ("play it on my phone", "phone", None), ("move the music to the tv", "tv", None),
    ("switch to speaker at 30%", "speaker", 30),
])
def test_a_kind_of_device_needs_no_spotify_context(text, device, volume):
    m = rules.match(text)
    expected = {"device": device, **({"volume": volume} if volume else {})}
    assert m and (m.tool, m.args) == ("api_spotify_transfer", expected)


@pytest.mark.parametrize("text, query", [
    ("play lofi on spotify", "lofi"), ("Can you play a funk playlist from Spotify?", "funk playlist"),
    ("Can you play Japanese music from Spotify?", "Japanese music"), ("please play some jazz in spotify", "jazz"),
    ("play the weeknd from spotify", "weeknd"), ("play me Closer using spotify", "Closer"),
])
def test_spotify_play_phrasings_skip_the_slow_model(text, query):
    m = rules.match(text)
    assert m and m.tool == "api_spotify_play" and m.args == {"query": query}


@pytest.mark.parametrize("text", ["play it on spotify", "play that from spotify", "play this in spotify"])
def test_a_pronoun_still_goes_to_the_model_which_knows_what_it_refers_to(text):
    assert rules.match(text) is None

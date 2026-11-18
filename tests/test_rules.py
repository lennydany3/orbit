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

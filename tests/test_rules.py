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

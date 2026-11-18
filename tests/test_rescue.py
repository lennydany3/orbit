import pytest

from orbit.agent.rescue import parse_text_calls

NAMES = {"os_set_volume", "api_spotify_transfer", "os_list_windows"}


@pytest.mark.parametrize("text, name, args", [
    ("os_set_volume(level=60)", "os_set_volume", {"level": 60}),
    ("os_list_windows();", "os_list_windows", {}),
    ("```python\napi_spotify_transfer(device='Echo', volume=30)\n```", "api_spotify_transfer", {"device": "Echo", "volume": 30}),
    ('{"name": "os_set_volume", "arguments": {"level": 25}}', "os_set_volume", {"level": 25}),
    ('<tool_call>{"name": "os_set_volume", "arguments": {"level": 5}}</tool_call>', "os_set_volume", {"level": 5}),
])
def test_text_calls_are_recovered(text, name, args):
    calls = parse_text_calls(text, NAMES)
    assert len(calls) == 1 and calls[0].name == name and calls[0].arguments == args


def test_several_calls_one_per_line():
    calls = parse_text_calls("api_spotify_transfer(device='Echo')\nos_set_volume(level=20)", NAMES)
    assert [c.name for c in calls] == ["api_spotify_transfer", "os_set_volume"]


@pytest.mark.parametrize("text", [
    "", "Sure! I will set the volume to 60.", "os_set_volume(level=60) and then I will rest",   # prose is never guessed at
    "delete_everything(path='C:/')",                 # not a tool we have
    "os_set_volume(level=__import__('os').system('x'))",  # not a plain value: never evaluated
    "os_set_volume(60)",                             # positional arguments are not guessed
])
def test_anything_else_is_left_alone(text):
    assert parse_text_calls(text, NAMES) == []


def test_a_garbled_tool_name_is_corrected_when_only_one_real_name_is_close():
    names = NAMES | {"browser_page_info", "browser_open"}
    calls = parse_text_calls("bralogy_page_info()", names)
    assert [c.name for c in calls] == ["browser_page_info"]


def test_a_garbled_name_is_not_guessed_when_it_is_ambiguous_or_far():
    assert parse_text_calls("os_set_volumes_x(level=1)", {"os_set_volume", "os_set_volumes_a"}) == []
    assert parse_text_calls("zzz(level=1)", NAMES) == []

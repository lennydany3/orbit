"""Time and date questions are answered from the PC clock by a rule: the model only ever guessed (and was then refused)."""
from datetime import datetime

import pytest

from orbit.agent import rules
from orbit.tools.base import Registry
from orbit.tools.os_layer import TimeParams, describe_now, get_time, register_os_tools
from orbit.voice.phrasing import Phraser


@pytest.mark.parametrize("text, part", [
    ("What's the time now?", "time"), ("what time is it", "time"), ("what is the time right now", "time"),
    ("tell me the time", "time"), ("can you tell me the time please", "time"), ("time", "time"),
    ("what's the time and date", "both"), ("date and time", "both"),
    ("what's the date", "date"), ("what day is it", "date"), ("what's today's date", "date"),
    ("what is the date today", "date"), ("what date is it", "date"), ("what day is it today", "date"),
])
def test_clock_questions_use_the_clock_tool(text, part):
    m = rules.match(text)
    assert m is not None and (m.tool, m.args) == ("os_get_time", {"part": part})


@pytest.mark.parametrize("text", ["what time does the movie start", "what is the time complexity", "set a timer", "play time", "what day does it ship"])
def test_other_sentences_with_time_in_them_are_left_alone(text):
    m = rules.match(text)
    assert m is None or m.tool != "os_get_time"


def test_the_clock_tool_is_registered_and_read_only():
    reg = Registry()
    register_os_tools(reg)
    tool = reg.get_exposed("os_get_time")
    assert tool is not None and tool.risk == "low"


@pytest.mark.parametrize("hour, minute, spoken", [(0, 5, "12:05 AM"), (9, 30, "9:30 AM"), (12, 0, "12:00 PM"), (15, 42, "3:42 PM"), (23, 59, "11:59 PM")])
def test_time_is_said_the_way_people_say_it(hour, minute, spoken):
    assert describe_now(datetime(2026, 11, 19, hour, minute)) == f"It's {spoken}."


def test_date_and_both():
    now = datetime(2026, 11, 5, 8, 7)
    assert describe_now(now, "date") == "Today is Thursday, November 5, 2026."
    assert describe_now(now, "both") == "It's 8:07 AM on Thursday, November 5, 2026."


def test_the_tool_reads_the_real_clock():
    result = get_time(TimeParams())
    assert result.ok and result.summary.startswith("It's ")


def test_it_is_spoken_as_is():
    assert Phraser().say("It's 3:42 PM.", "what's the time") == "It's 3:42 PM."
    assert Phraser().say("Today is Thursday, November 19, 2026.", "what's the date") == "Today is Thursday, November 19, 2026."

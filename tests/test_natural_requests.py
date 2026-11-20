"""People do not talk like a command line. "Uh, can you open Brave?" and "Show brave" once fell through to the chat voice, which
answered "I can't launch programs from here". These tests pin down how real, casual phrasings are understood:

  * the fast rules see through filler, polite frames and Orbit's own name,
  * "show / bring up / pull up <app>" brings an app to the front (or opens it),
  * a request is never treated as chat just because the last few turns were chat,
  * a polite request phrased as a question is still a request.
"""
import pytest
from conftest import ScriptedLLM

from orbit.agent import friend, rules
from orbit.llm.base import LLMResponse


def planned(text, **context):
    m = rules.match(text, **context)
    return (m.tool, m.args) if m else None


# ------------------------------------------------------------------ the phrases that went wrong
@pytest.mark.parametrize("text", ["Uh, can you open Brave?", "uh can you open brave", "Um, could you open Brave", "Umm... open Brave please",
                                  "Uhh can you please open Brave for me?"])
def test_spoken_filler_before_a_request_is_ignored(text):
    assert planned(text) == ("os_open_app", {"name": "Brave"})


@pytest.mark.parametrize("text", ["Show brave", "show Brave.", "Show the brave", "show my brave", "Uh, show brave", "can you show brave",
                                  "bring up brave", "pull up Brave", "bring up the brave window", "pull up brave please"])
def test_show_or_bring_up_an_app_brings_it_to_the_front(text):
    assert planned(text) == ("os_open_app", {"name": "Brave"})       # open_app focuses an app that is already open


def test_showing_a_website_opens_the_website_not_an_app():
    assert planned("show youtube") == ("browser_open", {"url": "https://www.youtube.com"})
    assert planned("bring up github")[0] == "browser_open"


@pytest.mark.parametrize("text", [
    "show me the time", "what time is it", "show tabs", "show windows", "show the volume", "show devices", "show me my tabs",
    "show me a joke", "show me", "show", "show it", "show that", "show everything", "bring up the volume",
])
def test_show_does_not_hijack_questions_and_lists(text):
    m = rules.match(text)
    assert m is None or m.tool != "os_open_app", (text, m)


# ------------------------------------------------------------------ polite frames, vocative and "I want to"
@pytest.mark.parametrize("text", [
    "Orbit, open Brave", "orbit open brave", "hey orbit can you open brave", "Hey Orbit, could you please open Brave?",
    "I want to open Brave", "I want you to open Brave", "I need you to open brave", "I'd like you to open Brave", "I would like to open brave",
    "go ahead and open brave", "open up Brave", "Open Brave for me please", "yo can you open brave real quick",
    "can u open brave", "would you mind opening Brave", "do you mind opening brave", "could you possibly open brave",
    "just open brave", "okay so now open brave",
])
def test_polite_and_casual_ways_to_ask_for_an_app(text):
    assert planned(text) == ("os_open_app", {"name": "Brave"}), text


@pytest.mark.parametrize("text, tool, args", [
    ("Uh, can you close Spotify?", "os_close_window", {"title": "Spotify"}),
    ("could you please minimize brave", "os_arrange_window", {"title": "Brave", "action": "minimize"}),
    ("I want you to maximize spotify", "os_arrange_window", {"title": "Spotify", "action": "maximize"}),
    ("do you mind closing spotify", "os_close_window", {"title": "Spotify"}),
    ("hey orbit what time is it", "os_get_time", {"part": "time"}),
    ("Orbit, volume 30", "os_set_volume", {"level": 30}),
    ("can you pause the music please", "os_media_key", {"key": "pause"}),
    ("uh, mute", "os_set_mute", {"muted": True}),
])
def test_other_commands_survive_the_same_framing(text, tool, args):
    assert planned(text) == (tool, args)


def test_two_requests_with_filler_are_still_two_requests():
    assert rules.split_commands("Uh, can you open Brave and then play some lofi on youtube") == ["open Brave", "play some lofi on youtube"]


# ------------------------------------------------------------------ "my primary window" is a monitor, not a Spotify device
@pytest.mark.parametrize("text, monitor", [
    ("Move Spotify to my primary window.", "primary"), ("move spotify to the primary window", "primary"),
    ("send brave to my secondary window", "secondary"), ("put chrome on the other window", "other"),
])
def test_a_monitor_called_a_window_is_still_a_monitor(text, monitor):
    tool, args = planned(text)
    assert tool == "os_move_window" and args["monitor"] == monitor, (text, tool, args)


def test_real_spotify_devices_are_still_devices():
    assert planned("move the music to echo pop")[0] == "api_spotify_transfer"
    assert planned("switch playback to the speaker")[0] == "api_spotify_transfer"


# ------------------------------------------------------------------ chat or command?
@pytest.mark.parametrize("text", [
    "Uh, can you open Brave?", "show brave", "can you minimize spotify", "could you switch to chrome", "I want you to close notepad",
    "uh can you play some music", "bring up the calculator", "pull up my downloads folder", "go to the primary monitor",
])
def test_a_request_is_never_chat_even_right_after_chatting(agent, text):
    assert agent._wants_chat(text, was_chatting=True) is False, text


@pytest.mark.parametrize("text", ["and then?", "really?", "why though", "haha nice", "lol", "no way", "what happened next"])
def test_a_vague_follow_up_right_after_chatting_is_still_chat(agent, text):
    assert agent._wants_chat(text, was_chatting=True) is True, text


def test_a_pc_word_in_a_follow_up_is_not_assumed_to_be_chat(agent):
    # "was_chatting" must not swallow a sentence about the PC: the classifier decides this one.
    agent.llm = ScriptedLLM(LLMResponse(content="ACTION"))
    assert agent._wants_chat("and what about my volume", was_chatting=True) is False


def test_the_chat_voice_never_claims_it_cannot_operate_the_pc():
    p = friend.persona().lower()
    assert "windows" in p, "it knows which computer this is, so it never explains macOS shortcuts"
    assert "never say you can't" in p or "never tell them you can't" in p


@pytest.fixture
def agent(make_agent):
    return make_agent(ScriptedLLM())


# ------------------------------------------------------------------ more ways people say the same thing
@pytest.mark.parametrize("text", ["open brave bro", "open Brave dude", "Open Brave, buddy", "open brave orbit", "open brave mate please"])
def test_a_friendly_word_on_the_end_is_not_part_of_the_name(text):
    assert planned(text) == ("os_open_app", {"name": "Brave"})


@pytest.mark.parametrize("text", ["i wanna open brave", "I gotta open Brave", "I have to open brave", "we need to open brave"])
def test_i_wanna_and_i_gotta(text):
    assert planned(text) == ("os_open_app", {"name": "Brave"})


@pytest.mark.parametrize("text", ["bring brave to the front", "put Spotify in the foreground", "get brave on top", "bring the brave window to the front"])
def test_bring_an_app_to_the_front(text):
    assert planned(text)[0] == "os_open_app"


def test_take_me_to_goes_to_a_site_or_an_app():
    assert planned("take me to github") == ("browser_open", {"url": "https://github.com"})
    assert planned("take me to brave") == ("os_open_app", {"name": "Brave"})
    assert planned("Uh, take me to youtube please")[0] == "browser_open"


def test_moving_a_window_to_a_monitor_is_not_bringing_it_to_the_front():
    assert planned("move brave to my primary monitor")[0] == "os_move_window"
    assert planned("bring brave to the primary monitor")[0] == "os_move_window"


@pytest.mark.parametrize("text", ["take me to github", "how loud is it", "how quiet is it", "is the sound on", "uh show spotify"])
def test_these_are_about_the_pc_even_right_after_chatting(agent, text):
    agent.llm = ScriptedLLM(LLMResponse(content="ACTION"))
    assert agent._wants_chat(text, was_chatting=True) is False, text


# ------------------------------------------------------------------ your scenario, end to end
def test_a_command_right_after_chatting_runs_the_tool_and_never_reaches_the_chat_voice(make_agent):
    chat = ScriptedLLM(LLMResponse(content="[happy] Hey! What's on your mind?"), LLMResponse(content="[neutral] SHOULD NEVER BE SAID"))
    agent = make_agent(ScriptedLLM(LLMResponse(content="CHAT")), chat_llm=chat, friend_mode=True)
    assert agent.handle("yo what's up").startswith("Hey!")
    assert agent.last_kind == "chat"

    reply = agent.handle("Uh, could you echo hi please?")          # filler, a polite frame, and "please", straight after chatting
    assert "echoed 'hi'" in reply
    assert agent.last_kind == "action"
    assert len(chat.calls) == 1, "the chat voice was not asked a second time"

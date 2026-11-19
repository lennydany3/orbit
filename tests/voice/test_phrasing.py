"""Natural spoken phrasing of the agent's tool reports. Includes the real replies from a live voice session."""
import random

import pytest

from orbit.voice.phrasing import FILLERS_FIRST, FILLERS_LONG, OKAYS, Phraser, clean_title, site_name
from orbit.voice.summarize import spoken_summary


def say(reply, request="", **kw):
    return Phraser(openers=("Okay, ",), **kw).say(reply, request)


@pytest.mark.parametrize("reply, expected", [
    # --- these are real replies from a live session ---
    ("Spotify was already open; brought it to the front", "Spotify was already open, so I brought it to the front."),
    ("opened Spotify", "Okay, I opened Spotify."),
    ("asked 'Spotify Premium' to close", "Okay, closing Spotify."),
    ("moved 'Spotify Premium' to monitor 1 (primary)", "Okay, I moved Spotify to your primary monitor."),
    ("clicked 'Amazon' in 'Spotify Premium' (clicked the words on screen)", "Okay, I clicked Amazon."),
    ("clicked 'Play Closer by The Chainsmokers, Halsey' in 'Spotify Premium' (pressed its control)",
     "Okay, I clicked Play Closer by The Chainsmokers, Halsey."),
    ("clicked '\U0001F534PLAYING GTA 6 EARLY ACCESS GAMEPLAY GET IN HERE!' (link) in your Brave",
     "Okay, I clicked PLAYING GTA 6 EARLY ACCESS GAMEPLAY GET IN HERE!."),
    ("opened https://www.youtube.com in your Brave (Personal profile)", "Okay, opening YouTube."),
    ("Volume set to 50%.", "Volume set to 50%."),                                   # the model's own sentence stays
    ("set volume to 50%", "Okay, volume is 50 percent."),
    ("volume is 80%, not muted", "The volume is 80 percent."),
    ("volume is 80%, muted", "The volume is 80 percent, and it's muted."),
    ("nothing is playing on Spotify", "Nothing is playing on Spotify right now."),
    ("playing: 'Closer' by The Chainsmokers on ATLAS", "Right now it's playing Closer by The Chainsmokers."),
    ("paused: 'Closer' by The Chainsmokers on ATLAS", "Closer by The Chainsmokers is paused."),
    ("3 tab(s) open in your Brave", "You have 3 tabs open."),
    ("closed 1 tab(s) in your Brave", "Okay, closed 1 tab."),
    ("It seems nothing is currently playing on Spotify. Would you like to play something?",
     "It seems nothing is currently playing on Spotify. Would you like to play something?"),
])
def test_tool_reports_become_natural_sentences(reply, expected):
    assert say(reply) == expected


def test_failures_sound_human():
    assert say("Failed: could not complete and verify the action on any layer (os:os_open_app). Last error: app 'Your Door' not found") \
        == "Sorry, I couldn't find an app called Your Door."
    assert say("Failed: the user declined 'fake_danger', so it was not run.") == "Okay, I won't do that."
    out = say("Failed: could not complete the action. Last error: no Spotify device matches 'Echo' (os:os_x)")
    assert out.startswith("Sorry, that didn't work.") and "os:" not in out and "(" not in out


def test_when_it_did_not_understand_it_says_what_it_heard_and_asks_again():
    reply = "Nothing was done: I did not run any tool for that, so I could not tell what to do. Try different words."
    assert say(reply, "Hey, everybody.") == 'Sorry, I didn\'t catch what to do with "Hey, everybody". Could you say that another way?'
    assert say(reply, "") == "Sorry, I didn't understand that. Could you say it another way?"


def test_several_actions_get_one_opener_not_one_each():
    assert say("opened Spotify; playing 'lofi beats' on ATLAS") == "Okay, I opened Spotify. Playing lofi beats."


def test_the_opener_varies_but_never_repeats_back_to_back():
    p = Phraser(rng=random.Random(3))
    openers = []
    for _ in range(12):
        out = p.say("asked 'Notepad' to close")
        openers.append(next(o for o in OKAYS if out.startswith(o.strip()[:4])))
    assert len(set(openers)) > 1
    assert all(a != b for a, b in zip(openers, openers[1:]))


def test_everything_is_still_cut_to_two_short_sentences_afterwards():
    spoken = spoken_summary(say("opened Spotify; playing 'x' on ATLAS; moved playback to Echo Pop; set volume to 30%"))
    assert spoken.count(".") <= 3 and "on screen" in spoken


def test_clean_titles_and_sites():
    assert clean_title("(204) YouTube - Brave") == "YouTube"
    assert clean_title("Spotify Premium") == "Spotify"
    assert clean_title("\U0001F534 Live now!! " + "word " * 30).endswith("word") and len(clean_title("word " * 40)) <= 60
    assert site_name("https://www.github.com/lennydany3") == "GitHub" and site_name("https://example.org/x") == "example.org"


def test_fillers_come_from_the_lists():
    p = Phraser(rng=random.Random(0))
    assert p.filler(0) in FILLERS_FIRST and p.filler(1) in FILLERS_LONG
    assert all(len(f) < 40 for f in FILLERS_FIRST + FILLERS_LONG)              # short enough to finish before the answer is ready


def test_empty_stays_empty():
    assert say("") == "" and say("   ") == ""


@pytest.mark.parametrize("reply, expected", [
    ("Failed: x (os:os_close_window). Last error: 'Task Manager' runs as administrator, so Windows does not let Orbit close it. Start Orbit ...",
     "Sorry, Task Manager runs as administrator, so Windows won't let me close it. If you start Orbit as administrator, I can."),
    ("Failed: x. Last error: 'Task Manager' runs as administrator, so Windows does not let Orbit minimize it. Start Orbit ...",
     "Sorry, Task Manager runs as administrator, so Windows won't let me minimize it. If you start Orbit as administrator, I can."),
    ("asked 'ChatGPT' to close, but chatgpt is still running in the background (the system tray). Say 'quit ChatGPT' to exit it completely",
     "I closed ChatGPT, but it's still running in the background. Say quit ChatGPT to close it completely."),
    ("quit ChatGPT completely (12 process(es) ended)", "Okay, I quit ChatGPT completely."),
    ("minimize: 'Japanese Music - Japanese Music'", "Okay, Japanese Music - Japanese Music is minimized."),
    ("left: 'Notepad'", "Okay, Notepad is snapped to the left."),
    ("pause on ATLAS", "Okay, paused."),
    ("next on ATLAS", "Okay, skipping to the next song."),
    ("I couldn't reach the model: Ollama error 500: llama-server process no longer running: exit status 0xc0000409",
     "Sorry, my language model isn't responding right now. Simple commands like open Spotify still work."),
])
def test_closing_quitting_and_error_messages_sound_human(reply, expected):
    assert say(reply) == expected


def test_not_found_failures_say_what_could_not_be_found():
    assert say("Failed: could not complete and verify the action on any layer (os:os_close_window). Last error: no open window matches "
               "'Tragedy'. Open windows include: 'Task Manager', 'ChatGPT'. If one of these is what the user meant, call again.")         == "Sorry, I couldn't find a window called Tragedy."
    assert say("Failed: x. Last error: nothing running matches 'chatgpt'") == "Sorry, I don't see chatgpt running."

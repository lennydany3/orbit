"""Ending a conversation by voice: "nothing", "leave it", "let's talk later", "bye"..."""
import random

import pytest

from orbit.voice import farewell
from orbit.voice.hotkey import KeyEvent
from orbit.voice.loop import EchoAgent
from orbit.voice.state import State
from tests.voice.test_loop import build, moves, user_holds_key_and_talks


# ---------------------------------------------------------------- the detector (pure)
@pytest.mark.parametrize("text, kind", [
    ("nothing", "nothing"), ("Nothing.", "nothing"), ("nothing, thanks", "nothing"), ("nothing else", "nothing"),
    ("leave it", "nothing"), ("Leave it for now.", "nothing"), ("okay leave it", "nothing"), ("never mind", "nothing"), ("nevermind", "nothing"),
    ("forget it", "nothing"), ("that's all", "nothing"), ("that is all", "nothing"), ("that's all, thanks", "nothing"), ("that will be all", "nothing"),
    ("no thanks", "nothing"), ("no thank you", "nothing"), ("I'm good", "nothing"), ("I'm done", "nothing"), ("that's enough", "nothing"),
    ("let's talk later", "later"), ("Lets talk later.", "later"), ("talk to you later", "later"), ("see you later", "later"), ("catch you later", "later"),
    ("not now", "later"), ("maybe later", "later"), ("we can talk tomorrow", "later"), ("later", "later"),
    ("bye", "bye"), ("ok bye", "bye"), ("bye orbit", "bye"), ("goodbye", "bye"), ("thanks bye", "bye"), ("good night", "bye"), ("I have to go", "bye"),
    ("stop listening", "stop"), ("stop listening now", "stop"), ("go to sleep", "stop"), ("be quiet", "stop"), ("end the conversation", "stop"),
    ("hey orbit nothing", "nothing"),
])
def test_a_goodbye_is_recognised(text, kind):
    found = farewell.detect(text)
    assert found is not None and found.kind == kind


@pytest.mark.parametrize("text", [
    "nothing is playing", "do nothing", "what is nothing", "play something", "open spotify", "leave it open", "later in the video play it",
    "stop", "stop the music", "quiet", "no", "volume 30", "bye the way open chrome", "that is all wrong open chrome", "i need nothing from the library please open",
    "", "   ", "uh",
])
def test_ordinary_requests_are_never_taken_for_a_goodbye(text):
    assert farewell.detect(text) is None


def test_a_long_sentence_is_never_a_goodbye():
    assert farewell.detect("that's all I wanted to say but also please open the browser and search for cats") is None


@pytest.mark.parametrize("text", ["कुछ नहीं", "rehne do", "kuch nahi", "baad mein baat karte hain",
                                  "ஒண்ணும் இல்ல", "appuram pesalam"])
def test_hindi_and_tamil_goodbyes_work_too(text):
    assert farewell.detect(text) is not None


def test_the_goodbye_fits_what_was_said():
    rng = random.Random(1)
    assert farewell.reply(farewell.detect("good night"), rng) in ("Good night!", "Sleep well. Good night!")
    assert farewell.reply(farewell.detect("lets talk later"), rng) in farewell._REPLIES["later"]
    assert farewell.reply(farewell.detect("stop listening"), rng) in farewell._REPLIES["stop"]
    assert farewell.reply(farewell.detect("nothing"), rng) in farewell._REPLIES["nothing"]
    assert farewell.reply(farewell.detect("bye"), rng) in farewell._REPLIES["bye"]


def test_every_goodbye_is_short_enough_to_be_said_in_a_breath():
    for replies in farewell._REPLIES.values():
        assert all(len(line) <= 80 for line in replies)


# ---------------------------------------------------------------- in the voice loop
class RecordingAgent(EchoAgent):
    def __init__(self):
        super().__init__()
        self.ended = 0

    def end_conversation(self):
        self.ended += 1


def test_after_the_wake_word_nothing_ends_the_conversation_without_the_agent():
    sim, loop, keys, tts, wake, out = build("Hey Orbit, nothing.", with_wake=True, agent=RecordingAgent())
    sim.when(lambda: sim.reads > 5, lambda: setattr(wake, "armed", True))
    sim.when(lambda: loop.st.is_(State.LISTENING) and sim.talk == 0 and not loop.heard, lambda: setattr(sim, "talk", 15))
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    assert loop.run() == "quit"
    assert loop.agent.calls == [] and loop.agent.ended == 1                      # the model never saw it
    assert len(tts.spoken) == 1 and tts.spoken[0] in farewell._REPLIES["nothing"]
    assert moves(loop)[-1] == ("speaking", "idle")
    assert ("speaking", "follow_up") not in moves(loop)                          # no follow-up window after a goodbye


def test_in_the_follow_up_window_leave_it_closes_the_conversation():
    sim, loop, keys, tts, _, out = build("volume thirty", "leave it", agent=RecordingAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.FOLLOW_UP) and sim.talk == 0 and not loop.heard and loop.turns == 1, lambda: setattr(sim, "talk", 12))
    sim.stop_when = lambda: loop.turns == 2 and loop.st.is_(State.IDLE)
    loop.run()
    assert loop.agent.calls == ["volume thirty"]                                 # only the first request ran
    assert tts.spoken[-1] in farewell._REPLIES["nothing"]
    assert moves(loop)[-1] == ("speaking", "idle") and sum(1 for m in moves(loop) if m == ("speaking", "follow_up")) == 1
    assert any("you ended the conversation" in line for line in out)


def test_a_bare_bye_is_a_goodbye_not_noise():
    """"bye" on its own used to be thrown away as something Whisper invents for silence."""
    sim, loop, keys, tts, _, out = build("bye", agent=RecordingAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert loop.agent.calls == [] and tts.spoken and tts.spoken[0] in farewell._REPLIES["bye"]


def test_the_next_conversation_after_a_goodbye_works_normally():
    sim, loop, keys, tts, _, _ = build("lets talk later", "open spotify", agent=RecordingAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.IDLE) and loop.turns == 1 and sim.reads > 40 and sim.talk == 0,
             lambda: (keys.events.append(KeyEvent("down")), setattr(sim, "talk", 15)))
    sim.when(lambda: loop.mode == "ptt" and loop.st.is_(State.LISTENING) and sim.talk == 0 and loop.turns == 1 and sim.reads > 45,
             lambda: keys.events.append(KeyEvent("up", 1.3, True)))
    sim.stop_when = lambda: loop.turns == 2 and loop.st.is_(State.FOLLOW_UP)
    loop.run()
    assert loop.agent.calls == ["open spotify"] and not loop._closing

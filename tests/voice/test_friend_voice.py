"""Voice loop in friend mode: chat replies are spoken (almost) whole with their feeling; commands keep the short style."""
from test_loop import build, moves, user_holds_key_and_talks

from orbit.voice import config
from orbit.voice.state import State

LONG_CHAT = ("Oof, that sounds rough. Three hours on one bug is a marathon. You should be proud you stuck with it. "
             "Want to tell me what was tripping you up?")


class ChatAgent:
    safety = type("S", (), {"confirm": None})()

    def __init__(self, kind="chat", emotion="caring", reply=LONG_CHAT):
        self.kind, self.emotion, self.reply, self.last_kind, self.last_emotion = kind, emotion, reply, "action", "neutral"

    def handle(self, text):
        self.last_kind, self.last_emotion = self.kind, self.emotion
        return self.reply


def run_one_turn(agent):
    sim, loop, keys, tts, _, out = build("I had a long day", agent=agent)
    emotions = []
    tts.set_emotion = emotions.append
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.FOLLOW_UP)
    loop.run()
    return loop, tts, emotions, out


def test_a_chat_reply_is_spoken_whole_with_its_feeling_and_a_longer_follow_up_window():
    loop, tts, emotions, out = run_one_turn(ChatAgent())
    assert tts.spoken == [LONG_CHAT]                                 # not cut to two sentences, not rewritten
    assert emotions == ["caring"] and any("[feeling] caring" in line for line in out)
    assert loop._follow_window == config.FOLLOW_UP_CHAT_S


def test_a_command_reply_keeps_the_short_neutral_style():
    loop, tts, emotions, _ = run_one_turn(ChatAgent(kind="action", reply="Opened Spotify. Volume is 30 percent. Playing lofi. On the Echo."))
    assert tts.spoken == ["Opened Spotify. Volume is 30 percent. The rest is on screen."]
    assert emotions == ["neutral"] and loop._follow_window == config.FOLLOW_UP_S


def test_emoji_in_a_chat_reply_are_not_spoken():
    loop, tts, _, out = run_one_turn(ChatAgent(reply="Nice one \U0001F389 you did it!"))
    assert tts.spoken == ["Nice one you did it!"] and any("\U0001F389" in line for line in out)

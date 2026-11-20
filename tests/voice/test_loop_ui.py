"""The voice loop talking to a face: what a connected sphere window is told during real turns, in the same
simulated world as test_loop.py (fake microphone, fake clock, fake voice)."""
import numpy as np

from orbit.ipc import protocol
from orbit.ipc.bridge import UiBridge
from orbit.voice import config
from orbit.voice.state import State

from test_loop import RiskyAgent, build, moves, user_holds_key_and_talks


class Face:
    """A UiBridge whose messages are collected, on the simulation's clock."""

    def __init__(self, sim):
        self.sent: list[dict] = []
        self.bridge = UiBridge(self.sent.append, clock=sim.clock)

    def states(self):
        return [m["state"] for m in self.sent if "state" in m and "type" not in m]

    def of_type(self, kind):
        return [m for m in self.sent if m.get("type") == kind]


def build_with_face(*texts, agent=None, **kwargs):
    # build() makes the simulated world itself, so the face is made afterwards on its clock and attached.
    sim, loop, keys, tts, wake, out = build(*texts, agent=agent, **kwargs)
    face = Face(sim)
    loop.ui = face.bridge
    return sim, loop, keys, tts, wake, out, face


def test_a_turn_is_told_to_the_face_state_by_state_with_what_was_said_and_the_reply():
    sim, loop, keys, tts, _, _, face = build_with_face("open spotify")
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert face.states() == ["listening", "transcribing", "thinking", "success", "speaking", "follow_up", "idle"]
    assert face.of_type("transcript") == [{"type": "transcript", "who": "you", "text": "open spotify"}]
    assert face.of_type("reply") == [{"type": "reply", "text": "Okay, I would do this: open spotify",
                                      "spoken": "Okay, I would do this: open spotify.", "kind": "action", "emotion": "neutral"}]
    # the flash and the reply come before the speaking state they introduce
    order = [("state", m["state"]) if "state" in m and "type" not in m else (m["type"],) for m in face.sent if "level" not in m]
    assert order.index(("state", "success")) < order.index(("reply",)) < order.index(("state", "speaking"))
    assert order.index(("transcript",)) < order.index(("state", "thinking"))


def test_the_follow_up_window_length_is_sent_with_the_state():
    sim, loop, keys, tts, _, _, face = build_with_face("volume thirty")
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    follow = [m for m in face.sent if m.get("state") == "follow_up"]
    assert follow and follow[0]["seconds"] == config.FOLLOW_UP_S


def test_your_voice_level_is_sent_while_you_talk_and_not_before_or_after():
    sim, loop, keys, tts, _, _, face = build_with_face("open spotify")
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    kinds = [("state", m["state"]) if "state" in m else ("level", m["level"]) for m in face.sent if "type" not in m]
    start, end = kinds.index(("state", "listening")), kinds.index(("state", "transcribing"))
    heard = [v for k, v in kinds[start:end] if k == "level"]
    assert heard and max(heard) > 0.9, "the loud frames the user talked with"
    before = [k for k in kinds[:start] if k[0] == "level"]
    assert before == [], "nothing is shown while only the wake word listens"
    thinking = kinds.index(("state", "thinking"))
    assert [v for k, v in kinds[start:thinking] if k == "level"][-1] == 0.0, "back to zero by the time it stops listening"


def test_what_orbit_says_out_loud_is_metered_through_the_voice():
    sim, loop, keys, tts, _, _, face = build_with_face("open spotify")
    played = []
    original = tts.speak

    def speak(text):                                    # a voice that reports each block it plays, like the real one
        for _ in range(4):
            loop.ui.audio_block(np.full(1280, 5000, np.int16))
            sim.t += 0.06
        played.append(text)
        return original(text)

    tts.speak = speak
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    speaking = face.states().index("speaking")
    after = [m for m in face.sent[face.sent.index({"state": "speaking", "reason": "reply ready"}):] if "level" in m]
    assert played and after and after[0]["level"] > 0.6 and speaking > 0


def test_a_friend_chat_reply_carries_its_feeling():
    class Friend:
        safety = type("S", (), {"confirm": None})()
        last_kind, last_emotion = "chat", "proud"

        def handle(self, text):
            return "That is great, I am proud of you!"

    sim, loop, keys, tts, _, _, face = build_with_face("I finished my project", agent=Friend())
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert face.of_type("reply")[0]["kind"] == "chat" and face.of_type("reply")[0]["emotion"] == "proud"
    assert "success" not in face.states(), "chatting is not an action that can succeed"


def test_a_failed_action_flashes_error_not_success():
    class Failing:
        safety = type("S", (), {"confirm": None})()
        last_failed = True

        def handle(self, text):
            return "Failed: no window called that."

    sim, loop, keys, tts, _, _, face = build_with_face("close the notes", agent=Failing())
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    states = face.states()
    assert "error" in states and "success" not in states
    assert states.index("error") == states.index("speaking") - 1


def test_a_crashed_agent_flashes_error():
    class Broken:
        safety = type("S", (), {"confirm": None})()

        def handle(self, text):
            raise RuntimeError("tool exploded")

    sim, loop, keys, tts, _, _, face = build_with_face("open spotify", agent=Broken())
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert face.states().count("error") == 1 and "success" not in face.states()


def test_the_kill_switch_shows_muted():
    from orbit.voice.hotkey import KeyEvent

    sim, loop, keys, tts, _, _, face = build_with_face("open spotify")
    sim.when(lambda: sim.reads > 3, lambda: keys.events.append(KeyEvent("kill")))
    assert loop.run() == "killed"
    assert face.sent[-1] == {"state": "muted", "reason": "kill switch"}


# ------------------------------------------------------------------ the risky-action question on screen
def asked(face):
    return face.of_type("confirm")


def test_a_risky_action_is_shown_on_screen_and_a_click_answers_it():
    sim, loop, keys, tts, _, out, face = build_with_face("close notepad", agent=RiskyAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: asked(face), lambda: face.bridge.on_command(protocol.ConfirmAnswer(asked(face)[0]["id"], True)))
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    question = asked(face)[0]
    assert question["action"] == "close window Notepad" and "risky action" in question["question"]
    assert tts.spoken[-1] == "Closed Notepad.", "yes on screen runs the action"
    assert face.of_type("confirm_done") == [{"type": "confirm_done", "id": question["id"], "answer": True, "by": "ui"}]
    assert ("thinking", "confirming") in moves(loop) and ("confirming", "thinking") in moves(loop)
    assert any("answered on screen" in line for line in out)


def test_no_on_screen_leaves_the_action_alone():
    sim, loop, keys, tts, _, _, face = build_with_face("close notepad", agent=RiskyAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: asked(face), lambda: face.bridge.on_command(protocol.ConfirmAnswer(asked(face)[0]["id"], False)))
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken[-1] == "Okay, I left Notepad open."
    assert face.of_type("confirm_done")[0]["by"] == "ui" and face.of_type("confirm_done")[0]["answer"] is False


def test_answering_by_voice_closes_the_question_on_screen_too():
    sim, loop, keys, tts, _, _, face = build_with_face("close notepad", "yes please", agent=RiskyAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop._confirm_step == "listen" and sim.talk == 0 and not loop.heard, lambda: setattr(sim, "talk", 12))
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    done = face.of_type("confirm_done")
    assert len(done) == 1 and done[0]["by"] == "voice" and done[0]["answer"] is True
    assert face.bridge.on_command(protocol.ConfirmAnswer(done[0]["id"], False)) == "no such confirmation is open"


def test_silence_is_a_no_on_screen_as_well():
    sim, loop, keys, tts, _, _, face = build_with_face("close notepad", agent=RiskyAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert face.of_type("confirm_done")[0]["by"] == "timeout" and face.of_type("confirm_done")[0]["answer"] is False
    assert sim.t > config.CONFIRM_TIMEOUT_S


def test_an_answer_for_another_question_is_ignored_by_the_loop():
    sim, loop, keys, tts, _, _, face = build_with_face("close notepad", "no", agent=RiskyAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: asked(face), lambda: face.bridge.on_command(protocol.ConfirmAnswer(asked(face)[0]["id"] + 5, True)))
    sim.when(lambda: loop._confirm_step == "listen" and sim.talk == 0 and not loop.heard, lambda: setattr(sim, "talk", 12))
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken[-1] == "Okay, I left Notepad open.", "the wrong id did nothing; the spoken no decided"
    assert face.of_type("confirm_done")[0]["by"] == "voice"


def test_the_kill_switch_settles_an_open_question_on_screen():
    from orbit.voice.hotkey import KeyEvent

    sim, loop, keys, tts, _, _, face = build_with_face("close notepad", agent=RiskyAgent())
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.CONFIRMING), lambda: keys.events.append(KeyEvent("kill")))
    assert loop.run() == "killed"
    assert face.of_type("confirm_done")[0]["by"] == "killed" and face.of_type("confirm_done")[0]["answer"] is False


def test_a_loop_with_no_face_behaves_exactly_as_before():
    sim, loop, keys, tts, _, _ = build("open spotify")
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert tts.spoken == ["Okay, I would do this: open spotify."]
    assert moves(loop)[0] == ("idle", "listening")

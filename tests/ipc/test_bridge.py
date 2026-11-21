"""The bridge: what the voice loop's calls become on the wire, how fast, and the one thing a face may answer."""
import numpy as np
import pytest

from orbit.ipc import protocol
from orbit.ipc.bridge import ORB_FOR, NullBridge, UiBridge
from orbit.voice.state import State

LOUD = np.full(1280, 6000, np.int16)
QUIET = np.zeros(1280, np.int16)


class Wire:
    def __init__(self):
        self.t = 100.0
        self.sent: list[dict] = []
        self.bridge = UiBridge(self.sent.append, clock=lambda: self.t, level_hz=20)

    def advance(self, s):
        self.t += s

    def go(self, new, reason="", seconds=None):
        self.bridge.state_changed(State.IDLE, new, reason, seconds)

    def levels(self):
        return [m["level"] for m in self.sent if "level" in m]

    def states(self):
        return [m["state"] for m in self.sent if "state" in m and "type" not in m]


def test_every_voice_state_has_a_sphere_state():
    assert set(ORB_FOR) == set(State)
    assert {s: ORB_FOR[s] for s in State} == {
        State.IDLE: "idle", State.WAKE_DETECTED: "wake", State.LISTENING: "listening", State.TRANSCRIBING: "transcribing",
        State.THINKING: "thinking", State.CONFIRMING: "confirming", State.SPEAKING: "speaking", State.FOLLOW_UP: "follow_up",
        State.ALARM: "speaking"}, "a ringing alarm is held as the speaking sphere: it pulses with the sound"


def test_a_state_change_is_sent_with_its_reason_and_the_follow_up_window():
    w = Wire()
    w.go(State.LISTENING, "hotkey")
    w.go(State.FOLLOW_UP, "listening 8s", seconds=8)
    w.go(State.IDLE, "finished", seconds=8)               # the window only means something for follow_up
    assert w.sent == [{"state": "listening", "reason": "hotkey"}, {"state": "follow_up", "reason": "listening 8s", "seconds": 8.0},
                      {"state": "idle", "reason": "finished"}]


def test_a_new_face_is_told_the_current_state_and_level():
    w = Wire()
    assert w.bridge.snapshot() == {"type": "hello", "version": 1, "state": "idle", "level": 0.0}
    w.go(State.LISTENING)
    w.bridge.mic_frame(LOUD)
    snap = w.bridge.snapshot()
    assert snap["state"] == "listening" and snap["level"] > 0.9


# ------------------------------------------------------------------ levels
def test_levels_are_sent_only_while_a_voice_is_being_heard():
    w = Wire()
    w.bridge.mic_frame(LOUD)                              # idle: the wake word is listening, nothing to show
    w.go(State.THINKING)
    w.advance(0.1)
    w.bridge.mic_frame(LOUD)
    w.bridge.audio_block(LOUD)
    assert w.levels() == []
    w.go(State.LISTENING)
    w.advance(0.1)
    w.bridge.audio_block(LOUD)                            # Orbit's speaker while you are the one talking: ignored
    assert w.levels() == []
    w.bridge.mic_frame(LOUD)
    assert w.levels() and w.levels()[-1] > 0.9
    w.go(State.SPEAKING)
    w.advance(0.1)
    w.bridge.mic_frame(LOUD)                              # the mic is off while Orbit speaks
    n = len(w.levels())
    w.bridge.audio_block(LOUD)
    assert len(w.levels()) == n + 1


def test_levels_are_limited_to_twenty_a_second_and_real_changes():
    w = Wire()
    w.go(State.LISTENING)
    for i in range(10):                                   # ten blocks inside 50 ms
        w.bridge.mic_frame(np.full(1280, 3000 + i * 400, np.int16))
        w.advance(0.005)
    assert len(w.levels()) == 1
    w.advance(0.1)
    w.bridge.mic_frame(np.full(1280, 3000, np.int16))
    w.advance(0.1)
    before = len(w.levels())
    w.bridge.mic_frame(np.full(1280, 3000, np.int16))     # the same loudness again: nothing new to say
    assert len(w.levels()) == before


def test_falling_silent_is_always_sent_and_sent_once():
    w = Wire()
    w.go(State.LISTENING)
    w.bridge.mic_frame(LOUD)
    w.bridge.mic_frame(QUIET)                             # straight away, inside the 50 ms gap
    assert w.levels()[-1] == 0.0
    n = len(w.levels())
    w.advance(0.2)
    w.bridge.mic_frame(QUIET)
    assert len(w.levels()) == n


def test_the_level_falls_to_zero_when_the_audio_stops_arriving():
    w = Wire()
    w.go(State.SPEAKING)
    w.bridge.audio_block(LOUD)
    w.advance(0.2)
    w.bridge.tick()
    assert w.levels()[-1] > 0.7, "a short gap between blocks is not silence"
    w.advance(0.2)
    w.bridge.tick()
    assert w.levels()[-1] == 0.0
    n = len(w.levels())
    w.advance(1)
    w.bridge.tick()
    assert len(w.levels()) == n


def test_leaving_a_voice_state_zeroes_the_level():
    w = Wire()
    w.go(State.LISTENING)
    w.bridge.mic_frame(LOUD)
    w.advance(0.1)
    w.go(State.TRANSCRIBING)
    assert w.sent[-2:] == [{"state": "transcribing"}, {"level": 0.0}]


# ------------------------------------------------------------------ outcomes and words
def test_success_error_and_kill_are_their_own_sphere_states():
    w = Wire()
    w.bridge.outcome(True)
    w.bridge.outcome(False)
    w.bridge.killed()
    assert w.sent == [{"state": "success"}, {"state": "error"}, {"state": "muted", "reason": "kill switch"}]


def test_what_was_said_and_the_reply_are_sent():
    w = Wire()
    w.bridge.transcript("you", "open spotify")
    w.bridge.reply("Opened Spotify.", "Opened Spotify.", "action", "neutral")
    assert w.sent == [{"type": "transcript", "who": "you", "text": "open spotify"},
                      {"type": "reply", "text": "Opened Spotify.", "spoken": "Opened Spotify.", "kind": "action", "emotion": "neutral"}]


# ------------------------------------------------------------------ confirmations
def test_a_face_can_answer_an_open_question_once():
    w = Wire()
    cid = w.bridge.confirm_asked("os_close_window(...) is a risky action. Allow?", "close window Notepad")
    assert w.sent[-1]["type"] == "confirm" and w.sent[-1]["id"] == cid and w.sent[-1]["action"] == "close window Notepad"
    assert w.bridge.poll_answer() is None
    assert w.bridge.on_command(protocol.ConfirmAnswer(cid, True)) is None
    assert w.bridge.poll_answer() == protocol.ConfirmAnswer(cid, True)
    assert w.bridge.on_command(protocol.ConfirmAnswer(cid, False)) == "no such confirmation is open"     # the first answer won
    assert w.bridge.poll_answer() is None


def test_an_answer_with_the_wrong_id_or_with_nothing_asked_is_refused():
    w = Wire()
    assert w.bridge.on_command(protocol.ConfirmAnswer(1, True)) == "no such confirmation is open"
    cid = w.bridge.confirm_asked("q", "a")
    assert w.bridge.on_command(protocol.ConfirmAnswer(cid + 1, True)) == "no such confirmation is open"
    assert w.bridge.poll_answer() is None


def test_once_the_question_is_settled_a_late_answer_is_refused():
    w = Wire()
    cid = w.bridge.confirm_asked("q", "a")
    w.bridge.confirm_done(cid, False, "voice")
    assert w.sent[-1] == {"type": "confirm_done", "id": cid, "answer": False, "by": "voice"}
    assert w.bridge.on_command(protocol.ConfirmAnswer(cid, True)) == "no such confirmation is open"


def test_an_old_answer_can_never_answer_a_new_question():
    w = Wire()
    first = w.bridge.confirm_asked("q1", "a1")
    w.bridge.on_command(protocol.ConfirmAnswer(first, True))      # accepted, but the loop never got to read it
    second = w.bridge.confirm_asked("q2", "a2")
    assert second == first + 1
    assert w.bridge.poll_answer() is None


# ------------------------------------------------------------------ never in the way
def test_a_broken_face_never_breaks_the_voice_loop():
    def boom(message):
        raise OSError("socket gone")

    bridge = UiBridge(boom)
    bridge.state_changed(State.IDLE, State.LISTENING, "x")
    bridge.mic_frame(LOUD)
    bridge.transcript("you", "hi")
    bridge.outcome(True)
    bridge.killed()
    bridge.confirm_done(1, True, "ui")


def test_the_null_bridge_accepts_every_call_and_never_has_an_answer():
    b = NullBridge()
    b.state_changed(State.IDLE, State.LISTENING, "x", None)
    b.mic_frame(LOUD)
    b.audio_block(LOUD)
    b.tick()
    b.transcript("you", "hi")
    b.reply("a", "b", "chat", "proud")
    b.outcome(True)
    b.confirm_done(0, True, "ui")
    b.killed()
    assert b.confirm_asked("q", "a") == 0 and b.poll_answer() is None


def test_a_play_request_waits_for_orbit_to_pick_it_up_and_too_many_are_refused():
    sent = []
    bridge = UiBridge(sent.append)
    assert bridge.poll_request() is None
    assert bridge.on_command(protocol.PlayRequest("p-1")) is None
    assert bridge.poll_request() == protocol.PlayRequest("p-1") and bridge.poll_request() is None
    results = [bridge.on_command(protocol.PlayRequest(f"p-{i}")) for i in range(12)]
    assert results[:8] == [None] * 8 and all("already working" in r for r in results[8:])


def test_a_reminder_is_published_and_a_dead_face_cannot_break_it():
    sent = []
    UiBridge(sent.append).reminder("r-1", "Call mom", "Reminder: Call mom.")
    assert sent == [{"type": "reminder", "id": "r-1", "title": "Call mom", "text": "Reminder: Call mom."}]

    def broken(message):
        raise OSError("the face went away")

    UiBridge(broken).reminder("r-1", "x", "y")                      # never raises into the voice loop


def test_the_null_bridge_has_the_new_calls_too():
    from orbit.ipc.bridge import NullBridge

    n = NullBridge()
    n.reminder("r", "t", "x")
    assert n.poll_request() is None


def test_a_stop_button_press_waits_for_the_loop_and_pressing_it_over_and_over_is_harmless():
    sent = []
    bridge = UiBridge(sent.append)
    assert bridge.poll_dismiss() is None
    assert bridge.on_command(protocol.DismissRequest("r-1")) is None
    assert bridge.poll_dismiss() == "r-1" and bridge.poll_dismiss() is None
    assert all(bridge.on_command(protocol.DismissRequest("r-1")) is None for _ in range(30)), "never an error, even when the queue is full"
    assert [bridge.poll_dismiss() for _ in range(9)].count(None) == 1, "and only 8 are kept"


def test_a_ringing_reminder_and_its_end_are_published_and_a_dead_face_cannot_break_them():
    sent = []
    bridge = UiBridge(sent.append)
    bridge.reminder("r-1", "Pasta", "Your timer is up.", "timer", True)
    bridge.alarm_stopped("r-1", "ui")
    assert sent == [{"type": "reminder", "id": "r-1", "title": "Pasta", "text": "Your timer is up.", "kind": "timer", "ringing": True},
                    {"type": "alarm_stopped", "id": "r-1", "by": "ui"}]

    def broken(message):
        raise OSError("the face went away")

    UiBridge(broken).reminder("r-1", "x", "y", "timer", True)
    UiBridge(broken).alarm_stopped("r-1", "voice")


def test_the_null_bridge_has_the_alarm_calls_too():
    n = NullBridge()
    n.reminder("r", "t", "x", "timer", True)
    n.alarm_stopped("r", "ui")
    assert n.poll_dismiss() is None


def test_a_ringing_alarm_shows_its_sound_as_the_spheres_level():
    sent = []
    t = [0.0]
    bridge = UiBridge(sent.append, clock=lambda: t[0])
    bridge.state_changed(State.IDLE, State.ALARM, "a reminder came due")
    assert sent[0] == {"state": "speaking", "reason": "a reminder came due"}
    bridge.audio_block(LOUD)
    assert any("level" in m and m["level"] > 0 for m in sent), "the sphere pulses with the bell"

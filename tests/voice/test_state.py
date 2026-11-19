"""Voice state machine: allowed transitions, timeouts and the per-turn timing line. No hardware."""
from datetime import datetime

import pytest

from orbit.voice.state import ALLOWED, IllegalTransition, State, TurnTimer, VoiceState


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def machine(logs=None):
    clock = Clock()
    return VoiceState(clock=clock, log=(logs.append if logs is not None else None), wall=lambda: datetime(2026, 11, 19, 12, 3, 44, 512000)), clock


def test_a_full_wake_word_turn():
    logs = []
    m, _ = machine(logs)
    for new in (State.WAKE_DETECTED, State.LISTENING, State.TRANSCRIBING, State.THINKING, State.SPEAKING, State.FOLLOW_UP, State.IDLE):
        m.to(new)
    assert m.state is State.IDLE and len(m.history) == 7
    assert logs[0] == "[12:03:44.512] idle -> wake_detected"          # every transition is logged with a timestamp


def test_hold_to_talk_skips_the_wake_step_and_a_risky_action_confirms_in_the_middle():
    m, _ = machine()
    m.to(State.LISTENING, "hotkey")
    for new in (State.TRANSCRIBING, State.THINKING, State.CONFIRMING, State.THINKING, State.SPEAKING):
        m.to(new)
    assert m.state is State.SPEAKING


def test_the_hotkey_can_interrupt_speech():
    m, _ = machine()
    for new in (State.LISTENING, State.TRANSCRIBING, State.THINKING, State.SPEAKING):
        m.to(new)
    m.to(State.LISTENING, "barge-in")
    assert m.state is State.LISTENING


@pytest.mark.parametrize("start, bad", [(State.IDLE, State.SPEAKING), (State.LISTENING, State.THINKING), (State.THINKING, State.LISTENING),
                                        (State.SPEAKING, State.THINKING), (State.CONFIRMING, State.SPEAKING)])
def test_illegal_transitions_are_refused(start, bad):
    m, _ = machine()
    m.state = start
    with pytest.raises(IllegalTransition):
        m.to(bad)
    assert m.state is start


def test_idle_is_always_reachable():
    for state in State:
        m, _ = machine()
        m.state = state
        m.to(State.IDLE, "kill switch")
        assert m.state is State.IDLE


def test_every_state_has_a_way_forward():
    assert set(ALLOWED) == set(State) and all(ALLOWED[s] for s in State)


def test_timeouts_use_time_in_the_current_state():
    m, clock = machine()
    m.to(State.LISTENING)
    clock.t += 3.4
    assert not m.timed_out(3.5)
    clock.t += 0.2
    assert m.timed_out(3.5)
    m.to(State.TRANSCRIBING)
    assert m.elapsed == 0 and not m.timed_out(3.5)       # the clock restarts at every transition


def test_turn_timer_summary_shows_where_the_time_went():
    t = TurnTimer(clock=lambda: 0.0)
    for name, at in (("speech_end", 10.0), ("text", 12.2), ("reply", 12.7), ("first_audio", 13.0)):
        t.mark(name, at)
    line = t.summary(end_silence_s=0.9)
    assert "transcribe 2.20s" in line and "agent 0.50s" in line and "first audio 0.30s" in line
    assert "= 3.00s from end of speech" in line and "+0.9s" in line
    assert TurnTimer().summary() == ""

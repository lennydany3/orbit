"""A reminder or a timer that rings until you say you have heard it, in the simulated world of test_loop.py: a fake microphone, a fake
clock, a fake voice and a fake alarm sound. Nothing here plays a sound or needs a model.

The rhythm in these tests is short (1 s of ringing, 2 s of listening) so that a minute of "nobody answered" takes a moment."""
from datetime import datetime, timedelta

import pytest

from orbit.ipc import protocol
from orbit.reminders import Reminder, ReminderStore
from orbit.voice.hotkey import KeyEvent
from orbit.voice.state import State

from test_live_changes import Due
from test_loop import build, moves
from test_loop_ui import Face

NOW = datetime(2026, 11, 22, 14, 30)


class FakeAlarm:
    """Records when the sound starts and stops, and whether the microphone was off while it played."""

    def __init__(self, mic=None):
        self.mic, self.log, self.playing, self.gain, self.on_audio = mic, [], False, 1.0, None

    def ring_seconds(self, kind, at_least):
        return at_least

    def start(self, kind):
        self.log.append(("start", kind, bool(self.mic.paused) if self.mic is not None else None))
        self.playing = True

    def stop(self):
        if self.playing:
            self.log.append(("stop",))
        self.playing = False

    def set_volume(self, v):
        self.gain = v

    def set_device(self, spec):
        return True

    @property
    def starts(self):
        return [entry for entry in self.log if entry[0] == "start"]


def ringing(reminders, *texts, **kwargs):
    """A loop with `reminders` due, a fake alarm sound and a face; the rhythm is 1 s of ringing then 2 s of listening."""
    sim, loop, keys, tts, wake, out = build(*texts, reminders=reminders, alarm=None, alarm_ring_s=1.0, alarm_listen_s=2.0, alarm_max_s=120.0, **kwargs)
    alarm = FakeAlarm(loop.mic)
    loop.alarm = alarm
    face = Face(sim)
    loop.ui = face.bridge
    return sim, loop, keys, tts, out, face, alarm


def due(title="Call mom", kind="reminder", late=False, rid="r-1"):
    return Reminder(rid, title, datetime.now(), "none", True, None, kind), late


def user_speaks_in_the_quiet(sim, loop, frames=10, when=lambda loop: True):
    """Speaks once the alarm has gone quiet for listening (after the echo of the sound has died away)."""
    sim.when(lambda: loop.st.is_(State.ALARM) and loop._ring is not None and loop._ring.step == "listen" and sim.t >= loop._ring.settle and when(loop),
             lambda: setattr(sim, "talk", frames))


# ------------------------------------------------------------------ ringing and being told "ok thanks"
def test_a_reminder_rings_says_what_it_is_and_stops_when_you_say_thanks():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "ok thank you")
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    assert loop.run() == "quit"
    assert alarm.log == [("start", "reminder", True), ("stop",)], "the bell rang with the microphone off, and stopped"
    assert tts.spoken == ["Reminder: Call mom.", "You're welcome."], "it said what it was once, then answered the thanks"
    assert moves(loop) == [("idle", "alarm"), ("alarm", "speaking"), ("speaking", "idle")]
    assert face.of_type("reminder") == [{"type": "reminder", "id": "r-1", "title": "Call mom", "text": "Reminder: Call mom.", "ringing": True}]
    assert face.of_type("alarm_stopped") == [{"type": "alarm_stopped", "id": "r-1", "by": "voice"}]
    assert loop.agent.calls == [], "the language model is not involved"
    assert loop.mode == "" and loop._ring is None


@pytest.mark.parametrize("said", ["ok", "okay", "fine", "thx", "got it", "alright", "stop", "thanks for reminding me"])
def test_each_of_these_stops_it(said):
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), said)
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop._ring is None and any(b == State.ALARM for _, _, b, _ in loop.st.history) and loop.st.is_(State.IDLE)
    loop.run()
    assert alarm.log[-1] == ("stop",) and face.of_type("alarm_stopped")[0]["by"] == "voice"


def test_it_keeps_ringing_over_and_over_and_says_what_it_is_every_third_time():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "ok thanks")
    user_speaks_in_the_quiet(sim, loop, when=lambda loop: len(alarm.starts) == 4)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert [e[0] for e in alarm.log].count("start") == 4 and alarm.log.count(("stop",)) == 4
    assert tts.spoken == ["Reminder: Call mom.", "Reminder: Call mom.", "You're welcome."], "the first time and the fourth, not every time"
    assert all(entry[2] for entry in alarm.starts), "the microphone was off every time the sound played"


def test_something_else_said_while_it_rings_does_not_stop_it():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "what is the weather today", "ok")
    user_speaks_in_the_quiet(sim, loop, when=lambda loop: len(alarm.starts) == 1)           # the weather, in the first quiet moment
    user_speaks_in_the_quiet(sim, loop, when=lambda loop: len(alarm.starts) == 2)           # "ok", in the next
    sim.stop_when = lambda: loop._ring is None and loop.st.is_(State.IDLE) and any(b == State.ALARM for _, _, b, _ in loop.st.history)
    loop.run()
    assert len(alarm.starts) == 2 and loop.agent.calls == [], "it rang again after the weather question, and never asked the model"
    assert any('"what is the weather today" -> ignore' in line for line in out)
    assert any('"ok" -> dismiss' in line for line in out)


def test_a_timer_has_its_own_sound_and_says_the_timer_is_up():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due("Timer", "timer")), "thanks")
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert alarm.starts == [("start", "timer", True)] and tts.spoken[0] == "Your timer is up."
    assert face.of_type("reminder")[0]["kind"] == "timer" and face.of_type("reminder")[0]["ringing"] is True


def test_an_alarm_clock_rings_with_the_timer_sound():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due("Alarm", "alarm")), "okay")
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop._ring is None and loop.st.is_(State.IDLE) and any(b == State.ALARM for _, _, b, _ in loop.st.history)
    loop.run()
    assert alarm.starts[0][:2] == ("start", "timer") and tts.spoken[0] == "Your alarm is going off."


def test_two_that_come_due_together_ring_once_and_are_both_said():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due("Stretch", rid="r-1"), due("Timer", "timer", rid="r-2")), "ok")
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop._ring is None and loop.st.is_(State.IDLE) and any(b == State.ALARM for _, _, b, _ in loop.st.history)
    loop.run()
    assert alarm.starts == [("start", "timer", True)], "one ring, and the louder sound"
    assert tts.spoken == ["Reminder: Stretch. Your timer is up."]
    assert {m["id"] for m in face.of_type("reminder")} == {"r-1", "r-2"} and {m["id"] for m in face.of_type("alarm_stopped")} == {"r-1", "r-2"}


def test_one_that_came_due_while_orbit_was_off_is_only_said_not_rung():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due(late=True)))
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert alarm.log == [] and tts.spoken == ["Reminder, a bit late: Call mom."]
    assert moves(loop) == [("idle", "speaking"), ("speaking", "idle")]


def test_with_alarms_switched_off_it_is_said_once_like_before(monkeypatch):
    from orbit.voice import config

    monkeypatch.setattr(config, "ALARM", False)
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()))
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert alarm.log == [] and tts.spoken == ["Reminder: Call mom."]


# ------------------------------------------------------------------ other ways to stop it
def test_the_stop_button_in_the_window_stops_it():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()))
    sim.when(lambda: alarm.starts and loop.st.is_(State.ALARM), lambda: face.bridge.on_command(protocol.DismissRequest("r-1")))
    sim.stop_when = lambda: loop._ring is None and loop.st.is_(State.IDLE) and any(b == State.ALARM for _, _, b, _ in loop.st.history)
    loop.run()
    assert alarm.log == [("start", "reminder", True), ("stop",)] and tts.spoken == []
    assert face.of_type("alarm_stopped") == [{"type": "alarm_stopped", "id": "r-1", "by": "ui"}]
    assert moves(loop) == [("idle", "alarm"), ("alarm", "idle")]


def test_a_stop_for_some_other_reminder_does_nothing():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "thanks")
    sim.when(lambda: alarm.starts and loop.st.is_(State.ALARM), lambda: face.bridge.on_command(protocol.DismissRequest("r-99")))
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert face.of_type("alarm_stopped")[0]["by"] == "voice", "only the thanks stopped it"


def test_a_stop_pressed_for_an_earlier_ring_cannot_stop_the_next_one():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "thanks")
    face.bridge.on_command(protocol.DismissRequest("r-1"))                        # left over from yesterday's ring, never read
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert face.of_type("alarm_stopped")[0]["by"] == "voice" and len(alarm.starts) == 1


def test_the_talk_key_stops_it_at_once_and_what_you_say_next_is_a_normal_request():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "open spotify")
    sim.when(lambda: alarm.starts and loop.st.is_(State.ALARM) and loop._ring.step == "ring", lambda: keys.events.append(KeyEvent("down")))
    sim.when(lambda: loop.mode == "ptt" and loop.st.is_(State.LISTENING), lambda: setattr(sim, "talk", 12))
    sim.when(lambda: loop.mode == "ptt" and loop.st.is_(State.LISTENING) and sim.talk == 0 and sim.reads > 30, lambda: keys.events.append(KeyEvent("up", 1.3, True)))
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert alarm.log == [("start", "reminder", True), ("stop",)]
    assert face.of_type("alarm_stopped")[0]["by"] == "key"
    assert loop.agent.calls == ["open spotify"], "the key press was not wasted: the next words went to the assistant"
    assert moves(loop)[:3] == [("idle", "alarm"), ("alarm", "listening"), ("listening", "transcribing")]


def test_the_kill_switch_silences_it_and_stops_the_loop():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()))
    sim.when(lambda: alarm.starts and loop.st.is_(State.ALARM), lambda: keys.events.append(KeyEvent("kill")))
    assert loop.run() == "killed"
    assert alarm.playing is False and face.of_type("alarm_stopped")[0]["by"] == "killed"


def test_it_gives_up_after_the_longest_it_may_ring():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()))
    loop.alarm_max_s = 10.0
    sim.stop_when = lambda: loop._ring is None and loop.st.is_(State.IDLE) and any(b == State.ALARM for _, _, b, _ in loop.st.history)
    loop.run()
    assert face.of_type("alarm_stopped") == [{"type": "alarm_stopped", "id": "r-1", "by": "timeout"}]
    assert alarm.playing is False and 10.0 <= sim.t < 14.0 and len(alarm.starts) >= 3
    assert any("nobody answered" in line for line in out)


def test_it_rings_as_long_as_it_takes_when_there_is_no_limit():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "thanks")
    loop.alarm_max_s = 0
    user_speaks_in_the_quiet(sim, loop, when=lambda loop: len(alarm.starts) == 12)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert len(alarm.starts) == 12 and sim.t > 30


# ------------------------------------------------------------------ snooze
def test_snooze_rings_the_same_thing_again_later(tmp_path):
    store = ReminderStore(tmp_path / "r.json")
    store.add("Call mom", datetime.now() - timedelta(seconds=1), "none", datetime.now(), "reminder")
    store.add("Pasta", datetime.now() - timedelta(seconds=1), "none", datetime.now(), "timer")
    sim, loop, keys, tts, out, face, alarm = ringing(store, "snooze for ten minutes")
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert tts.spoken[-1] == "Okay, I'll ring again in 10 minutes."
    again = {r.title: r for r in store.reminders}
    assert set(again) == {"Call mom", "Pasta"} and {r.kind for r in again.values()} == {"reminder", "timer"}
    assert all(timedelta(minutes=9) < r.due - datetime.now() <= timedelta(minutes=10) for r in again.values())
    assert face.of_type("alarm_stopped")[0]["by"] == "voice"


def test_snooze_with_no_length_is_five_minutes(tmp_path):
    store = ReminderStore(tmp_path / "r.json")
    store.add("Call mom", datetime.now() - timedelta(seconds=1), "none", datetime.now())
    sim, loop, keys, tts, out, face, alarm = ringing(store, "snooze")
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert tts.spoken[-1] == "Okay, I'll ring again in 5 minutes."


def test_a_snooze_that_cannot_be_saved_is_said_and_still_stops_the_ring():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "snooze")          # this store cannot add anything
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert tts.spoken[-1] == "Sorry, I couldn't snooze that." and alarm.playing is False


# ------------------------------------------------------------------ a sound that cannot be played
def test_a_sound_that_cannot_play_still_says_the_reminder_and_listens():
    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()), "thanks")

    def broken(kind):
        raise OSError("no sound card")

    alarm.start = broken
    user_speaks_in_the_quiet(sim, loop)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert tts.spoken == ["Reminder: Call mom.", "You're welcome."]
    assert sum("cannot play the alarm sound" in line for line in out) == 1, "said once, not every stretch"


def test_the_alarm_follows_the_volume_and_speaker_of_the_settings_page():
    from orbit import settings

    sim, loop, keys, tts, out, face, alarm = ringing(Due(due()))
    chosen = []
    alarm.set_device = lambda spec: chosen.append(spec) or True
    loop.tts.set_volume = lambda v: None
    loop.tts.set_device = lambda spec: True
    loop.apply_prefs(settings.parse({"audio": {"volume": 40, "speaker": "Echo"}}))
    assert alarm.gain == 0.4 and chosen == ["Echo"]

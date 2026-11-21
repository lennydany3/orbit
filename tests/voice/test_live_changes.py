"""What changes while Orbit runs: the Settings page's choices, reminders coming due, and the window's Play button. Same simulated world
as test_loop.py (fake microphone, fake clock, fake voice)."""
import json
import os
import time
from datetime import datetime

import numpy as np
import pytest

from orbit import settings
from orbit.ipc import protocol
from orbit.reminders import Reminder
from orbit.voice import config
from orbit.voice.prefs import LivePrefs
from orbit.voice.state import State

from test_loop import build, moves
from test_loop_ui import Face


def prefs_for(**sections) -> settings.Settings:
    return settings.parse(sections)


# ------------------------------------------------------------------ reading the file
def test_the_file_is_looked_at_about_once_a_second_and_read_only_when_it_changed(tmp_path):
    file = tmp_path / "s.json"
    t = [0.0]
    live = LivePrefs(file, check_s=1.0, clock=lambda: t[0])
    assert live.current.voice.wake_word is True and live.poll() is None
    file.write_text(json.dumps({"voice": {"wake_word": False}}), encoding="utf-8")
    t[0] = 0.5
    assert live.poll() is None, "not looked at yet"
    t[0] = 1.2
    got = live.poll()
    assert got is not None and got.voice.wake_word is False and live.current is got
    t[0] = 3.0
    assert live.poll() is None, "unchanged: not read again"
    os.utime(file, ns=(time.time_ns() + 9_000_000_000,) * 2)
    file.write_text(json.dumps({"audio": {"volume": 20}}), encoding="utf-8")
    t[0] = 5.0
    assert live.poll().audio.volume == 20


def test_a_broken_file_means_the_defaults_never_a_crash(tmp_path):
    file = tmp_path / "s.json"
    t = [0.0]
    live = LivePrefs(file, clock=lambda: t[0])
    file.write_text("{{{", encoding="utf-8")
    t[0] = 2.0
    assert live.poll().voice == settings.VoiceOptions(True, True)


# ------------------------------------------------------------------ applying it
class Voice:
    """A fake voice that can be told how loud to be and where to speak."""

    def __init__(self):
        self.volume, self.device = None, "unset"

    def set_volume(self, v):
        self.volume = v

    def set_device(self, spec):
        if spec == "gone":
            raise ValueError("No output device whose name contains 'gone'")
        changed = spec != self.device
        self.device = spec
        return changed


def with_voice(loop, tts):
    loop.tts.set_volume, loop.tts.set_device = tts.set_volume, tts.set_device
    return tts


def test_the_pages_choices_are_applied_to_a_running_loop():
    sim, loop, keys, tts, wake, out = build(with_wake=True)
    voice = with_voice(loop, Voice())
    assert loop.wake_enabled and loop.follow_up
    loop.apply_prefs(prefs_for(voice={"wake_word": False, "keep_listening": False}, audio={"volume": 89, "speaker": "Echo"}))
    assert (loop.wake_enabled, loop.follow_up, voice.volume, voice.device) == (False, False, 0.89, "Echo")
    assert any("Wake word off" in line for line in out) and any("Keep listening after a reply: off" in line for line in out)
    loop.apply_prefs(prefs_for())
    assert (loop.wake_enabled, loop.follow_up, voice.volume) == (True, True, 1.0), "back to the way it always was"


def test_what_the_command_line_chose_is_not_changed_by_the_page():
    sim, loop, keys, tts, wake, out = build(with_wake=True, lock=frozenset({"wake", "follow_up", "mic", "speaker"}))
    voice = with_voice(loop, Voice())
    loop.mic.switch = lambda spec: pytest.fail("the microphone was chosen on the command line")
    loop.apply_prefs(prefs_for(voice={"wake_word": False, "keep_listening": False}, audio={"mic": "USB", "speaker": "Echo"}))
    assert loop.wake_enabled is True and loop.follow_up is True and voice.device == "unset"


def test_a_microphone_that_is_not_there_keeps_the_one_that_works():
    sim, loop, keys, tts, wake, out = build()
    calls = []

    def switch(spec):
        calls.append(spec)
        if spec == "gone":
            raise ValueError("No input device whose name contains 'gone'")
        return True

    loop.mic.switch = switch
    loop.apply_prefs(prefs_for(audio={"mic": "gone"}))
    assert calls == ["gone"] and any("cannot use the microphone 'gone'" in line and "keeping the current one" in line for line in out)
    loop.apply_prefs(prefs_for(audio={"mic": "USB"}))
    assert calls == ["gone", "USB"] and any(line.startswith("Microphone:") for line in out)


def test_a_speaker_that_is_not_there_keeps_the_one_that_works():
    sim, loop, keys, tts, wake, out = build()
    with_voice(loop, Voice())
    loop.apply_prefs(prefs_for(audio={"speaker": "gone"}))
    assert any("cannot use the speaker 'gone'" in line for line in out)


def test_the_choices_wait_for_a_quiet_moment_so_a_microphone_is_never_swapped_mid_sentence():
    sim, loop, keys, tts, wake, out = build()
    loop.mic.switch = lambda spec: True
    seen = []

    class Live:
        def __init__(self):
            self.pending = [prefs_for(voice={"keep_listening": False})]

        def poll(self):
            return self.pending.pop() if self.pending else None

    loop.prefs = Live()
    loop.st.to(State.LISTENING, "test")
    loop._follow_prefs()
    assert loop.follow_up is True and loop._wanted is not None, "read, but held back while a turn is under way"
    loop.st.to(State.TRANSCRIBING, "test")
    loop.st.to(State.THINKING, "test")
    loop.st.to(State.SPEAKING, "test")
    loop._idle("done")
    loop._follow_prefs()
    assert loop.follow_up is False and loop._wanted is None


def test_switching_the_wake_word_on_clears_what_it_heard_while_it_was_off():
    sim, loop, keys, tts, wake, out = build(with_wake=True, wake_enabled=False)
    resets = []
    wake.reset = lambda: resets.append(1)
    loop.apply_prefs(prefs_for(voice={"wake_word": True}))
    assert loop.wake_enabled and resets == [1]


def test_with_the_wake_word_off_it_is_never_listened_for_but_the_key_still_works():
    sim, loop, keys, tts, wake, out = build("open spotify", with_wake=True, wake_enabled=False)
    sim.when(lambda: sim.reads > 5, lambda: setattr(wake, "armed", True))
    sim.stop_when = lambda: sim.reads > 60
    loop.run()
    assert loop.agent.calls == [] and wake.armed is True, "the model was never asked"
    assert not loop._can_barge_in("hello"), "and it cannot interrupt speech either"


# ------------------------------------------------------------------ reminders
class Due:
    """A store with reminders already due, handed out once."""

    def __init__(self, *items):
        self.items, self.asked = list(items), 0

    def take_due(self, now):
        self.asked += 1
        items, self.items = self.items, []
        return items


def run_until_quiet(sim, loop, turns):
    sim.stop_when = lambda: loop.turns == turns and loop.st.is_(State.IDLE)
    return loop.run()


def test_a_reminder_that_comes_due_is_said_by_orbit_and_shown_to_the_face():
    sim, loop, keys, tts, _, out = build(reminders=Due((Reminder("r-1", "Call mom", datetime.now()), False)))
    face = Face(sim)
    loop.ui = face.bridge
    assert run_until_quiet(sim, loop, 1) == "quit"
    assert tts.spoken == ["Reminder: Call mom."]
    assert moves(loop) == [("idle", "speaking"), ("speaking", "idle")], "nobody was talking: no follow-up window opens afterwards"
    assert face.of_type("reminder") == [{"type": "reminder", "id": "r-1", "title": "Call mom", "text": "Reminder: Call mom."}]
    assert face.of_type("reply")[0]["text"] == "Reminder: Call mom." and any("[reminder]" in line for line in out)
    assert loop.agent.calls == [], "the language model is not involved"


def test_two_reminders_due_together_are_said_one_after_the_other():
    now = datetime.now()
    sim, loop, keys, tts, _, _ = build(reminders=Due((Reminder("r-1", "Stretch", now), False), (Reminder("r-2", "Timer", now), True)))
    run_until_quiet(sim, loop, 2)
    assert tts.spoken == ["Reminder: Stretch.", "Your timer is up."]


def test_a_reminder_waits_for_the_end_of_a_conversation():
    sim, loop, keys, tts, _, _ = build("open spotify", reminders=Due((Reminder("r-1", "Call mom", datetime.now()), False)))
    from test_loop import user_holds_key_and_talks

    sim.when(lambda: sim.reads > 5, lambda: None)
    loop._reminders_at = 10_000.0                                   # not asked about until the turn is over
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.turns == 1 and loop.st.is_(State.IDLE), lambda: setattr(loop, "_reminders_at", 0.0))
    sim.stop_when = lambda: loop.turns == 2 and loop.st.is_(State.IDLE)
    loop.run()
    assert tts.spoken == ["Okay, I would do this: open spotify.", "Reminder: Call mom."]


def test_a_problem_reading_the_reminders_never_stops_the_voice():
    class Broken:
        def take_due(self, now):
            raise OSError("disk on fire")

    sim, loop, keys, tts, _, _ = build("open spotify", reminders=Broken())
    from test_loop import user_holds_key_and_talks

    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    assert loop.run() == "quit" and tts.spoken == ["Okay, I would do this: open spotify."]


# ------------------------------------------------------------------ the window's Play button
class PlayingAgent:
    def __init__(self):
        self.played, self.calls = [], []
        self.safety = type("S", (), {"confirm": None})()
        self.last_kind, self.last_failed, self.actions_done = "action", False, 0

    def play_saved(self, playlist_id):
        self.played.append(playlist_id)
        return "Playing Gym Mix on this PC."

    def handle(self, text):
        self.calls.append(text)
        return "no"


def test_the_play_button_plays_the_saved_playlist_and_says_so():
    sim, loop, keys, tts, _, out = build(agent=PlayingAgent())
    face = Face(sim)
    loop.ui = face.bridge
    sim.when(lambda: sim.reads > 3, lambda: face.bridge.on_command(protocol.PlayRequest("p-1")))
    assert run_until_quiet(sim, loop, 1) == "quit"
    assert loop.agent.played == ["p-1"] and loop.agent.calls == []
    assert tts.spoken and "Gym Mix" in tts.spoken[0]
    assert [m for _, m in [(a, b) for _, a, b, _ in loop.st.history]][:3] == [State.THINKING, State.SPEAKING, State.IDLE], "no follow-up window for a button"
    assert face.states()[:3] == ["thinking", "success", "speaking"]


def test_the_play_button_does_nothing_for_an_agent_that_cannot():
    sim, loop, keys, tts, _, _ = build()                             # the dev EchoAgent has no saved playlists
    face = Face(sim)
    loop.ui = face.bridge
    sim.when(lambda: sim.reads > 3, lambda: face.bridge.on_command(protocol.PlayRequest("p-1")))
    sim.stop_when = lambda: sim.reads > 40
    loop.run()
    assert tts.spoken == [] and moves(loop) == []


def test_the_speaking_volume_scales_what_is_played():
    from orbit.voice import tts as tts_mod

    class Engine(tts_mod.StreamingTTS):
        name = "t"

    engine = Engine()
    assert engine.gain == 1.0
    fallback = tts_mod.FallbackTTS([engine, tts_mod.WindowsTTS.__new__(tts_mod.WindowsTTS)])
    fallback.set_volume(0.5)
    assert engine.gain == 0.5 and fallback.engines[1].gain == 0.5
    fallback.set_volume(7)
    assert engine.gain == 1.0
    fallback.set_volume(-3)
    assert engine.gain == 0.0


def test_the_real_microphone_switches_and_goes_back_if_the_new_one_will_not_open(monkeypatch):
    from orbit.voice import audio_io

    opened = []

    class Stream:
        def __init__(self, **kw):
            opened.append(kw["device"])
            if kw["device"] == 9:
                raise RuntimeError("device busy")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(audio_io.sd, "InputStream", Stream)
    monkeypatch.setattr(audio_io, "resolve_device", lambda spec, kind: {None: 1, "": 1, "usb": 2, "busy": 9}[spec])
    mic = audio_io.MicStream(None)
    mic.start()
    assert mic.switch("") is False, "already the default"
    assert mic.switch("usb") is True and mic.device == 2 and opened == [1, 2]
    with pytest.raises(RuntimeError):
        mic.switch("busy")
    assert mic.device == 2 and opened[-2:] == [9, 2], "it went back to the microphone that works, and is listening on it again"

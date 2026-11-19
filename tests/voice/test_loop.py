"""The voice loop end to end, in a simulated world: a fake user "speaks" into a fake microphone on a fake clock.
Whisper, the voice and the agent are stand-ins, so every path (hold-to-talk, wake word, follow-up, confirmation, barge-in,
kill switch, timeouts, errors) runs deterministically in about a second each, with no hardware and no models."""
import random
import threading
import time

import numpy as np
import pytest

from orbit.voice import config
from orbit.voice.hotkey import KeyEvent
from orbit.voice.loop import EchoAgent, VoiceLoop, describe_action, is_noise, parse_yes_no
from orbit.voice.state import State
from orbit.voice.stt import Transcript
from orbit.voice.tts import SpeakStats
from orbit.voice.vad import SpeechSegmenter
from orbit.voice.wakeword import WakeResult

LOUD = np.full(config.FRAME_SAMPLES, 8000, np.int16)
QUIET = np.zeros(config.FRAME_SAMPLES, np.int16)


# ------------------------------------------------------------------ pure helpers
@pytest.mark.parametrize("text, expected", [
    ("yes", True), ("Yes.", True), ("yeah", True), ("Yep, do it", True), ("go ahead", True), ("yes please", True), ("sure", True),
    ("no", False), ("nope", False), ("yes no", False), ("don't", False), ("maybe", False), ("", False), ("uh", False),
    ("okay", False), ("I think so", False), ("yes I guess that would be fine whatever", False), ("not yes", False),
])
def test_only_a_clear_yes_counts(text, expected):
    assert parse_yes_no(text) is expected


def test_risky_action_is_described_in_plain_words():
    assert describe_action("os_close_window({'title': 'Notepad'}) is a risky action. Allow?") == "close window Notepad"
    assert describe_action("browser_click({'text': 'Delete account', 'scope': 'this'}) is a risky action. Allow?") == "click Delete account this"
    assert describe_action("os_quit_app({'name': 'ChatGPT'}) is a risky action. Allow?") == "quit ChatGPT completely"
    assert describe_action("something odd") == "do something that needs your permission"


# ------------------------------------------------------------------ the simulated world
class LoudnessVAD:
    def reset(self):
        pass

    def __call__(self, window):
        return 1.0 if float(np.abs(window).max()) > 0.1 else 0.0


class Sim:
    def __init__(self):
        self.t, self.reads, self.talk, self.rules, self.stop_when = 0.0, 0, 0, [], None

    def clock(self):
        return self.t

    def when(self, predicate, action):
        self.rules.append([predicate, action, False])


class SimMic:
    def __init__(self, sim):
        self.sim, self.paused = sim, False

    def start(self):
        pass

    def stop(self):
        pass

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def read(self, timeout=0.05):
        sim = self.sim
        time.sleep(0.0006)                      # lets worker threads (agent, voice, speech-to-text) make progress
        sim.t += config.FRAME_MS / 1000
        sim.reads += 1
        for rule in sim.rules:
            if not rule[2] and rule[0]():
                rule[2] = True
                rule[1]()
        if sim.stop_when and sim.stop_when():
            raise KeyboardInterrupt
        if self.paused:
            return None
        if sim.talk > 0:
            sim.talk -= 1
            return LOUD
        return QUIET

    def read_timed(self, timeout=0.05):
        frame = self.read(timeout)
        return None if frame is None else (frame, self.sim.t)


class FakeKeys:
    def __init__(self):
        self.events, self.killed = [], threading.Event()

    def start(self):
        pass

    def stop(self):
        pass

    def poll(self):
        out, self.events = self.events, []
        return out


class FakeTTS:
    supports_pause = True

    def __init__(self, mic, slow=False):
        self.mic, self.slow, self.spoken, self.mic_paused_while_speaking, self.stopped = mic, slow, [], [], threading.Event()
        self.events = []                                   # "pause" / "resume" calls, in order

    def pause(self):
        self.events.append("pause")

    def resume(self):
        self.events.append("resume")

    def speak(self, text):
        self.spoken.append(text)
        self.mic_paused_while_speaking.append(self.mic.paused)
        if self.slow:
            self.stopped.wait(3.0)
        else:
            time.sleep(0.005)
        return SpeakStats("fake", 0.01, first_audio_s=0.005, interrupted=self.stopped.is_set())

    def stop(self):
        self.stopped.set()


class FakeTranscriber:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), 0

    def transcribe(self, clip):
        self.calls += 1
        return Transcript(self.texts.pop(0) if self.texts else "", 0.2, len(clip) / config.SAMPLE_RATE)


class FakeWake:
    def __init__(self, sim):
        self.sim, self.armed, self.reject = sim, False, False

    def process(self, frame, on_candidate=None):
        if self.armed:
            self.armed = False
            if on_candidate:
                on_candidate()                              # the model fired, before the slower check
            if self.reject:
                return WakeResult(1.0, False, rejected=True, heard="hey bit")
            return WakeResult(1.0, True)
        return WakeResult(0.0, False)

    def reset(self):
        pass


def build(*texts, agent=None, tts_slow=False, with_wake=False, follow_up=True, pause_s=None, **loop_kwargs):
    sim = Sim()
    mic, keys = SimMic(sim), FakeKeys()
    tts = FakeTTS(mic, slow=tts_slow)
    out: list[str] = []
    wake = FakeWake(sim) if with_wake else None
    agent = agent or EchoAgent()
    loop_kwargs.setdefault("media_check", lambda: False)                     # never ask the real PC what is playing
    transcriber = FakeTranscriber(*texts)
    loop = VoiceLoop(agent, mic, transcriber, tts, keys, wake, SpeechSegmenter(LoudnessVAD(), pause_s=pause_s), LoudnessVAD(),
                     follow_up=follow_up, out=out.append, file_log=lambda line: None, clock=sim.clock, **loop_kwargs)
    agent.safety.confirm = loop.confirm
    # the user's habits: hold the key and talk when idle; stop talking and release when done
    loop.test_transcriber = transcriber
    return sim, loop, keys, tts, wake, out


def user_holds_key_and_talks(sim, loop, keys, after_reads=5):
    sim.when(lambda: sim.reads > after_reads and loop.st.is_(State.IDLE) and loop.turns == 0,
             lambda: (keys.events.append(KeyEvent("down")), setattr(sim, "talk", 15)))
    sim.when(lambda: loop.mode == "ptt" and loop.st.is_(State.LISTENING) and sim.talk == 0 and sim.reads > after_reads + 3,
             lambda: keys.events.append(KeyEvent("up", 1.3, True)))


def moves(loop):
    return [(a.value, b.value) for _, a, b, _ in loop.st.history]


# ------------------------------------------------------------------ the scenarios
def test_hold_to_talk_turn_runs_the_agent_and_speaks_a_short_reply():
    sim, loop, keys, tts, _, out = build("open spotify")
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    assert loop.run() == "quit"
    assert loop.agent.calls == ["open spotify"]
    assert tts.spoken == ["Okay, I would do this: open spotify."]
    assert moves(loop) == [("idle", "listening"), ("listening", "transcribing"), ("transcribing", "thinking"), ("thinking", "speaking"),
                           ("speaking", "follow_up"), ("follow_up", "idle")]
    assert tts.mic_paused_while_speaking == [True]                  # half-duplex: the mic is off while Orbit talks
    assert any("[listening]" in line for line in out) and any("[thinking]" in line for line in out) and any("[speaking]" in line for line in out)
    assert any(line.startswith("Orbit: Okay, I would do this: open spotify") for line in out)   # the full reply is shown


def test_the_spoken_reply_is_the_short_version_but_the_screen_has_all_of_it():
    class Verbose:
        safety = type("S", (), {"confirm": None})()

        def handle(self, text):
            return "Opened Spotify. Volume is 30 percent. Playing lofi. On the Echo Pop. See https://x.example/y for more."

    sim, loop, keys, tts, _, out = build("open spotify", agent=Verbose())
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken == ["Opened Spotify. Volume is 30 percent. The rest is on screen."]
    assert any("https://x.example/y" in line for line in out)


def test_wake_word_turn_strips_the_wake_phrase_before_the_agent_sees_it():
    sim, loop, keys, tts, wake, _ = build("Hey Orbit, open Spotify.", with_wake=True)
    sim.when(lambda: sim.reads > 5, lambda: setattr(wake, "armed", True))
    sim.when(lambda: loop.st.is_(State.LISTENING) and sim.talk == 0 and not loop.heard, lambda: setattr(sim, "talk", 15))
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert loop.agent.calls == ["open Spotify."]
    assert moves(loop)[:3] == [("idle", "wake_detected"), ("wake_detected", "listening"), ("listening", "transcribing")]


def test_nothing_said_after_the_wake_word_goes_back_to_sleep_after_3_5_seconds():
    sim, loop, keys, tts, wake, out = build(with_wake=True)
    sim.when(lambda: sim.reads > 5, lambda: setattr(wake, "armed", True))
    sim.stop_when = lambda: any(b == State.IDLE and a == State.LISTENING for _, a, b, _ in loop.st.history)
    loop.run()
    listening_since = next(i for i, (_, _, b, _) in enumerate(loop.st.history) if b == State.LISTENING)
    assert loop.agent.calls == [] and tts.spoken == []
    assert any("nothing heard" in line for line in out)
    assert 3.5 <= sim.t - 6 * 0.08 < 4.5, "the timeout should be about 3.5 s of (simulated) time"
    assert listening_since >= 0


def test_an_empty_transcript_never_reaches_the_agent():
    sim, loop, keys, tts, _, out = build("")
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert loop.agent.calls == [] and tts.spoken == ["Sorry, I didn't catch that."]
    assert any("agent will not run" in line for line in out)


def test_a_follow_up_works_without_the_wake_word_and_then_the_window_closes():
    sim, loop, keys, tts, _, out = build("volume thirty", "pause")
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.FOLLOW_UP) and sim.talk == 0 and not loop.heard and loop.turns == 1, lambda: setattr(sim, "talk", 12))
    sim.stop_when = lambda: loop.turns == 2 and loop.st.is_(State.IDLE)
    loop.run()
    assert loop.agent.calls == ["volume thirty", "pause"]
    assert ("follow_up", "listening") in moves(loop)


def test_follow_up_can_be_turned_off():
    sim, loop, keys, tts, _, _ = build("volume thirty", follow_up=False)
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert ("speaking", "idle") in moves(loop) and ("speaking", "follow_up") not in moves(loop)


class RiskyAgent:
    """Stands in for an agent whose tool is risky: it asks through safety.confirm, exactly like Safety.approve does."""

    def __init__(self):
        self.safety = type("S", (), {"confirm": None})()
        self.asked = []

    def handle(self, text):
        self.asked.append(text)
        ok = self.safety.confirm("os_close_window({'title': 'Notepad'}) is a risky action. Allow?")
        return "Closed Notepad." if ok else "Okay, I left Notepad open."


@pytest.mark.parametrize("answer, expected_reply", [
    ("yes please", "Closed Notepad."), ("Yeah, do it", "Closed Notepad."),
    ("no", "Okay, I left Notepad open."), ("maybe later", "Okay, I left Notepad open."), ("", "Okay, I left Notepad open."),
    (None, "Okay, I left Notepad open."),                                  # said nothing at all: that is a no
])
def test_a_risky_action_is_confirmed_by_voice_and_only_a_clear_yes_runs_it(answer, expected_reply):
    texts = ["close notepad"] + ([answer] if answer is not None else [])
    sim, loop, keys, tts, _, out = build(*texts, agent=RiskyAgent())
    user_holds_key_and_talks(sim, loop, keys)
    if answer is not None:
        sim.when(lambda: loop._confirm_step == "listen" and sim.talk == 0 and not loop.heard, lambda: setattr(sim, "talk", 12))
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken == ["Do you want me to close window Notepad? Say yes or no.", expected_reply]
    assert ("thinking", "confirming") in moves(loop) and ("confirming", "thinking") in moves(loop)
    if answer is None:
        assert sim.t > config.CONFIRM_TIMEOUT_S and any("no answer heard" in line for line in out)


def test_the_kill_switch_stops_everything_even_while_the_agent_is_busy():
    release = threading.Event()

    class Slow:
        safety = type("S", (), {"confirm": None})()

        def handle(self, text):
            release.wait(3.0)
            return "too late"

    sim, loop, keys, tts, _, out = build("open spotify", agent=Slow())
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.THINKING), lambda: keys.events.append(KeyEvent("kill")))
    started = time.perf_counter()
    assert loop.run() == "killed"
    release.set()
    assert time.perf_counter() - started < 2.0 and tts.stopped.is_set() and tts.spoken == []
    assert any("[killed]" in line for line in out)


def test_the_hotkey_interrupts_speech_immediately():
    sim, loop, keys, tts, _, _ = build("tell me a story", "stop", tts_slow=True)
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.SPEAKING), lambda: (keys.events.append(KeyEvent("down")), setattr(sim, "talk", 12)))
    sim.stop_when = lambda: any(a == "speaking" and b == "listening" for a, b in moves(loop)) and loop.st.is_(State.LISTENING)
    started = time.perf_counter()
    loop.run()
    assert tts.stopped.is_set() and time.perf_counter() - started < 2.0     # not the 3 s the slow voice would have taken
    assert ("speaking", "listening") in moves(loop)


def test_an_agent_crash_is_spoken_as_an_apology_not_a_crash():
    class Broken:
        safety = type("S", (), {"confirm": None})()

        def handle(self, text):
            raise RuntimeError("tool exploded")

    sim, loop, keys, tts, _, out = build("open spotify", agent=Broken())
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken and tts.spoken[0].startswith("Sorry, something went wrong")


def test_a_quick_tap_of_the_key_is_not_a_turn():
    sim, loop, keys, tts, _, out = build("open spotify")
    sim.when(lambda: sim.reads > 5, lambda: keys.events.append(KeyEvent("down")))
    sim.when(lambda: loop.st.is_(State.LISTENING), lambda: keys.events.append(KeyEvent("up", 0.08, False)))
    sim.stop_when = lambda: sim.reads > 40
    loop.run()
    assert loop.agent.calls == [] and tts.spoken == []
    assert loop.st.is_(State.IDLE)


# ------------------------------------------------------------------ the REAL agent + its real Safety gate
@pytest.mark.parametrize("answer, ran", [("yes please", True), ("no thanks", False), ("hmm maybe", False)])
def test_the_real_agent_asks_by_voice_before_a_risky_tool_and_only_a_clear_yes_runs_it(make_agent, answer, ran):
    from conftest import ScriptedLLM

    agent = make_agent(ScriptedLLM())              # rules handle "danger ...": no model needed; fake_danger is risk=confirm
    sim, loop, keys, tts, _, out = build("danger old files", answer, agent=agent)
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop._confirm_step == "listen" and sim.talk == 0 and not loop.heard, lambda: setattr(sim, "talk", 12))
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken[0] == "Do you want me to danger old files? Say yes or no."
    shown = "\n".join(out)
    assert ("pretended to delete 'old files'" in shown) is ran
    assert ("declined" in shown) is (not ran)


# ------------------------------------------------------------------ conversational voice
class ScriptedAgent:
    """Replies with a fixed text, optionally only once the test says so (to simulate a slow model)."""

    def __init__(self, reply, wait_for=None):
        self.reply, self.wait_for, self.safety = reply, wait_for, type("S", (), {"confirm": None})()

    def handle(self, text):
        if self.wait_for is not None:
            self.wait_for.wait(5.0)
        return self.reply


def _slow_turn(release_after_s, reply="opened Spotify"):
    from orbit.voice.phrasing import FILLERS_FIRST, FILLERS_LONG

    release = threading.Event()
    sim, loop, keys, tts, _, out = build("open spotify", agent=ScriptedAgent(reply, release), rng=random.Random(0))
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.THINKING) and loop.st.elapsed >= release_after_s, release.set)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    return tts, out, FILLERS_FIRST, FILLERS_LONG


def test_a_slow_answer_gets_a_one_moment_filler_instead_of_silence():
    tts, out, first, long_ = _slow_turn(4.0)                    # the model needs 4 s: one filler (due at 2.5 s), no second one
    assert len(tts.spoken) == 2 and tts.spoken[0] in first
    assert tts.spoken[1].endswith("I opened Spotify.")          # the real answer comes after the filler has finished
    assert any(line.strip() == f"[says] {tts.spoken[0]}" for line in out)


def test_a_very_slow_answer_gets_a_second_still_working_filler():
    tts, _, first, long_ = _slow_turn(9.5)
    assert len(tts.spoken) == 3 and tts.spoken[0] in first and tts.spoken[1] in long_


def test_a_quick_answer_gets_no_filler():
    tts, _, _, _ = _slow_turn(1.0)
    assert len(tts.spoken) == 1 and tts.spoken[0].endswith("I opened Spotify.")


def test_fillers_can_be_turned_off():
    release = threading.Event()
    sim, loop, keys, tts, _, _ = build("open spotify", agent=ScriptedAgent("opened Spotify", release), fillers=False)
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.THINKING) and loop.st.elapsed >= 4.0, release.set)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert len(tts.spoken) == 1


def test_tool_reports_are_spoken_naturally_while_the_screen_keeps_the_original():
    sim, loop, keys, tts, _, out = build("close notepad", agent=ScriptedAgent("asked 'Notepad' to close"))
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken[0].lower().endswith("closing notepad.") and "asked" not in tts.spoken[0]      # "Okay, closing..." / "Done. Closing..."
    assert any(line == "Orbit: asked 'Notepad' to close" for line in out)                      # the original, untouched
    assert any(line.strip().startswith("[says]") and "closing notepad." in line.lower() for line in out)


def test_natural_speech_can_be_switched_off():
    sim, loop, keys, tts, _, out = build("close notepad", agent=ScriptedAgent("asked 'Notepad' to close"), conversational=False)
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken == ["asked 'Notepad' to close"]


def test_when_it_did_not_understand_it_says_what_it_heard():
    reply = "Nothing was done: I did not run any tool for that, so I could not tell what to do. Try different words."
    sim, loop, keys, tts, _, _ = build("hey everybody", agent=ScriptedAgent(reply))
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.spoken == ["Sorry, I didn't catch what to do with \"hey everybody\". Could you say that another way?"]
    assert loop._follow_window == config.FOLLOW_UP_QUESTION_S                       # it asked you something: leave time to answer


def test_when_orbit_asks_a_question_you_get_longer_to_answer_without_the_wake_word():
    sim, loop, keys, tts, _, out = build("what is playing", agent=ScriptedAgent("Nothing is playing. Would you like to play something?"))
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.FOLLOW_UP)
    loop.run()
    assert loop._follow_window == config.FOLLOW_UP_QUESTION_S > config.FOLLOW_UP_S
    assert any("Orbit asked a question" in line for line in out)


def test_a_plain_statement_keeps_the_short_follow_up_window():
    sim, loop, keys, tts, _, _ = build("open spotify", agent=ScriptedAgent("Opened Spotify."))
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.FOLLOW_UP)
    loop.run()
    assert loop._follow_window == config.FOLLOW_UP_S


def test_the_answer_to_orbits_question_is_heard_after_a_pause_longer_than_the_normal_window():
    sim, loop, keys, tts, _, _ = build("what is playing", "yes please", agent=ScriptedAgent("Nothing is playing. Want me to play something?"))
    user_holds_key_and_talks(sim, loop, keys)
    started = {}
    sim.when(lambda: loop.st.is_(State.FOLLOW_UP), lambda: started.setdefault("t", sim.t))
    # the user answers 4 s later: too late for the normal 2 s window, fine for the 6 s question window
    sim.when(lambda: "t" in started and sim.t - started["t"] > 4.0 and loop.st.is_(State.FOLLOW_UP) and sim.talk == 0 and not loop.heard,
             lambda: setattr(sim, "talk", 12))
    sim.stop_when = lambda: loop.turns == 2
    loop.run()
    assert loop.agent.reply and len(tts.spoken) == 2


# ------------------------------------------------------------------ interrupting Orbit by voice
def _talk_then_interrupt(texts, reply="Here is a long answer that goes on for a while.", **kw):
    sim, loop, keys, tts, wake, out = build(*texts, agent=ScriptedAgent(reply), tts_slow=True, with_wake=True, **kw)
    user_holds_key_and_talks(sim, loop, keys)
    return sim, loop, keys, tts, wake, out


def test_the_wake_word_interrupts_speech_and_the_new_request_is_handled():
    sim, loop, keys, tts, wake, out = _talk_then_interrupt(["tell me a story", "Hey Orbit, open Spotify."])
    sim.when(lambda: loop.st.is_(State.SPEAKING) and sim.reads > 30, lambda: setattr(wake, "armed", True))
    sim.when(lambda: loop.mode == "wake" and loop.st.is_(State.LISTENING) and sim.talk == 0 and not loop.heard,
             lambda: setattr(sim, "talk", 15))
    sim.stop_when = lambda: loop.turns >= 1 and len(tts.spoken) >= 2                   # the interruption got its own full turn
    started = time.perf_counter()
    loop.run()
    assert tts.mic_paused_while_speaking[0] is False                  # the microphone stayed open while Orbit talked
    assert tts.events[:1] == ["pause"]                               # muted the instant the model fired...
    assert tts.stopped.is_set() and time.perf_counter() - started < 2.5   # ...and stopped for good once the check agreed (not the 3 s)
    assert ("speaking", "listening") in moves(loop)
    assert any("[interrupted]" in line for line in out)
    assert len(tts.spoken) >= 2                                      # the interruption got its own reply


def test_a_wake_candidate_the_check_rejects_does_not_cut_the_speech_for_good():
    sim, loop, keys, tts, wake, out = _talk_then_interrupt(["tell me a story"])
    wake.reject = True
    sim.when(lambda: loop.st.is_(State.SPEAKING) and sim.reads > 30, lambda: setattr(wake, "armed", True))
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.events[:2] == ["pause", "resume"]                     # muted for the check, then un-muted: it carried on
    assert ("speaking", "listening") not in moves(loop)
    assert any("not the wake word" in line for line in out)


def test_replies_that_say_the_wake_word_stay_half_duplex_so_orbit_cannot_trigger_itself():
    sim, loop, keys, tts, wake, out = _talk_then_interrupt(["help"], reply="Just say hey orbit whenever you need me.")
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.mic_paused_while_speaking == [True]


def test_without_a_wake_word_the_microphone_stays_off_while_orbit_talks():
    sim, loop, keys, tts, _, _ = build("tell me a story", agent=ScriptedAgent("A story."), tts_slow=False)
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.mic_paused_while_speaking == [True]


def test_barge_in_can_be_switched_off():
    sim, loop, keys, tts, wake, _ = _talk_then_interrupt(["tell me a story"], barge_in=False)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert tts.mic_paused_while_speaking == [True]


def test_the_echo_canceller_sees_every_frame_while_orbit_speaks_and_not_when_it_is_quiet():
    class CountingAEC:
        def __init__(self):
            self.frames, self.resets = 0, 0

        def process(self, mic, ref):
            self.frames += 1
            return mic

        def reset_history(self):
            self.resets += 1

    from orbit.voice.aec import ReferenceTimeline

    aec, timeline = CountingAEC(), ReferenceTimeline()
    sim, loop, keys, tts, wake, _ = _talk_then_interrupt(["tell me a story"], aec=aec, reference=timeline)
    sim.when(lambda: loop.st.is_(State.SPEAKING) and sim.reads > 60, lambda: tts.stopped.set())     # let the slow voice finish
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert aec.resets == 1 and aec.frames > 0


# ------------------------------------------------------------------ not acting on background sound
@pytest.mark.parametrize("text, noise", [
    ("Uh...", True), ("um", True), ("Hmm.", True), ("Okay.", True), ("uh huh", True), ("", True), ("...", True),
    ("Thank you.", True), ("Thanks for watching!", True), ("you", True), ("Bye.", True),
    ("open spotify", False), ("pause", False), ("volume 30", False), ("Thank you Orbit, open Spotify", False), ("close it", False),
])
def test_filler_and_silence_hallucinations_are_noise(text, noise):
    assert is_noise(text) is noise


def test_follow_up_stays_closed_while_music_is_playing():
    sim, loop, keys, tts, _, out = build("open spotify", media_check=lambda: True)
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.IDLE)
    loop.run()
    assert ("speaking", "idle") in moves(loop) and ("speaking", "follow_up") not in moves(loop)
    assert any("music is playing" in line for line in out)


def test_follow_up_opens_normally_when_nothing_is_playing():
    sim, loop, keys, tts, _, _ = build("open spotify", media_check=lambda: False)
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1 and loop.st.is_(State.FOLLOW_UP)
    loop.run()
    assert ("speaking", "follow_up") in moves(loop)


def test_a_filler_in_the_follow_up_window_is_ignored_without_an_apology_or_the_agent():
    sim, loop, keys, tts, _, out = build("open spotify", "Uh...")
    user_holds_key_and_talks(sim, loop, keys)
    sim.when(lambda: loop.st.is_(State.FOLLOW_UP) and sim.talk == 0 and not loop.heard and loop.turns == 1, lambda: setattr(sim, "talk", 12))
    sim.stop_when = lambda: any("ignored" in line for line in out) and loop.st.is_(State.IDLE)
    loop.run()
    assert loop.agent.calls == ["open spotify"]                                  # "Uh..." never reached the agent
    assert len(tts.spoken) == 1                                                   # and nobody apologised for a stranger's noise


def test_a_filler_after_a_deliberate_request_still_gets_an_apology():
    sim, loop, keys, tts, _, _ = build("Uh...")
    user_holds_key_and_talks(sim, loop, keys)
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert loop.agent.calls == [] and tts.spoken == ["Sorry, I didn't catch that."]


# ------------------------------------------------------------------ speculative transcription
def _wake_turn(texts, speak_plan, **kw):
    sim, loop, keys, tts, wake, out = build(*texts, with_wake=True, pause_s=0.55, **kw)
    sim.when(lambda: sim.reads > 5, lambda: setattr(wake, "armed", True))
    state = {"i": 0}

    def next_burst():
        if state["i"] < len(speak_plan):
            n, gap = speak_plan[state["i"]]
            state["i"] += 1
            sim.talk = n
            sim.gap_frames = gap

    sim.when(lambda: loop.st.is_(State.LISTENING) and sim.talk == 0, next_burst)
    return sim, loop, tts


def test_when_you_really_stopped_the_early_transcription_is_the_one_that_is_used():
    sim, loop, tts = _wake_turn(["open spotify"], [(15, 0)])
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert loop.test_transcriber.calls == 1                                       # one transcription served both the pause and the end
    assert loop.agent.calls == ["open spotify"]


def test_when_you_carry_on_after_a_pause_the_early_result_is_thrown_away():
    sim, loop, tts = _wake_turn(["open", "open spotify"], [(12, 0)])
    # after the first burst and a 0.7 s pause, speak again: the early transcription ("open") must not be used
    sim.when(lambda: loop.st.is_(State.LISTENING) and loop.mode == "wake" and sim.talk == 0 and loop._spec is not None,
             lambda: setattr(sim, "talk", 12))                                  # the moment the early transcription starts, carry on talking
    sim.stop_when = lambda: loop.turns == 1
    loop.run()
    assert loop.agent.calls[-1] == "open spotify" and loop.test_transcriber.calls == 2

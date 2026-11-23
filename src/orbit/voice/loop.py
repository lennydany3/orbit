"""The voice loop: wake word or hotkey -> your speech -> the existing agent -> a short spoken reply.

Entry point: `python -m orbit --voice`.  Dev bench: `python -m orbit.voice.loop --wav replay.wav --echo-agent`.

The agent is NOT changed: this calls the same `agent.handle(text)` the text REPL uses and gets a reply string back.
Risky actions still ask first; the question is spoken and only a clear "yes" counts (the agent's Safety.confirm is swapped
for `VoiceConfirm`, which hands the question to this loop).

Threads: only the main thread touches the microphone and the hotkeys. Speech-to-text, the agent and text-to-speech run in
short-lived daemon threads, so the kill switch and the barge-in hotkey stay responsive while Orbit thinks or talks.
Half-duplex by default: the mic is paused while Orbit transcribes, thinks and speaks, so it never hears itself.
"""
import argparse
import ast
import logging
import queue
import random
import re
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import numpy as np

from . import alarm as alarm_mod
from . import config
from ..ipc.bridge import NullBridge
from .aec import EchoCanceller, ReferenceAligner, ReferenceTimeline
from . import farewell
from .phrasing import Phraser, strip_emoji
from .state import State, TurnTimer, VoiceState
from .summarize import spoken_summary
from .wakeword import strip_wake_phrase

# ---------------------------------------------------------------- helpers (pure, unit-tested)
_NO_WORDS = {"no", "nope", "nah", "not", "dont", "don't", "stop", "cancel", "never", "wait", "negative", "wrong"}
_YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "affirmative", "absolutely", "confirm", "confirmed"}
_YES_PHRASES = ("go ahead", "do it", "please do")


def parse_yes_no(text: str) -> bool:
    """True only for a clear yes. Anything else (silence, mumbling, "maybe", "yes no", a long sentence) is no."""
    words = re.findall(r"[a-z']+", (text or "").lower().replace("’", "'"))
    if not words or len(words) > 6 or any(w in _NO_WORDS for w in words):
        return False
    joined = " ".join(words)
    return any(w in _YES_WORDS for w in words) or any(p in joined for p in _YES_PHRASES)


_RISK_QUESTION = re.compile(r"^(\w+)\((\{.*\})\)\s+is a risky action", re.S)
_LAYER_PREFIXES = {"os", "browser", "api", "spotify", "fake"}


def describe_action(question: str) -> str:
    """Safety's question ("os_close_window({'title': 'Notepad'}) is a risky action. Allow?") -> "close window Notepad"."""
    m = _RISK_QUESTION.match((question or "").strip())
    if not m:
        return "do something that needs your permission"
    words = [w for w in m.group(1).split("_") if w]
    if words and words[0] in _LAYER_PREFIXES:
        words = words[1:]
    try:
        params = ast.literal_eval(m.group(2))
    except (ValueError, SyntaxError):
        params = {}
    if words == ["custom", "command"] and params.get("name"):
        return f"run your {params['name']} command: {params.get('steps', '')}"[:240]
    if words == ["delete", "mine"] and params.get("what"):
        return f"delete {params['what']}"[:240]
    if words == ["quit", "app"] and params.get("name"):
        return f"quit {params['name']} completely"
    detail = " ".join(str(v) for v in params.values() if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip())
    return (" ".join(words) + " " + detail).strip()[:120] or "do that"


def spawn(fn, *args) -> Future:
    """Run fn(*args) in a daemon thread and return a Future (a stuck call can never keep Orbit from exiting)."""
    future: Future = Future()

    def run() -> None:
        try:
            future.set_result(fn(*args))
        except BaseException as e:  # noqa: BLE001 - handed to whoever reads the future
            future.set_exception(e)

    threading.Thread(target=run, daemon=True).start()
    return future


class VoiceConfirm:
    """Replaces Safety.confirm. Called from the agent's thread; the voice loop speaks the question and hears the answer."""

    def __init__(self, patience_s: float = config.CONFIRM_TIMEOUT_S + 60):
        self._requests: queue.Queue = queue.Queue()
        self._patience = patience_s

    def __call__(self, question: str) -> bool:
        reply = {"event": threading.Event(), "answer": False}
        self._requests.put((question, reply))
        reply["event"].wait(self._patience)          # a safety valve: never wait forever, and the default answer is no
        return bool(reply["answer"])

    def poll(self):
        try:
            return self._requests.get_nowait()
        except queue.Empty:
            return None


_FILLERS = frozenset({"uh", "um", "umm", "uhh", "hmm", "hm", "mm", "mmm", "ah", "oh", "huh", "er", "erm", "eh", "okay", "ok",
                      "so", "well", "and", "the", "a", "you"})
# What Whisper "hears" in silence or noise. Never a request.
_HALLUCINATIONS = frozenset({"thank you", "thank you.", "thanks for watching", "thanks for watching!", "bye", "bye bye", "bye.",
                             "thanks", "you", "the end", "subtitles by the amara.org community"})


def _announcement(reminder) -> str:
    from ..reminders import announcement

    return announcement(reminder)


def is_noise(text: str) -> bool:
    """A "transcript" that is only filler ("Uh...") or something Whisper invents for silence: not a request."""
    clean = re.sub(r"[^a-z' ]+", " ", (text or "").lower()).split()
    if not clean:
        return True
    return len(clean) <= 3 and all(w in _FILLERS for w in clean) or " ".join(clean) in _HALLUCINATIONS


def media_is_playing() -> bool:
    """Is something playing on this PC right now (Spotify, a browser video ...)? False if Windows cannot tell."""
    try:
        from ..tools.os_layer import media

        return media.state().status == "playing"
    except Exception:  # noqa: BLE001
        return False


def _device_name(index) -> str:
    try:
        from .audio_io import device_label

        return device_label(index)
    except Exception:  # noqa: BLE001
        return str(index)


def _keep_model_warm(llm, out, every_s: float = 20 * 60) -> None:
    """Load the language model now and touch it every 20 minutes, so no request ever pays the ~20 s cold start."""
    first = True
    while True:
        try:
            seconds = llm.warm_up()
            if first:
                out(f"  [llm] language model is ready ({seconds:.1f}s)")
        except Exception as e:  # noqa: BLE001 - Ollama may simply not be running: simple commands still work
            if first:
                out(f"  [llm] could not warm up the language model ({e}); the first model answer may be slow")
        first = False
        time.sleep(every_s)


def _file_logger() -> logging.Logger:
    logger = logging.getLogger("orbit.voice")
    if not logger.handlers:
        config.VOICE_LOG.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(config.VOICE_LOG, maxBytes=500_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


@dataclass
class Ring:
    """One reminder, timer or alarm that is ringing (or several that came due together) and the point it has got to."""
    due: list                                   # the reminders.Reminder items that rang
    kind: str                                   # which sound: "reminder" or "timer"
    text: str                                   # what Orbit says it is
    started: float
    step: str = "ring"                          # ring -> (say ->) listen -> (transcribe ->) ring again ...
    until: float = 0.0                          # when this step ends
    settle: float = 0.0                         # listening: what the microphone hears before this moment is the sound's echo
    cycle: int = 0                              # stretches of ringing so far
    heard: bool = False                         # listening: you have started to speak
    warned: bool = False                        # the "cannot play the sound" message has been shown
    ids: set = field(default_factory=set)


# ---------------------------------------------------------------- the loop
class VoiceLoop:
    def __init__(self, agent, mic, transcriber, tts, keys=None, wake=None, segmenter=None, check_vad=None, *,
                 follow_up: bool = config.FOLLOW_UP, use_ptt: bool = True, wake_name: str = config.WAKEWORD_MODEL,
                 out=print, verbose: bool = False, monitor=None, file_log=None, clock=time.monotonic,
                 conversational: bool = config.CONVERSATIONAL, fillers: bool = config.FILLERS, rng: random.Random | None = None,
                 aec: EchoCanceller | None = None, reference: ReferenceTimeline | None = None, barge_in: bool = config.BARGE_IN,
                 media_check=None, ui=None, prefs=None, reminders=None, wake_enabled: bool = True, lock: frozenset = frozenset(),
                 alarm=None, alarm_ring_s: float = config.ALARM_RING_S, alarm_listen_s: float = config.ALARM_LISTEN_S,
                 alarm_max_s: float = config.ALARM_MAX_S, alarm_settle_s: float = config.ALARM_SETTLE_S, live_decode=None):
        """`prefs` (voice.prefs.LivePrefs) lets the Settings page change the volume, devices, wake word and follow-up while Orbit runs;
        `lock` names what the command line chose and the page must not change ("wake", "follow_up", "mic", "speaker").
        `reminders` (reminders.ReminderStore) is what is said aloud when a reminder comes due.
        `alarm` (alarm.AlarmPlayer) makes a reminder or timer ring until you say "ok thanks" (without it, it is only said once).
        `live_decode` makes a stt_fast.LiveDecode for each hold-to-talk turn, so the speech is decoded while you are still talking."""
        self.agent, self.mic, self.transcriber, self.tts = agent, mic, transcriber, tts
        self.prefs, self.reminders, self.wake_enabled, self._locked = prefs, reminders, wake_enabled, lock
        self._wanted = None                        # settings read from the file and not applied yet (devices change between turns)
        self._announce: deque = deque()            # reminders due and waiting to be said
        self._reminders_at = 0.0
        self.alarm, self._ring = alarm, None       # the sound player, and the Ring that is ringing now
        self.alarm_ring_s, self.alarm_listen_s, self.alarm_max_s, self.alarm_settle_s = alarm_ring_s, alarm_listen_s, alarm_max_s, alarm_settle_s
        self.ui = ui or NullBridge()               # the sphere window, if one is connected (python -m orbit --voice --ipc)
        self._confirm_id = 0
        self.keys, self.wake, self.segmenter, self.check_vad = keys, wake, segmenter, check_vad
        self.follow_up, self.use_ptt, self.wake_name, self.out, self.verbose, self.monitor = follow_up, use_ptt, wake_name, out, verbose, monitor
        self.confirm = VoiceConfirm()
        self._chat_turn = False                    # was the reply being spoken a friend-mode chat? (it gets a longer follow-up window)
        self._flog = file_log or _file_logger().info
        self._clock = clock
        self.conversational, self.fillers, self.phraser = conversational, fillers, Phraser(rng)
        self.request_text = ""                      # what you said, kept so a failure can say what it heard
        self.aec, self.reference, self.barge_in_enabled = aec, reference, barge_in
        self._media_playing = media_check or media_is_playing
        self.aligner = ReferenceAligner(reference) if reference is not None else None
        self._barge = False                        # may the wake word interrupt the speech that is playing now?
        self._spec: tuple | None = None            # (clip, future): an early transcription started at a short pause
        self._aec_until = 0.0                      # keep cancelling for a moment after an interruption (the voice tail)
        self._filler_future: Future | None = None
        self._fillers_said = 0
        self._last_spoken = ""
        self._closing = False                      # the goodbye being spoken ends the conversation: no follow-up window after it
        self._follow_window = config.FOLLOW_UP_S
        self.st = VoiceState(clock=clock, log=self._on_transition, on_change=self._on_change)
        self.turn = TurnTimer(clock=clock)
        self.turns = 0
        self.killed = False
        self.mode = ""                       # how the current turn started: "wake", "ptt", "followup"
        self.preroll: deque = deque(maxlen=4)
        self.ptt_frames: list = []
        self.live_decode, self._live = live_decode, None   # decoding a hold-to-talk turn while it is recorded (see stt_fast.py)
        self.heard = False
        self.future: Future | None = None          # the current helper job: speech-to-text, or speech
        self.agent_future: Future | None = None    # the agent call; kept apart because a confirmation reuses `future` meanwhile
        self._pending_confirm = None
        self._confirm_step = ""
        self._confirm_started = 0.0
        self._speak_started = 0.0
        self._mic_paused = False

    # ---- small utilities ----
    def _on_transition(self, line: str) -> None:
        self._flog(line)
        if self.verbose:
            self.out("  " + line)

    def _on_change(self, old: State, new: State, reason: str) -> None:
        self.ui.state_changed(old, new, reason, self._follow_window if new is State.FOLLOW_UP else None)

    def _pause_mic(self) -> None:
        if not self._mic_paused:
            self.mic.pause()
            self._mic_paused = True
            self.preroll.clear()

    def _resume_mic(self) -> None:
        if self._mic_paused:
            self.mic.resume()
            self._mic_paused = False

    def _reset_listeners(self) -> None:
        if self.segmenter:
            self.segmenter.reset()
        if self.wake:
            self.wake.reset()

    def _idle(self, reason: str) -> None:
        self._closing = False
        self.st.to(State.IDLE, reason)
        self._resume_mic()
        self._reset_listeners()
        self.mode, self.heard, self._spec = "", False, None
        self._drop_live()

    def _drop_live(self) -> None:
        if self._live is not None:
            self._live.cancel()
            self._live = None

    @staticmethod
    def _text_of(future: Future):
        try:
            return future.result(), None
        except Exception as e:  # noqa: BLE001
            return None, e

    # ---- main loop ----
    def run(self, seconds: float | None = None) -> str:
        """Returns why it stopped: "killed", "timeout" (the --seconds limit) or "quit" (Ctrl+C)."""
        self.mic.start()
        if self.keys:
            self.keys.start()
        started = self._clock()
        reason = "quit"
        try:
            while True:
                if seconds is not None and self._clock() - started >= seconds:
                    reason = "timeout"
                    break
                self._handle_keys()
                self._follow_prefs()
                if self.killed:
                    reason = "killed"
                    break
                item = self.mic.read_timed(timeout=0.05)
                self._tick(*(item if item else (None, 0.0)))
                self.ui.tick()
        except KeyboardInterrupt:
            reason = "quit"
        finally:
            for cleanup in (self.tts.stop, self.alarm.stop if self.alarm else None, self.keys.stop if self.keys else None, self.mic.stop):
                try:                                   # a second Ctrl+C during shutdown must not print a traceback
                    if cleanup:
                        cleanup()
                except BaseException:  # noqa: BLE001
                    pass
        if self.monitor is not None:
            self.out("\n--- voice report ---\n" + self.monitor.report())
        return reason

    def _echo_free(self, frame, t: float):
        """The microphone frame with Orbit's own voice subtracted (when that is possible and helps), else the frame itself."""
        if self.aec is None or self.aligner is None:
            return frame
        return self.aec.process(frame, self.aligner.next(len(frame), t))

    def _tick(self, frame, t: float = 0.0) -> None:
        s = self.st.state
        if frame is not None:
            self.ui.mic_frame(frame)               # the face shows the level only while a voice is being heard
        if s is State.IDLE:
            self._tick_idle(frame)
        elif s in (State.LISTENING, State.FOLLOW_UP):
            if frame is not None and self._clock() < self._aec_until:
                frame = self._echo_free(frame, t)               # right after an interruption the tail of Orbit's voice is still around
            self._tick_listen(frame)
        elif s is State.TRANSCRIBING:
            self._tick_transcribing()
        elif s is State.THINKING:
            self._tick_thinking()
        elif s is State.CONFIRMING:
            self._tick_confirming(frame)
        elif s is State.SPEAKING:
            self._tick_speaking(frame, t)
        elif s is State.ALARM:
            self._tick_alarm(frame)

    # ---- hotkeys ----
    def _handle_keys(self) -> None:
        if not self.keys:
            return
        for e in self.keys.poll():
            if e.kind == "kill":
                self._kill()
                return
            if not self.use_ptt:
                continue
            if e.kind == "down":
                self._on_ptt_down()
            elif e.kind == "up":
                self._on_ptt_up(e)

    def _kill(self) -> None:
        self.out("[killed] kill switch pressed: microphone stopped")
        self.killed = True
        self.tts.stop()
        self._silence_alarm("killed")
        if self._pending_confirm:                      # a waiting risky action gets "no"
            self._pending_confirm["event"].set()
            self._pending_confirm = None
            self.ui.confirm_done(self._confirm_id, False, "killed")
        self.ui.killed()

    def _on_ptt_down(self) -> None:
        if self.st.is_(State.SPEAKING):
            self.tts.stop()                            # barge-in: the hotkey always interrupts speech immediately
            if self.future is not None:
                try:
                    self.future.result(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
            self._start_listening("ptt", "hotkey interrupted the speech")
        elif self.st.is_(State.ALARM):
            self._silence_alarm("key")                 # the talk key stops a ringing alarm at once, and what you say next is a normal request
            self._start_listening("ptt", "hotkey stopped the alarm")
        elif self.st.is_(State.IDLE, State.FOLLOW_UP):
            self._start_listening("ptt", "hotkey")
        else:
            self.out("  (busy: Orbit is working on your last request)")

    def _on_ptt_up(self, e) -> None:
        if self.mode != "ptt" or not self.st.is_(State.LISTENING):
            return
        if not e.valid:
            self.out(f"  (held {e.held_s:.2f}s: a tap or shortcut, not talking)")
            self._idle("key tap")
            return
        self._finish_ptt()

    def _finish_ptt(self) -> None:
        clip = np.concatenate(self.ptt_frames) if self.ptt_frames else np.zeros(0, np.int16)
        from .vad import speech_seconds

        if len(clip) == 0 or speech_seconds(self.check_vad, clip) < config.VAD_MIN_SPEECH_S:
            self.out(f"  (no speech heard in {len(clip) / config.SAMPLE_RATE:.1f}s of audio)")
            self._idle("no speech")
            return
        self.turn = TurnTimer(self._clock)
        self.turn.mark("speech_end")
        live, self._live = self._live, None
        pieces = live.pieces if live is not None else 0
        early = live.finish(clip) if live is not None else None      # None: nothing was cut while you talked, decode it whole
        if early is not None and self.verbose:
            self.out(f"  ({pieces} of {pieces + 1} pieces were decoded while you talked)")
        self._begin_transcribe(clip, early)

    # ---- listening ----
    def _start_listening(self, mode: str, reason: str) -> None:
        if mode == "wake" and self.st.is_(State.IDLE):
            self.st.to(State.WAKE_DETECTED, reason)
            reason = "listening for your request"
        keep = list(self.preroll) if mode == "ptt" else []
        self._resume_mic()
        self.mode, self.heard, self._spec = mode, False, None
        self.ptt_frames = keep
        self._drop_live()
        if mode == "ptt" and self.live_decode is not None:
            self._live = self.live_decode()
            for frame in keep:
                self._live.feed(frame)
        if self.segmenter:
            self.segmenter.reset()
        self.st.to(State.LISTENING, reason)
        self.out("[listening]")

    def _tick_idle(self, frame) -> None:
        if frame is not None:
            self.preroll.append(frame)
        if self._say_reminder() or self._start_window_request():
            return
        if frame is None or not self.wake or not self.wake_enabled:
            return
        result = self.wake.process(frame)
        if result.rejected:
            self.out(f'  (wake candidate rejected: the check heard "{result.heard}")')
        if result.triggered:
            self.out(f'[wake] "{self.wake_name.replace("_", " ")}" (score {result.score:.2f})')
            self._start_listening("wake", f'wake word "{self.wake_name}"')

    def _tick_listen(self, frame) -> None:
        if self.mode == "ptt":
            if frame is not None:
                self.ptt_frames.append(frame)
                if self._live is not None:
                    self._live.feed(frame)
                if len(self.ptt_frames) * config.FRAME_MS / 1000 >= config.PTT_MAX_S:
                    self.out(f"  (reached the {config.PTT_MAX_S:.0f}s limit)")
                    self._finish_ptt()
            return
        if frame is not None:
            for e in self.segmenter.feed(frame):
                if e.kind == "start":
                    self.heard = True
                    if self.st.is_(State.FOLLOW_UP):
                        self.st.to(State.LISTENING, "follow-up speech")
                        self.out("[listening] (follow-up, no wake word needed)")
                elif e.kind == "pause" and config.SPECULATIVE_STT:
                    self._spec = (e.clip, spawn(self.transcriber.transcribe, e.clip))      # a head start; discarded if you carry on
                elif e.kind == "end":
                    self.turn = TurnTimer(self._clock)
                    self.turn.mark("speech_end")
                    self._begin_transcribe(e.clip)
                    return
        if self.heard:
            return
        limit = self._follow_window if self.st.is_(State.FOLLOW_UP) else config.WAKE_NO_SPEECH_S
        if self.st.timed_out(limit):
            if self.st.is_(State.LISTENING):
                self.out(f"  (nothing heard for {limit:.1f}s after the wake word, going back to sleep)")
            self._idle("no speech heard")

    # ---- what changes while Orbit runs: the Settings page, reminders, the window's Play button ----
    def _follow_prefs(self) -> None:
        """Applies the Settings page's choices as they change. Between turns only: a microphone is never swapped mid-sentence."""
        if self.prefs is None:
            return
        new = self.prefs.poll()
        if new is not None:
            self._wanted = new
        if self._wanted is None or not self.st.is_(State.IDLE):
            return
        wanted, self._wanted = self._wanted, None
        self.apply_prefs(wanted)

    def apply_prefs(self, s) -> None:
        """Makes the loop follow settings `s` (a `settings.Settings`)."""
        want_wake = s.voice.wake_word and "wake" not in self._locked
        if "wake" not in self._locked and want_wake != self.wake_enabled and self.wake is not None:
            self.wake_enabled = want_wake
            if want_wake:
                self.wake.reset()
            self.out(f'Wake word {"on" if want_wake else "off"}: ' + (f'say "{self.wake_name.replace("_", " ")}".' if want_wake else "hold the talk key."))
        if "follow_up" not in self._locked and s.voice.keep_listening != self.follow_up:
            self.follow_up = s.voice.keep_listening
            self.out(f"Keep listening after a reply: {'on' if self.follow_up else 'off'}.")
        set_volume = getattr(self.tts, "set_volume", None)
        if set_volume is not None:
            set_volume(s.audio.volume / 100)
        if self.alarm is not None:
            self.alarm.set_volume(s.audio.volume / 100)
        if "mic" not in self._locked and hasattr(self.mic, "switch"):
            try:
                if self.mic.switch(s.audio.mic):
                    self._reset_listeners()
                    self.out(f"Microphone: {_device_name(getattr(self.mic, 'device', None))}")
            except Exception as e:  # noqa: BLE001 - not plugged in, or busy: the one that works keeps working
                self.out(f"  (cannot use the microphone '{s.audio.mic}': {e}; keeping the current one)")
        if "speaker" not in self._locked and hasattr(self.tts, "set_device"):
            try:
                if self.alarm is not None:
                    self.alarm.set_device(s.audio.speaker)
                if self.tts.set_device(s.audio.speaker):
                    self.out(f"Speaker: {s.audio.speaker or 'the Windows default'}")
            except Exception as e:  # noqa: BLE001
                self.out(f"  (cannot use the speaker '{s.audio.speaker}': {e}; keeping the current one)")

    def _say_reminder(self) -> bool:
        """Between turns: says a reminder that has come due. True if it started speaking."""
        if self.reminders is not None and not self._announce and self._clock() - self._reminders_at >= 1.0:
            self._reminders_at = self._clock()
            try:
                self._announce.extend(self.reminders.take_due(datetime.now()))
            except Exception as e:  # noqa: BLE001 - a reminders file problem must never stop the voice
                self._flog(f"reminders: {type(e).__name__}")
        if not self._announce:
            return False
        from .. import reminders as reminder_text

        reminder, late = self._announce.popleft()
        if self.alarm is not None and config.ALARM and not late:
            together = [reminder]                                      # everything else that came due at the same moment rings as one
            rest: deque = deque()
            for other, was_late in self._announce:
                if was_late:
                    rest.append((other, was_late))
                else:
                    together.append(other)
            self._announce = rest
            self._start_ring(together)
            return True
        said = reminder_text.announcement(reminder, late)
        self.out(f"[reminder] {said}")
        self.ui.reminder(reminder.id, reminder.title, said)
        self.ui.reply(said, said, "action", "calm")
        self.mode = "reminder"
        self._speak(said, "a reminder came due", emotion="calm", chat=False)
        return True

    # ---- a reminder or timer that rings until you say you have heard it ----
    def _start_ring(self, due: list) -> None:
        texts = [_announcement(r) for r in due]
        said = " ".join(texts)
        self.out(f"[ring] {said}")
        while self.ui.poll_dismiss() is not None:                      # a Stop pressed for an earlier ring must not end this one
            pass
        for r, text in zip(due, texts):
            self.ui.reminder(r.id, r.title, text, r.kind, True)
        self.ui.reply(said, said, "action", "calm")
        self.mode = "alarm"
        self._pause_mic()
        self.st.to(State.ALARM, "a reminder came due")
        self._ring = Ring(due, alarm_mod.sound_kind(r.kind for r in due), said, self._clock(), ids={r.id for r in due})
        self._ring_stretch()

    def _ring_stretch(self) -> None:
        """Rings: the sound plays for a stretch, with the microphone off (it would hear only the sound)."""
        ring = self._ring
        ring.step, ring.heard = "ring", False
        length = self.alarm_ring_s
        try:
            length = self.alarm.ring_seconds(ring.kind, self.alarm_ring_s)
            self.alarm.start(ring.kind)
        except Exception as e:  # noqa: BLE001 - no speaker, or the file is gone: it is still said and listened for, just not rung
            self._flog(f"alarm: cannot play ({type(e).__name__})")
            if not ring.warned:
                ring.warned = True
                self.out(f"  (cannot play the alarm sound: {e}; it will only be said)")
        ring.until = self._clock() + length

    def _ring_say(self) -> None:
        ring = self._ring
        ring.step = "say"
        self._set_emotion("calm")
        self.future = spawn(self.tts.speak, ring.text)

    def _ring_listen(self) -> None:
        """Quiet for a few seconds, microphone on: this is when "ok thanks" is heard."""
        ring, now = self._ring, self._clock()
        ring.step, ring.heard = "listen", False
        ring.settle, ring.until = now + self.alarm_settle_s, now + self.alarm_listen_s
        self._resume_mic()
        self._reset_listeners()

    def _tick_alarm(self, frame) -> None:
        ring = self._ring
        if ring is None:                                               # cannot happen; never be stuck if it does
            self._idle("no alarm ringing")
            return
        now = self._clock()
        asked = self.ui.poll_dismiss()
        if asked is not None and asked in ring.ids:
            self._finish_ring("ui")
            return
        if self.alarm_max_s and now - ring.started >= self.alarm_max_s:
            self.out(f"  (rang for {self.alarm_max_s:.0f}s and nobody answered: stopping)")
            self._finish_ring("timeout")
            return
        if ring.step == "ring":
            if now >= ring.until:
                self._stop_sound()
                if ring.cycle % config.ALARM_SAY_EVERY == 0:
                    self._ring_say()
                else:
                    self._ring_listen()
        elif ring.step == "say":
            if self.future.done():
                self._ring_listen()
        elif ring.step == "listen":
            self._ring_hear(frame, now)
        elif ring.step == "transcribe" and self.future.done():
            self._ring_judge()

    def _ring_hear(self, frame, now: float) -> None:
        ring = self._ring
        if not ring.heard and now >= ring.until:                       # nobody spoke: ring again
            ring.cycle += 1
            self._pause_mic()
            self._ring_stretch()
            return
        if frame is None or now < ring.settle:
            return
        for e in self.segmenter.feed(frame):
            if e.kind == "start":
                ring.heard = True
            elif e.kind == "end":
                self._pause_mic()
                ring.step = "transcribe"
                self.future = spawn(self.transcriber.transcribe, e.clip)
                return

    def _ring_judge(self) -> None:
        ring = self._ring
        result, error = self._text_of(self.future)
        text = "" if error is not None else result.text
        verdict = alarm_mod.judge(text)
        self.out(f'  you said "{text.strip()}" -> {verdict.kind}')
        if verdict.kind == "dismiss":
            self._finish_ring("voice", say="You're welcome." if verdict.thanks else None)
        elif verdict.kind == "snooze":
            self._snooze(verdict.minutes)
        else:                                                          # something else: it was not said to the alarm
            ring.cycle += 1
            self._ring_stretch()

    def _snooze(self, minutes: int) -> None:
        """Rings again later: the same reminders, due again `minutes` from now."""
        from .. import reminders as reminder_text

        add = getattr(self.reminders, "add", None)
        try:
            if add is None:
                raise OSError("there is no reminders file")
            now = datetime.now()
            for r in self._ring.due:
                add(r.title, now + timedelta(minutes=minutes), "none", now, r.kind)
            said = f"Okay, I'll ring again in {reminder_text.duration_text(minutes * 60)}."
        except Exception as e:  # noqa: BLE001
            self._flog(f"alarm: snooze failed ({type(e).__name__})")
            said = "Sorry, I couldn't snooze that."
        self._finish_ring("voice", say=said)

    def _stop_sound(self) -> None:
        if self.alarm is not None:
            try:
                self.alarm.stop()
            except Exception:  # noqa: BLE001
                pass

    def _silence_alarm(self, by: str) -> None:
        """Stops the sound (and any speech of it) and forgets the ring. The caller moves the state on."""
        ring, self._ring = self._ring, None
        if ring is None:
            return
        self._stop_sound()
        if ring.step == "say":
            self.tts.stop()
            try:
                self.future.result(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
        self.out(f"[ring] stopped ({by})")
        for rid in sorted(ring.ids):
            self.ui.alarm_stopped(rid, by)

    def _finish_ring(self, by: str, say: str | None = None) -> None:
        """You have answered (or it gave up): silence, then back to waiting, after one short answer if there is one."""
        self._silence_alarm(by)
        if say:
            self.mode = "reminder"
            self._speak(say, "the alarm stopped", emotion="calm", chat=False)
        else:
            self._idle("the alarm stopped")

    def _start_window_request(self) -> bool:
        """The window's Play button on a saved playlist: runs it as if you had asked, and tells you the result."""
        play = getattr(self.agent, "play_saved", None)
        if play is None:
            return False
        request = self.ui.poll_request()
        if request is None:
            return False
        self.out(f"[window] play a saved playlist ({request.id})")
        self.mode, self.heard, self._spec = "window", False, None
        self.turn = TurnTimer(self._clock)
        self.turn.mark("speech_end")
        self.request_text, self._fillers_said, self._filler_future = "play a saved playlist", 0, None
        self._pause_mic()
        self.st.to(State.THINKING, "a request from the window")
        self.out("[thinking]")
        self.agent_future = spawn(play, request.id)
        return True

    # ---- transcribing -> thinking ----
    def _begin_transcribe(self, clip: np.ndarray, early: Future | None = None) -> None:
        """`early`: the text already being finished by live decoding (hold-to-talk), used instead of decoding the clip again."""
        self._pause_mic()
        self.st.to(State.TRANSCRIBING, f"{len(clip) / config.SAMPLE_RATE:.1f}s of speech")
        spec, self._spec = self._spec, None
        if early is not None:
            self.future = early
        elif spec is not None and len(spec[0]) == len(clip) and np.array_equal(spec[0], clip):
            self.future = spec[1]                      # you really had stopped: the early transcription is already under way
            if self.verbose:
                self.out("  (using the early transcription)")
        else:
            self.future = spawn(self.transcriber.transcribe, clip)

    def _tick_transcribing(self) -> None:
        if not self.future.done():
            return
        result, error = self._text_of(self.future)
        if error is not None:
            self.out(f"  [stt] speech-to-text failed: {error}")
            self.ui.outcome(False)
            self._speak("Sorry, I couldn't understand that.", "speech-to-text failed")
            return
        text = strip_wake_phrase(result.text, self.wake_name) if self.mode == "wake" and self.wake else result.text
        self.turn.mark("text")
        if self.monitor is not None:
            self.monitor.add_timing("speech-to-text", result.seconds)
        goodbye = farewell.detect(text)
        if goodbye is not None:
            self._end_conversation(text, goodbye, result.seconds)
            return
        if not re.search(r"[A-Za-z0-9]", text) or is_noise(text):
            if self.mode == "followup":                    # nobody addressed Orbit here: say nothing, just stop listening
                self.out(f'  (ignored: "{text.strip()}" is not a request)')
                self._idle("noise in the follow-up window")
                return
            self.out("  (empty transcript: the agent will not run)")
            self._speak("Sorry, I didn't catch that.", "empty transcript")
            return
        self.out(f'You said: "{text}"   [speech-to-text {result.seconds:.1f}s]')
        self.ui.transcript("you", text)
        if config.LOG_TRANSCRIPTS:
            self._flog(f"transcript: {text}")
        self.request_text, self._fillers_said, self._filler_future = text, 0, None
        self.st.to(State.THINKING, "running the agent")
        self.out("[thinking]")
        self.agent_future = spawn(self.agent.handle, text)

    def _end_conversation(self, text: str, goodbye, seconds: float) -> None:
        """"nothing", "leave it", "let's talk later", "bye"...: say a short goodbye and go back to waiting for "hey orbit".
        The language model is not involved, and no follow-up window opens afterwards."""
        self.out(f'You said: "{text}"   [speech-to-text {seconds:.1f}s]')
        self.out(f"  (you ended the conversation: {goodbye.kind})")
        self.ui.transcript("you", text)
        if config.LOG_TRANSCRIPTS:
            self._flog(f"transcript: {text}")
        end = getattr(self.agent, "end_conversation", None)
        if end is not None:
            end()                                          # a vague "and then?" after this must not count as a chat follow-up
        said = farewell.reply(goodbye, self.phraser.rng)
        self.out(f"Orbit: {said}")
        self.ui.reply(said, said, "chat", "calm")
        self._speak(said, "you ended the conversation", emotion="calm", chat=False)
        self._closing = True                               # (set after _speak, which clears it for every other reply)

    def _tick_thinking(self) -> None:
        request = self.confirm.poll()
        if request:
            self._finish_filler()                  # never talk over a filler: the question must be heard clearly
            self._begin_confirm(*request)
            return
        if not self.agent_future.done():
            self._maybe_filler()
            return
        if self._filler_future is not None and not self._filler_future.done():
            return                                 # the agent is done, but let "One moment." finish first
        reply, error = self._text_of(self.agent_future)
        if error is not None:
            self._flog(f"agent error: {type(error).__name__}")
            reply = f"Sorry, something went wrong: {error}"
        self.turn.mark("reply")
        if self.monitor is not None and "text" in self.turn.marks:
            self.monitor.add_timing("agent", self.turn.marks["reply"] - self.turn.marks["text"])
        self.out(f"Orbit: {reply}")                       # the full reply stays in the CLI; a natural, short version is spoken
        kind = getattr(self.agent, "last_kind", "action")
        if error is not None or (kind == "action" and getattr(self.agent, "last_failed", False)):
            self.ui.outcome(False)                     # a flash before the reply is spoken: something did not work
        elif kind == "action":
            self.ui.outcome(True)
        if kind == "chat":
            # Friend mode: it is already written to be spoken, so it is not rewritten, and it is spoken (almost) whole, with feeling.
            emotion = getattr(self.agent, "last_emotion", "neutral")
            spoken = spoken_summary(strip_emoji(reply), config.CHAT_SPOKEN_MAX_SENTENCES, config.CHAT_SPOKEN_MAX_CHARS)
            self.out(f"  [feeling] {emotion}")
            self.ui.reply(reply, spoken, "chat", emotion)
            self._speak(spoken, "reply ready", emotion=emotion, chat=True)
            return
        said = self.phraser.say(reply, self.request_text) if self.conversational else reply
        spoken = spoken_summary(said)
        if spoken != reply:
            self.out(f"  [says] {spoken}")
        self.ui.reply(reply, spoken, "action", "neutral")
        self._speak(spoken, "reply ready")

    def _maybe_filler(self) -> None:
        """No dead air: after ~2.5 s say "One moment.", after ~8 s "Still working on it." (the mic is already off)."""
        if not self.fillers or self._fillers_said >= 2:
            return
        if getattr(self.agent, "actions_done", 0) > 0:
            return                                 # the click, scroll or move already happened: "Let me check." now would be wrong
        if self.st.elapsed < (config.FILLER_AFTER_S if self._fillers_said == 0 else config.FILLER_AGAIN_S):
            return
        if self._filler_future is not None and not self._filler_future.done():
            return
        text = self.phraser.filler(self._fillers_said)
        self._fillers_said += 1
        self.out(f"  [says] {text}")
        self._set_emotion("neutral")
        self._filler_future = spawn(self.tts.speak, text)

    def _finish_filler(self) -> None:
        if self._filler_future is not None:
            try:
                self._filler_future.result(timeout=4.0)
            except Exception:  # noqa: BLE001
                pass
            self._filler_future = None

    # ---- speaking ----
    def _can_barge_in(self, text: str) -> bool:
        """May the wake word interrupt this reply? Not if Orbit might say its own wake word, or cannot be paused."""
        return (self.barge_in_enabled and self.wake is not None and self.wake_enabled and bool(getattr(self.tts, "supports_pause", False))
                and "orbit" not in text.lower())

    def _set_emotion(self, emotion: str) -> None:
        set_it = getattr(self.tts, "set_emotion", None)
        if set_it is not None:
            set_it(emotion)

    def _speak(self, text: str, reason: str, emotion: str = "neutral", chat: bool = False) -> None:
        self._closing = False
        self._chat_turn = chat
        self._set_emotion(emotion)
        self._barge = self._can_barge_in(text)
        if self._barge:
            self._resume_mic()                         # stay open: you may interrupt with the wake word
            self._reset_listeners()
            if self.reference is not None:
                self.reference.reset()                 # a new utterance for the echo canceller (it keeps what it learned)
            if self.aec is not None:
                self.aec.reset_history()
        else:
            self._pause_mic()                          # half-duplex: Orbit never hears itself
        self.st.to(State.SPEAKING, reason)
        self.out("[speaking]")
        self._speak_started = self._clock()
        self._last_spoken = text
        self.future = spawn(self.tts.speak, text)

    def _tick_speaking(self, frame=None, t: float = 0.0) -> None:
        if self._barge and frame is not None and not self.future.done():
            result = self.wake.process(self._echo_free(frame, t), on_candidate=self.tts.pause)   # mute at once, verify after
            if result.rejected:
                self.tts.resume()                                  # a false alarm: carry on talking
                self.out(f'  (that was not the wake word: the check heard "{result.heard}"; carrying on)')
            elif result.triggered:
                self._barge_in()
                return
        if not self.future.done():
            return
        stats, error = self._text_of(self.future)
        if error is not None:
            self.out(f"  [tts] could not speak: {error}")
        elif stats is not None and stats.first_audio_s is not None:
            self.turn.mark("first_audio", self._speak_started + stats.first_audio_s)
        if "speech_end" in self.turn.marks and "first_audio" in self.turn.marks:
            self.out("  [turn] " + self.turn.summary(config.VAD_END_SILENCE_S if self.mode != "ptt" else 0.0))
            if self.monitor is not None:
                self._record_turn_timings()
        self.turns += 1
        if self._closing:
            self._idle("conversation ended by you: waiting for the wake word")
        elif self.mode in ("reminder", "window"):                   # nobody was talking: no follow-up window for an announcement or a button
            self._idle("finished")
        elif self.follow_up and not config.FOLLOW_UP_WHILE_MEDIA and self._media_check():
            self.out('  (music is playing, so no follow-up without the wake word: say "'
                     + self.wake_name.replace("_", " ") + '" or hold the key)')
            self._idle("media is playing")
        elif self.follow_up:
            self._resume_mic()
            self._reset_listeners()
            self.mode, self.heard = "followup", False
            asked = self._last_spoken.rstrip().endswith("?")          # Orbit asked a question: give a real chance to answer it
            self._follow_window = config.FOLLOW_UP_QUESTION_S if asked else config.FOLLOW_UP_S
            if self._chat_turn:                                       # a conversation: stay open at least as long as for a question
                self._follow_window = max(self._follow_window, config.FOLLOW_UP_CHAT_S)
            self.st.to(State.FOLLOW_UP, f"listening {self._follow_window:.0f}s for " + ("your answer" if asked else "a follow-up") + ", no wake word needed")
            if asked:
                self.out("[listening] (Orbit asked a question: just answer, no wake word needed)")
        else:
            self._idle("finished")

    def _record_turn_timings(self) -> None:
        """Feed the four numbers that matter for latency into the monitor's end-of-run report (median / p90 each).
        ("agent" is recorded as soon as the reply is ready; "speech-to-text" is the engine's own compute time, which a speculative
        transcription can make shorter than what the user waited, so the wall-clock "transcribe" below is the one to compare.)"""
        for name, a, b in (("transcribe (end of speech -> text)", "speech_end", "text"),
                           ("reply -> first audio", "reply", "first_audio"),
                           ("end of speech -> first spoken word", "speech_end", "first_audio")):
            gap = self.turn.gap(a, b)
            if gap is not None:
                self.monitor.add_timing(name, gap)
        total = self.turn.gap("speech_end", "first_audio")
        if total is not None and self.mode != "ptt":
            self.monitor.add_timing("last word -> first spoken word (incl. end-of-speech wait)", total + config.VAD_END_SILENCE_S)

    def _media_check(self) -> bool:
        """Is media playing? Asked in a helper thread with a short deadline, so a slow answer never stalls the loop."""
        try:
            return bool(spawn(self._media_playing).result(timeout=0.6))
        except Exception:  # noqa: BLE001
            return False

    def _barge_in(self) -> None:
        """You said the wake word while Orbit was talking: stop talking and listen to the new request."""
        self.out('[interrupted] heard "' + self.wake_name.replace("_", " ") + '" while speaking')
        self.tts.stop()
        try:
            self.future.result(timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
        self._aec_until = self._clock() + 0.7
        self._start_listening("wake", "wake word interrupted the speech")

    # ---- confirming a risky action by voice ----
    def _begin_confirm(self, question: str, reply: dict) -> None:
        self._pending_confirm = reply
        self._confirm_id = self.ui.confirm_asked(question, describe_action(question)) or 0
        spoken = f"Do you want me to {describe_action(question)}? Say yes or no."
        self.out(f"[confirm] {question}\n  Orbit asks: {spoken}")
        self._pause_mic()
        self.st.to(State.CONFIRMING, "risky action needs a yes")
        self._confirm_step = "ask"
        self._set_emotion("neutral")
        self.future = spawn(self.tts.speak, spoken)

    def _tick_confirming(self, frame) -> None:
        step = self._confirm_step
        shown = self.ui.poll_answer()
        if shown is not None and shown.id == self._confirm_id and self._pending_confirm is not None:
            self.tts.stop()                            # answered on screen: stop asking out loud
            if self.future is not None:
                try:
                    self.future.result(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
            self.out(f"  answered on screen -> {'YES' if shown.yes else 'NO'}")
            self._answer(shown.yes, "ui")
            return
        if step == "ask":
            if self.future.done():
                self._resume_mic()
                self._reset_listeners()
                self._confirm_step, self._confirm_started, self.heard = "listen", self._clock(), False
                self.out("[listening] say yes or no")
        elif step == "listen":
            if frame is not None:
                for e in self.segmenter.feed(frame):
                    if e.kind == "start":
                        self.heard = True
                    elif e.kind == "end":
                        self._pause_mic()
                        self._confirm_step = "transcribe"
                        self.future = spawn(self.transcriber.transcribe, e.clip)
                        return
            if not self.segmenter.speaking and self._clock() - self._confirm_started > config.CONFIRM_TIMEOUT_S:
                self.out("  (no answer heard: treating that as no)")
                self._answer(False, "timeout")
        elif step == "transcribe" and self.future.done():
            result, error = self._text_of(self.future)
            text = "" if error is not None else result.text
            yes = parse_yes_no(text)
            self.out(f'  you said "{text}" -> {"YES" if yes else "NO"}')
            self._answer(yes, "voice")

    def _answer(self, yes: bool, by: str = "voice") -> None:
        reply, self._pending_confirm = self._pending_confirm, None
        if reply is not None:
            reply["answer"] = yes
            reply["event"].set()
            self.ui.confirm_done(self._confirm_id, yes, by)
        self._pause_mic()
        self.st.to(State.THINKING, f"confirmation: {'yes' if yes else 'no'}")
        self.out("[thinking]")


# ---------------------------------------------------------------- building and running it
class EchoAgent:
    """Dev stand-in for the real agent: repeats the request. Never touches the PC."""

    def __init__(self):
        self.calls: list[str] = []
        self.safety = type("S", (), {"confirm": None})()

    def handle(self, text: str) -> str:
        self.calls.append(text)
        close = re.match(r"close (.+?)[.!?]?$", text.strip(), re.I)
        if close and self.safety.confirm:                 # pretend "close X" is risky, to demo the spoken yes / no
            title = close.group(1)
            if self.safety.confirm(f"os_close_window({{'title': {title!r}}}) is a risky action. Allow?"):
                return f"Okay, I would close {title}."
            return f"Okay, I left {title} alone."
        return f"Okay, I would do this: {text}"


def privacy_note(transcriber, tts, llm_description: str = "") -> str:
    """One honest line about what leaves this PC. The wake word, voice-activity detection and echo cancellation always stay local."""
    sends = []
    if "cloud" in llm_description:
        sends.append("your requests go to Groq (language model)")
    if not sends:
        return "Speech-to-text, the wake word and the voice all run on this PC. Nothing is recorded or sent anywhere."
    return ("Cloud mode: " + "; ".join(sends) + ". Your voice never leaves this PC: speech-to-text, the wake word, the voice, "
            "silence detection and echo cancellation all run here, and audio is never saved by Orbit.")


def _make_alarm(device, volume: float, out):
    """The player that rings a reminder or timer, on the speaker the page chose (the Windows default if that one is not there).
    None if it cannot be made: reminders are then only said once."""
    try:
        try:
            return alarm_mod.AlarmPlayer(device, volume)
        except ValueError as e:
            out(f"  (cannot ring on the speaker '{device}': {e}; using the Windows default)")
            return alarm_mod.AlarmPlayer(None, volume)
    except Exception as e:  # noqa: BLE001
        out(f"  (alarm sounds unavailable: {e}; reminders and timers will only be said once)")
        return None


def run_voice(agent, *, device: str | None = config.MIC_DEVICE, wake_enabled: bool = True, follow_up: bool | None = None,
              seconds: float | None = None, wav: str | None = None, verbose: bool = False, out=print, ui=None) -> str:
    """Builds everything and runs the loop. Returns "killed" | "quit" | "timeout", or "failed" if voice cannot start
    (no mic, a missing model): the caller then carries on in text mode, so a voice problem never crashes the REPL.

    What the Settings page chooses (microphone, speaker, speaking volume, wake word, keep listening) is used from the start and followed
    while Orbit runs. A choice made on the command line (`--mic`, `--no-wake`, `--no-follow-up`) or in the environment wins over the page."""
    from ..reminders import ReminderStore
    from .audio_io import FileMic, MicStream, device_label
    from .hotkey import Hotkeys
    from .monitor import Monitor, lower_priority
    from .prefs import LivePrefs
    from .stt import build_transcriber
    from .tts import make_tts
    from .vad import SileroVAD, SpeechSegmenter

    prefs = LivePrefs()
    chosen = prefs.current
    locked = frozenset(name for name, taken in (("wake", not wake_enabled), ("follow_up", follow_up is not None),
                                                 ("mic", bool(device)), ("speaker", bool(config.SPEAKER_DEVICE))) if taken)
    try:
        lower_priority()
        out("Loading voice (speech-to-text, wake word, voice) ...")
        transcriber = build_transcriber(log=out)
        out(f"  speech-to-text: {getattr(transcriber, 'name', 'local')} {getattr(transcriber, 'model_name', '')} "
            f"(loaded in {getattr(transcriber, 'load_s', 0.0):.1f}s, on this PC)")
        live_decode = None
        if config.LIVE_DECODE and getattr(transcriber, "live_chunking", False):
            from .stt_fast import LiveDecode

            live_vad = SileroVAD()                                            # its own: the VAD is stateful (reset for every turn)
            live_decode = lambda: LiveDecode(transcriber, live_vad)           # noqa: E731 - one session per hold-to-talk turn
        segmenter = SpeechSegmenter(SileroVAD(), pause_s=config.SPECULATIVE_PAUSE_S if config.SPECULATIVE_STT else None)
        check_vad = SileroVAD()
        wake = None
        if "wake" not in locked:
            try:
                from .wakeword import WakeWord

                wake = WakeWord(config.WAKEWORD_MODEL)      # loaded even when the page has the wake word off: switching it on needs no restart
            except FileNotFoundError as e:
                out(f"  wake word unavailable ({str(e).splitlines()[0]}); hold-to-talk only")
        try:
            mic = FileMic(wav) if wav else MicStream(device or chosen.audio.mic or None)
        except ValueError as e:                              # the microphone chosen on the page is not there now: the default one
            out(f"  (cannot use the microphone '{chosen.audio.mic}': {e}; using the Windows default)")
            mic = MicStream(None)
        keys = Hotkeys()
        reference = ReferenceTimeline() if config.AEC and config.BARGE_IN else None
        try:
            tts = make_tts(device=config.SPEAKER_DEVICE or chosen.audio.speaker or None, log=out, reference=reference)
        except ValueError as e:
            out(f"  (cannot use the speaker '{chosen.audio.speaker}': {e}; using the Windows default)")
            tts = make_tts(log=out, reference=reference)
        if hasattr(tts, "set_volume"):
            tts.set_volume(chosen.audio.volume / 100)
        alarm = _make_alarm(config.SPEAKER_DEVICE or chosen.audio.speaker or None, chosen.audio.volume / 100, out) if config.ALARM else None
    except Exception as e:  # noqa: BLE001 - any setup problem: fall back to text
        out(f"Voice could not start ({type(e).__name__}: {e}). Falling back to text mode.")
        return "failed"

    use_follow_up = follow_up if follow_up is not None else (config.FOLLOW_UP and chosen.voice.keep_listening)
    loop = VoiceLoop(agent, mic, transcriber, tts, keys, wake, segmenter, check_vad,
                     follow_up=use_follow_up, out=out, verbose=verbose, monitor=Monitor(),
                     aec=EchoCanceller() if reference is not None else None, reference=reference, ui=ui,
                     prefs=prefs, reminders=ReminderStore(), wake_enabled=wake is not None and chosen.voice.wake_word, lock=locked, alarm=alarm,
                     live_decode=live_decode)
    agent.safety.confirm = loop.confirm              # risky actions are confirmed by voice from now on
    if ui is not None and hasattr(tts, "set_audio_listener"):
        tts.set_audio_listener(ui.audio_block)         # the face shows Orbit's voice level while it speaks
    if ui is not None and alarm is not None:
        alarm.on_audio = ui.audio_block                # ...and pulses with a ringing alarm
    loop.monitor.start()
    llm = getattr(agent, "llm", None)
    if llm is not None and hasattr(llm, "warm_up"):
        threading.Thread(target=_keep_model_warm, args=(llm, out), daemon=True, name="llm-warm").start()
    triggers = [f"hold {keys.ptt_name}"] + ([f'say "{config.WAKEWORD_MODEL.replace("_", " ")}"'] if loop.wake_enabled else [])
    out(f"Mic: {getattr(mic, 'label', None) or device_label(mic.device)}")
    out(f"Voice is on. Start a turn: {' or '.join(triggers)}.  Kill switch: {config.KILL_COMBO}.  Ctrl+C quits.")
    out(privacy_note(transcriber, tts, getattr(agent, "llm_description", "")))
    if loop._can_barge_in("ok"):
        out(f'You can interrupt Orbit by saying "{config.WAKEWORD_MODEL.replace("_", " ")}" while it talks, or by holding {keys.ptt_name}.\n')
    else:
        out("")
    try:
        return loop.run(seconds)
    except KeyboardInterrupt:
        return "quit"
    except Exception as e:  # noqa: BLE001 - e.g. the microphone is busy or unplugged: carry on in text mode
        out(f"Voice stopped ({type(e).__name__}: {e}). Falling back to text mode.")
        return "failed"
    finally:
        loop.monitor.stop()


def main(argv: list[str] | None = None) -> int:
    import sys

    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="python -m orbit.voice.loop", description="Voice loop dev bench (the real entry point is: python -m orbit --voice)")
    p.add_argument("--echo-agent", action="store_true", help="use a stand-in agent that only repeats your request (touches nothing)")
    p.add_argument("--wav", help="replay this 16 kHz mono WAV in real time instead of using the microphone")
    p.add_argument("--device", default=config.MIC_DEVICE)
    p.add_argument("--seconds", type=float)
    p.add_argument("--no-wake", action="store_true")
    p.add_argument("--no-follow-up", action="store_true")
    p.add_argument("--verbose", action="store_true", help="also print every state transition")
    args = p.parse_args(argv)
    if not args.echo_agent:
        p.error("this bench needs --echo-agent; use `python -m orbit --voice` for the real agent")
    result = run_voice(EchoAgent(), device=args.device, wake_enabled=not args.no_wake, follow_up=not args.no_follow_up,
                       seconds=args.seconds, wav=args.wav, verbose=args.verbose)
    return 1 if result == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

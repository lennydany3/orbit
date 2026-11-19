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
from logging.handlers import RotatingFileHandler

import numpy as np

from . import config
from .aec import EchoCanceller, ReferenceAligner, ReferenceTimeline
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


# ---------------------------------------------------------------- the loop
class VoiceLoop:
    def __init__(self, agent, mic, transcriber, tts, keys=None, wake=None, segmenter=None, check_vad=None, *,
                 follow_up: bool = config.FOLLOW_UP, use_ptt: bool = True, wake_name: str = config.WAKEWORD_MODEL,
                 out=print, verbose: bool = False, monitor=None, file_log=None, clock=time.monotonic,
                 conversational: bool = config.CONVERSATIONAL, fillers: bool = config.FILLERS, rng: random.Random | None = None,
                 aec: EchoCanceller | None = None, reference: ReferenceTimeline | None = None, barge_in: bool = config.BARGE_IN,
                 media_check=None):
        self.agent, self.mic, self.transcriber, self.tts = agent, mic, transcriber, tts
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
        self._follow_window = config.FOLLOW_UP_S
        self.st = VoiceState(clock=clock, log=self._on_transition)
        self.turn = TurnTimer(clock=clock)
        self.turns = 0
        self.killed = False
        self.mode = ""                       # how the current turn started: "wake", "ptt", "followup"
        self.preroll: deque = deque(maxlen=4)
        self.ptt_frames: list = []
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
        self.st.to(State.IDLE, reason)
        self._resume_mic()
        self._reset_listeners()
        self.mode, self.heard, self._spec = "", False, None

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
                if self.killed:
                    reason = "killed"
                    break
                item = self.mic.read_timed(timeout=0.05)
                self._tick(*(item if item else (None, 0.0)))
        except KeyboardInterrupt:
            reason = "quit"
        finally:
            for cleanup in (self.tts.stop, self.keys.stop if self.keys else None, self.mic.stop):
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
        if self._pending_confirm:                      # a waiting risky action gets "no"
            self._pending_confirm["event"].set()
            self._pending_confirm = None

    def _on_ptt_down(self) -> None:
        if self.st.is_(State.SPEAKING):
            self.tts.stop()                            # barge-in: the hotkey always interrupts speech immediately
            if self.future is not None:
                try:
                    self.future.result(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
            self._start_listening("ptt", "hotkey interrupted the speech")
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
        self._begin_transcribe(clip)

    # ---- listening ----
    def _start_listening(self, mode: str, reason: str) -> None:
        if mode == "wake" and self.st.is_(State.IDLE):
            self.st.to(State.WAKE_DETECTED, reason)
            reason = "listening for your request"
        keep = list(self.preroll) if mode == "ptt" else []
        self._resume_mic()
        self.mode, self.heard, self._spec = mode, False, None
        self.ptt_frames = keep
        if self.segmenter:
            self.segmenter.reset()
        self.st.to(State.LISTENING, reason)
        self.out("[listening]")

    def _tick_idle(self, frame) -> None:
        if frame is None:
            return
        self.preroll.append(frame)
        if not self.wake:
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

    # ---- transcribing -> thinking ----
    def _begin_transcribe(self, clip: np.ndarray) -> None:
        self._pause_mic()
        self.st.to(State.TRANSCRIBING, f"{len(clip) / config.SAMPLE_RATE:.1f}s of speech")
        spec, self._spec = self._spec, None
        if spec is not None and len(spec[0]) == len(clip) and np.array_equal(spec[0], clip):
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
            self._speak("Sorry, I couldn't understand that.", "speech-to-text failed")
            return
        text = strip_wake_phrase(result.text, self.wake_name) if self.mode == "wake" and self.wake else result.text
        self.turn.mark("text")
        if self.monitor is not None:
            self.monitor.add_timing("speech-to-text", result.seconds)
        if not re.search(r"[A-Za-z0-9]", text) or is_noise(text):
            if self.mode == "followup":                    # nobody addressed Orbit here: say nothing, just stop listening
                self.out(f'  (ignored: "{text.strip()}" is not a request)')
                self._idle("noise in the follow-up window")
                return
            self.out("  (empty transcript: the agent will not run)")
            self._speak("Sorry, I didn't catch that.", "empty transcript")
            return
        self.out(f'You said: "{text}"   [speech-to-text {result.seconds:.1f}s]')
        if config.LOG_TRANSCRIPTS:
            self._flog(f"transcript: {text}")
        self.request_text, self._fillers_said, self._filler_future = text, 0, None
        self.st.to(State.THINKING, "running the agent")
        self.out("[thinking]")
        self.agent_future = spawn(self.agent.handle, text)

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
        if getattr(self.agent, "last_kind", "action") == "chat":
            # Friend mode: it is already written to be spoken, so it is not rewritten, and it is spoken (almost) whole, with feeling.
            emotion = getattr(self.agent, "last_emotion", "neutral")
            spoken = spoken_summary(strip_emoji(reply), config.CHAT_SPOKEN_MAX_SENTENCES, config.CHAT_SPOKEN_MAX_CHARS)
            self.out(f"  [feeling] {emotion}")
            self._speak(spoken, "reply ready", emotion=emotion, chat=True)
            return
        said = self.phraser.say(reply, self.request_text) if self.conversational else reply
        spoken = spoken_summary(said)
        if spoken != reply:
            self.out(f"  [says] {spoken}")
        self._speak(spoken, "reply ready")

    def _maybe_filler(self) -> None:
        """No dead air: after ~2.5 s say "One moment.", after ~8 s "Still working on it." (the mic is already off)."""
        if not self.fillers or self._fillers_said >= 2:
            return
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
        return (self.barge_in_enabled and self.wake is not None and bool(getattr(self.tts, "supports_pause", False))
                and "orbit" not in text.lower())

    def _set_emotion(self, emotion: str) -> None:
        set_it = getattr(self.tts, "set_emotion", None)
        if set_it is not None:
            set_it(emotion)

    def _speak(self, text: str, reason: str, emotion: str = "neutral", chat: bool = False) -> None:
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
                self.monitor.add_timing("end of speech -> first spoken word", self.turn.gap("speech_end", "first_audio"))
        self.turns += 1
        if self.follow_up and not config.FOLLOW_UP_WHILE_MEDIA and self._media_check():
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
        spoken = f"Do you want me to {describe_action(question)}? Say yes or no."
        self.out(f"[confirm] {question}\n  Orbit asks: {spoken}")
        self._pause_mic()
        self.st.to(State.CONFIRMING, "risky action needs a yes")
        self._confirm_step = "ask"
        self._set_emotion("neutral")
        self.future = spawn(self.tts.speak, spoken)

    def _tick_confirming(self, frame) -> None:
        step = self._confirm_step
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
                self._answer(False)
        elif step == "transcribe" and self.future.done():
            result, error = self._text_of(self.future)
            text = "" if error is not None else result.text
            yes = parse_yes_no(text)
            self.out(f'  you said "{text}" -> {"YES" if yes else "NO"}')
            self._answer(yes)

    def _answer(self, yes: bool) -> None:
        reply, self._pending_confirm = self._pending_confirm, None
        if reply is not None:
            reply["answer"] = yes
            reply["event"].set()
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
    if getattr(transcriber, "name", "") == "sarvam":
        sends.append("what you say after the wake word goes to Sarvam (speech-to-text)")
    if any(getattr(e, "name", "") == "sarvam" for e in getattr(tts, "engines", [])):
        sends.append("Orbit's replies go to Sarvam (text-to-speech)")
    if "cloud" in llm_description:
        sends.append("your requests go to Groq (language model)")
    if not sends:
        return "Speech-to-text, the wake word and the voice all run on this PC. Nothing is recorded or sent anywhere."
    return ("Cloud mode: " + "; ".join(sends) + ". The wake word, silence detection and echo cancellation stay on this PC, "
            "and audio is never saved by Orbit.")


def run_voice(agent, *, device: str | None = config.MIC_DEVICE, wake_enabled: bool = True, follow_up: bool | None = None,
              seconds: float | None = None, wav: str | None = None, verbose: bool = False, out=print) -> str:
    """Builds everything and runs the loop. Returns "killed" | "quit" | "timeout", or "failed" if voice cannot start
    (no mic, a missing model): the caller then carries on in text mode, so a voice problem never crashes the REPL."""
    from .audio_io import FileMic, MicStream, device_label
    from .hotkey import Hotkeys
    from .monitor import Monitor, lower_priority
    from .cloud import build_transcriber
    from .tts import make_tts
    from .vad import SileroVAD, SpeechSegmenter

    try:
        lower_priority()
        out("Loading voice (speech-to-text, wake word, voice) ...")
        transcriber = build_transcriber(log=out)
        segmenter = SpeechSegmenter(SileroVAD(), pause_s=config.SPECULATIVE_PAUSE_S if config.SPECULATIVE_STT else None)
        check_vad = SileroVAD()
        wake = None
        if wake_enabled:
            try:
                from .wakeword import WakeWord

                wake = WakeWord(config.WAKEWORD_MODEL)
            except FileNotFoundError as e:
                out(f"  wake word unavailable ({str(e).splitlines()[0]}); hold-to-talk only")
        mic = FileMic(wav) if wav else MicStream(device)
        keys = Hotkeys()
        reference = ReferenceTimeline() if config.AEC and config.BARGE_IN else None
        tts = make_tts(log=out, reference=reference)
    except Exception as e:  # noqa: BLE001 - any setup problem: fall back to text
        out(f"Voice could not start ({type(e).__name__}: {e}). Falling back to text mode.")
        return "failed"

    loop = VoiceLoop(agent, mic, transcriber, tts, keys, wake, segmenter, check_vad,
                     follow_up=config.FOLLOW_UP if follow_up is None else follow_up, out=out, verbose=verbose, monitor=Monitor(),
                     aec=EchoCanceller() if reference is not None else None, reference=reference)
    agent.safety.confirm = loop.confirm              # risky actions are confirmed by voice from now on
    loop.monitor.start()
    llm = getattr(agent, "llm", None)
    if llm is not None and hasattr(llm, "warm_up"):
        threading.Thread(target=_keep_model_warm, args=(llm, out), daemon=True, name="llm-warm").start()
    triggers = [f"hold {config.PTT_KEY}"] + ([f'say "{config.WAKEWORD_MODEL.replace("_", " ")}"'] if wake else [])
    out(f"Mic: {getattr(mic, 'label', None) or device_label(mic.device)}")
    out(f"Voice is on. Start a turn: {' or '.join(triggers)}.  Kill switch: {config.KILL_COMBO}.  Ctrl+C quits.")
    out(privacy_note(transcriber, tts, getattr(agent, "llm_description", "")))
    if loop._can_barge_in("ok"):
        out(f'You can interrupt Orbit by saying "{config.WAKEWORD_MODEL.replace("_", " ")}" while it talks, or by holding {config.PTT_KEY}.\n')
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

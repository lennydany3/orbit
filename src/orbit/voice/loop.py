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
from .phrasing import Phraser
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
                 conversational: bool = config.CONVERSATIONAL, fillers: bool = config.FILLERS, rng: random.Random | None = None):
        self.agent, self.mic, self.transcriber, self.tts = agent, mic, transcriber, tts
        self.keys, self.wake, self.segmenter, self.check_vad = keys, wake, segmenter, check_vad
        self.follow_up, self.use_ptt, self.wake_name, self.out, self.verbose, self.monitor = follow_up, use_ptt, wake_name, out, verbose, monitor
        self.confirm = VoiceConfirm()
        self._flog = file_log or _file_logger().info
        self._clock = clock
        self.conversational, self.fillers, self.phraser = conversational, fillers, Phraser(rng)
        self.request_text = ""                      # what you said, kept so a failure can say what it heard
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
        self.mode, self.heard = "", False

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
                self._tick(self.mic.read(timeout=0.05))
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

    def _tick(self, frame) -> None:
        s = self.st.state
        if s is State.IDLE:
            self._tick_idle(frame)
        elif s in (State.LISTENING, State.FOLLOW_UP):
            self._tick_listen(frame)
        elif s is State.TRANSCRIBING:
            self._tick_transcribing()
        elif s is State.THINKING:
            self._tick_thinking()
        elif s is State.CONFIRMING:
            self._tick_confirming(frame)
        elif s is State.SPEAKING:
            self._tick_speaking()

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
        self.mode, self.heard = mode, False
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
        if not re.search(r"[A-Za-z0-9]", text):
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
        self._filler_future = spawn(self.tts.speak, text)

    def _finish_filler(self) -> None:
        if self._filler_future is not None:
            try:
                self._filler_future.result(timeout=4.0)
            except Exception:  # noqa: BLE001
                pass
            self._filler_future = None

    # ---- speaking ----
    def _speak(self, text: str, reason: str) -> None:
        self._pause_mic()
        self.st.to(State.SPEAKING, reason)
        self.out("[speaking]")
        self._speak_started = self._clock()
        self._last_spoken = text
        self.future = spawn(self.tts.speak, text)

    def _tick_speaking(self) -> None:
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
        if self.follow_up:
            self._resume_mic()
            self._reset_listeners()
            self.mode, self.heard = "followup", False
            asked = self._last_spoken.rstrip().endswith("?")          # Orbit asked a question: give a real chance to answer it
            self._follow_window = config.FOLLOW_UP_QUESTION_S if asked else config.FOLLOW_UP_S
            self.st.to(State.FOLLOW_UP, f"listening {self._follow_window:.0f}s for " + ("your answer" if asked else "a follow-up") + ", no wake word needed")
            if asked:
                self.out("[listening] (Orbit asked a question: just answer, no wake word needed)")
        else:
            self._idle("finished")

    # ---- confirming a risky action by voice ----
    def _begin_confirm(self, question: str, reply: dict) -> None:
        self._pending_confirm = reply
        spoken = f"Do you want me to {describe_action(question)}? Say yes or no."
        self.out(f"[confirm] {question}\n  Orbit asks: {spoken}")
        self._pause_mic()
        self.st.to(State.CONFIRMING, "risky action needs a yes")
        self._confirm_step = "ask"
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


def run_voice(agent, *, device: str | None = config.MIC_DEVICE, wake_enabled: bool = True, follow_up: bool | None = None,
              seconds: float | None = None, wav: str | None = None, verbose: bool = False, out=print) -> str:
    """Builds everything and runs the loop. Returns "killed" | "quit" | "timeout", or "failed" if voice cannot start
    (no mic, a missing model): the caller then carries on in text mode, so a voice problem never crashes the REPL."""
    from .audio_io import FileMic, MicStream, device_label
    from .hotkey import Hotkeys
    from .monitor import Monitor, lower_priority
    from .stt import Transcriber
    from .tts import make_tts
    from .vad import SileroVAD, SpeechSegmenter

    try:
        lower_priority()
        out("Loading voice (speech-to-text, wake word, voice) ...")
        transcriber = Transcriber()
        segmenter, check_vad = SpeechSegmenter(SileroVAD()), SileroVAD()
        wake = None
        if wake_enabled:
            try:
                from .wakeword import WakeWord

                wake = WakeWord(config.WAKEWORD_MODEL)
            except FileNotFoundError as e:
                out(f"  wake word unavailable ({str(e).splitlines()[0]}); hold-to-talk only")
        mic = FileMic(wav) if wav else MicStream(device)
        keys = Hotkeys()
        tts = make_tts(log=out)
    except Exception as e:  # noqa: BLE001 - any setup problem: fall back to text
        out(f"Voice could not start ({type(e).__name__}: {e}). Falling back to text mode.")
        return "failed"

    loop = VoiceLoop(agent, mic, transcriber, tts, keys, wake, segmenter, check_vad,
                     follow_up=config.FOLLOW_UP if follow_up is None else follow_up, out=out, verbose=verbose, monitor=Monitor())
    agent.safety.confirm = loop.confirm              # risky actions are confirmed by voice from now on
    loop.monitor.start()
    triggers = [f"hold {config.PTT_KEY}"] + ([f'say "{config.WAKEWORD_MODEL.replace("_", " ")}"'] if wake else [])
    out(f"Mic: {getattr(mic, 'label', None) or device_label(mic.device)}")
    out(f"Voice is on. Start a turn: {' or '.join(triggers)}.  Kill switch: {config.KILL_COMBO}.  Ctrl+C quits.")
    out("Speech-to-text, the wake word and the voice all run on this PC. Nothing is recorded or sent anywhere.\n")
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

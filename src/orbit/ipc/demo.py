"""A pretend voice turn for working on the face without a microphone, a model or a speaker.

    python -m orbit.ipc.demo               # one turn after another, forever
    python -m orbit.ipc.demo --confirm     # each turn ends with a "close Notepad?" question to answer in the window

It drives the same `UiBridge` the real voice loop does, so what a face sees here is what it sees from Orbit.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Callable

import numpy as np

from ..voice.state import State
from .. import config
from . import protocol
from .bridge import UiBridge

_SAMPLE_RATE = 16_000


def pcm_at(level: float, db_range: tuple[float, float] = protocol.MIC_RANGE, samples: int = 1280) -> np.ndarray:
    """A block of audio that `level_from_pcm` reads as `level` (the inverse of its dBFS mapping)."""
    floor, ceil = db_range
    db = floor + (ceil - floor) * min(1.0, max(0.0, level))
    amplitude = 10 ** (db / 20.0) * 32768.0
    return (amplitude * math.sqrt(2.0) * np.sin(np.linspace(0, 40 * math.pi, samples))).astype(np.int16)


def voice(feed: Callable[[np.ndarray], None], db_range: tuple[float, float], seconds: float, sleep: Callable[[float], None]) -> None:
    """Syllable-like levels for `seconds`, fed to `feed` 12 times a second (as the mic and speaker blocks arrive)."""
    for i in range(int(seconds * 12)):
        feed(pcm_at(0.15 + 0.7 * abs(math.sin(i * 0.9)) * (0.6 + 0.4 * math.sin(i * 0.23)), db_range))
        sleep(1 / 12)


def short_sleeps(seconds: float) -> None:
    """Sleeps in 50 ms slices, so a Ctrl+C (or the window going away) is noticed at once."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        time.sleep(min(0.05, left))


def play_turn(bridge: UiBridge, *, sleep: Callable[[float], None] = short_sleeps, confirm: bool = False) -> None:
    """One whole turn. With `confirm`, it asks a risky-action question and waits up to 10 s for a face to answer it."""
    def go(new: State, reason: str = "", seconds: float | None = None) -> None:
        bridge.state_changed(State.IDLE, new, reason, seconds)

    sleep(1.5)                                             # idle: the previous turn (or startup) left the sphere there
    go(State.WAKE_DETECTED, "wake word")
    sleep(0.1)
    go(State.LISTENING, "listening for your request")
    voice(bridge.mic_frame, protocol.MIC_RANGE, 2.6, sleep)
    go(State.TRANSCRIBING, "2.6s of speech")
    bridge.transcript("you", "Close Notepad." if confirm else "Click the first video.")
    sleep(1.0)
    go(State.THINKING, "running the agent")
    sleep(1.0)
    if confirm:
        asked = bridge.confirm_asked("os_close_window({'title': 'Notepad'}) is a risky action. Allow?", "close window Notepad")
        go(State.CONFIRMING, "risky action needs a yes")
        for _ in range(100):                                   # up to 10 s for the face to answer
            if (answer := bridge.poll_answer()) is not None and answer.id == asked:
                bridge.confirm_done(asked, answer.yes, "ui")
                break
            sleep(0.1)
        else:
            bridge.confirm_done(asked, False, "timeout")
            answer = None
        go(State.THINKING, "confirmation")
        sleep(0.6)
        said = "Okay, I closed Notepad." if answer is not None and answer.yes else "Okay, I left Notepad alone."
    else:
        sleep(0.8)
        said = "Playing the first video."
    bridge.outcome(True)
    bridge.reply(said, said, "action", "neutral")
    go(State.SPEAKING, "reply ready")
    voice(bridge.audio_block, protocol.VOICE_RANGE, 2.3, sleep)
    go(State.FOLLOW_UP, "listening 8s for a follow-up", seconds=8.0)
    sleep(2.0)
    go(State.IDLE, "no follow-up")


def main(argv: list[str] | None = None) -> int:
    from .link import Link

    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="python -m orbit.ipc.demo", description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--confirm", action="store_true", help="end each turn with a yes/no question for the window to answer")
    p.add_argument("--exit-with-parent", action="store_true", help="stop when standard input ends (the sphere window keeps it open)")
    p.add_argument("--token", default=config.IPC_TOKEN, help="the token faces must send (default: ORBIT_IPC_TOKEN, else a new random one, printed)")
    args = p.parse_args(argv)
    try:
        link = Link(port=args.port, token=args.token)
        link.start()
    except ImportError:
        print('Needs the "websockets" package: pip install -e ".[ipc]"')
        return 2
    except OSError as e:
        print(f"Cannot open port {args.port}: {e}")
        return 2
    if args.exit_with_parent:
        import _thread

        from .lifetime import watch_parent

        watch_parent(sys.stdin.buffer, _thread.interrupt_main)
    print(f"Demo face link: {link.url}\nCtrl+C stops.", flush=True)
    try:
        while True:
            play_turn(link.bridge, confirm=args.confirm)
    except KeyboardInterrupt:
        pass
    finally:
        link.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

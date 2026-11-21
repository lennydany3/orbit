"""The messages between Orbit (this Python process) and its face (the Rust window, or the design's review page).

Everything is one JSON object per WebSocket text frame.

Orbit -> face
    {"state": "listening", "reason": "wake word"}   the sphere's state (idle wake listening transcribing thinking
                                                     confirming speaking follow_up success error muted). `follow_up`
                                                     also carries "seconds": how long the window stays open.
    {"level": 0.42}                                  0..1: the mic while you talk, Orbit's voice while it speaks
    {"type": "hello", "version": 1, "state": "idle", "level": 0.0}   once, when a face connects
    {"type": "transcript", "who": "you", "text": "..."}
    {"type": "reply", "text": "...", "spoken": "...", "kind": "action" | "chat", "emotion": "proud"}
    {"type": "confirm", "id": 3, "question": "...", "action": "close window Notepad"}   a risky action needs yes or no
    {"type": "confirm_done", "id": 3, "answer": true, "by": "voice" | "ui" | "timeout" | "killed"}
    {"type": "reminder", "id": "r-3f9a", "title": "Stretch", "text": "Reminder: stretch."}   a reminder just came due (Orbit says it too).
                                                     A timer or alarm adds "kind": "timer" | "alarm"; one that is ringing adds
                                                     "ringing": true, and the face shows a Stop button until the next message.
    {"type": "alarm_stopped", "id": "r-3f9a", "by": "voice" | "key" | "ui" | "timeout" | "killed"}   it stopped ringing
    {"type": "error", "message": "..."}              about the face's last message

The state and level messages have no "type" on purpose: they are the format the design's page already understands.

`success` and `error` are one-shot flashes sent just before the state that follows them (`speaking`). Backend states
are never held back to let a flash finish: the face decides how long each animation needs.

Face -> Orbit
    {"type": "confirm", "id": 3, "yes": true}        the answer to a `confirm` that is still open
    {"type": "play", "id": "p-3f9a1c2b"}             the Play button on one of the user's saved playlists (the Music page)
    {"type": "dismiss", "id": "r-3f9a"}              the Stop button on a reminder or timer that is ringing

That is the whole command set. A face cannot make Orbit do anything else: `play` only names a playlist the user saved themselves in
music.json (an id that is not in that file plays nothing), Orbit plays it through its ordinary Spotify tool, and `dismiss` only
silences the alarm that is ringing (an id that is not the one ringing does nothing).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

import numpy as np

PROTOCOL_VERSION = 1
MAX_COMMAND_BYTES = 4096
MAX_TEXT_CHARS = 4000

ORB_STATES = ("idle", "wake", "listening", "transcribing", "thinking", "confirming", "speaking", "follow_up", "success", "error", "muted")

# Loudness as 0..1 between two levels in dBFS (0 dBFS is the loudest a sample can be). Measured on real audio:
#   your microphone: a quiet room is below -65, your speech is mostly -29 (typical) to -17 (loud) -> -55 .. -15
#   Orbit's voice (Piper): mostly -16, up to -8, since it is mastered much hotter than a microphone -> -45 .. -6
MIC_RANGE = (-55.0, -15.0)
VOICE_RANGE = (-45.0, -6.0)


def level_from_pcm(block, db_range: tuple[float, float] = MIC_RANGE) -> float:
    """0..1 loudness of an int16 block. Empty or silent blocks are 0."""
    if block is None or len(block) == 0:
        return 0.0
    x = np.asarray(block, dtype=np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x)))
    if rms < 1e-5:
        return 0.0
    floor, ceil = db_range
    return min(1.0, max(0.0, (20.0 * math.log10(rms) - floor) / (ceil - floor)))


def _clip(text: str) -> str:
    text = str(text)
    return text if len(text) <= MAX_TEXT_CHARS else text[: MAX_TEXT_CHARS - 1] + "…"


def orb_state(state: str, *, reason: str | None = None, seconds: float | None = None) -> dict:
    if state not in ORB_STATES:
        raise ValueError(f"unknown orb state: {state!r}")
    msg: dict = {"state": state}
    if reason:
        msg["reason"] = _clip(reason)
    if seconds is not None:
        msg["seconds"] = round(float(seconds), 2)
    return msg


def level(value: float) -> dict:
    return {"level": round(min(1.0, max(0.0, float(value))), 2)}


def hello(state: str, lvl: float) -> dict:
    return {"type": "hello", "version": PROTOCOL_VERSION, "state": state, "level": round(float(lvl), 2)}


def transcript(who: str, text: str) -> dict:
    return {"type": "transcript", "who": who, "text": _clip(text)}


def reply(text: str, spoken: str, kind: str, emotion: str) -> dict:
    return {"type": "reply", "text": _clip(text), "spoken": _clip(spoken), "kind": kind, "emotion": emotion}


def confirm(confirm_id: int, question: str, action: str) -> dict:
    return {"type": "confirm", "id": confirm_id, "question": _clip(question), "action": _clip(action)}


def confirm_done(confirm_id: int, answer: bool, by: str) -> dict:
    return {"type": "confirm_done", "id": confirm_id, "answer": bool(answer), "by": by}


def reminder(reminder_id: str, title: str, text: str, kind: str = "reminder", ringing: bool = False) -> dict:
    msg = {"type": "reminder", "id": str(reminder_id)[:40], "title": _clip(title), "text": _clip(text)}
    if kind in ("timer", "alarm"):
        msg["kind"] = kind
    if ringing:
        msg["ringing"] = True
    return msg


def alarm_stopped(reminder_id: str, by: str) -> dict:
    return {"type": "alarm_stopped", "id": str(reminder_id)[:40], "by": by}


def error(message: str) -> dict:
    return {"type": "error", "message": _clip(message)}


class ProtocolError(ValueError):
    """A face sent something that is not one of the commands above."""


@dataclass(frozen=True)
class ConfirmAnswer:
    id: int
    yes: bool


@dataclass(frozen=True)
class PlayRequest:
    id: str


@dataclass(frozen=True)
class DismissRequest:
    id: str


_PLAY_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def parse_command(raw: str | bytes) -> ConfirmAnswer | PlayRequest | DismissRequest:
    """Strict: anything but a well-formed `confirm`, `play` or `dismiss` raises ProtocolError (which the server answers with an `error`)."""
    size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    if size > MAX_COMMAND_BYTES:
        raise ProtocolError("message too long")
    try:
        msg = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ProtocolError("not JSON") from None
    if not isinstance(msg, dict):
        raise ProtocolError("expected a JSON object")
    if msg.get("type") == "play":
        pid = msg.get("id")
        if not isinstance(pid, str) or not _PLAY_ID.match(pid):
            raise ProtocolError('play needs the "id" of a saved playlist')
        return PlayRequest(pid)
    if msg.get("type") == "dismiss":
        rid = msg.get("id")
        if not isinstance(rid, str) or not _PLAY_ID.match(rid):
            raise ProtocolError('dismiss needs the "id" of the reminder that is ringing')
        return DismissRequest(rid)
    if msg.get("type") != "confirm":
        raise ProtocolError("unknown command (the only ones are: confirm, play, dismiss)")
    cid, yes = msg.get("id"), msg.get("yes")
    if not isinstance(cid, int) or isinstance(cid, bool) or not isinstance(yes, bool):
        raise ProtocolError('confirm needs an integer "id" and a boolean "yes"')
    return ConfirmAnswer(cid, yes)

"""Where Orbit keeps what is yours: reminders, notes and tasks, and your playlists.

Each one is a small JSON file next to `settings.json` (so `ORBIT_SETTINGS` moves them all together):

    reminders.json   {"version": 1, "reminders": [...], "history": [...], "seen": "..."}
    notes.json       {"version": 1, "notes": [...], "tasks": [...]}
    music.json       {"version": 1, "playlists": [...]}

The window (the Rust app) and Orbit both read and write them, so the rules are the same on both sides and every write is
careful: the whole file goes to a temporary name first and is then renamed over the old one, so a crash never leaves half a
file; a missing or broken file reads as empty and is never allowed to stop Orbit; and anything in a record this version does
not know is written back as it was. Dates are local wall-clock times written as `2026-11-23T17:00` (seconds only when they
matter), because a reminder is about the clock on your wall, not about a time zone.

`tests/fixtures` holds example files that both sides are tested against.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path

from . import settings

REMINDERS_FILE = "reminders.json"
NOTES_FILE = "notes.json"
MUSIC_FILE = "music.json"
VERSION = 1

_write_lock = threading.Lock()


def path_of(name: str) -> Path:
    """The data file `name`, next to the settings file."""
    return settings.path().parent / name


def read(path: Path) -> dict:
    """The file's JSON object, or an empty one if it is missing, unreadable, or not an object. Never raises."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write(path: Path, data: dict) -> None:
    """Writes the whole file or none of it (a temporary file, then a rename). Raises OSError if it cannot."""
    data = {**data, "version": VERSION}
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with _write_lock:
        try:
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            try:
                temp.unlink()
            except OSError:
                pass
            raise


def stamp(path: Path) -> tuple[int, int] | None:
    """Changes whenever the file does (None if there is none): how a watcher notices an edit made by the other side."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def new_id(prefix: str) -> str:
    """A short unique id for a record: r-3f9a1c2b."""
    return f"{prefix}-{secrets.token_hex(4)}"


# ---- the one way a time is written --------------------------------------------------------------------------------------

def to_text(when: datetime) -> str:
    """2026-11-23T17:00 (seconds only if there are some)."""
    return when.strftime("%Y-%m-%dT%H:%M:%S" if when.second else "%Y-%m-%dT%H:%M")


def from_text(text: object) -> datetime | None:
    """A written time back to a datetime, or None if it is not one."""
    if not isinstance(text, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def clean(text: object, limit: int) -> str:
    """A line of text: spaces squeezed, trimmed, cut to `limit` characters. Anything that is not text is empty."""
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())[:limit]

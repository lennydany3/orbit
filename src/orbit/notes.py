"""Notes and tasks: `notes.json`, which the window edits and Orbit adds to when you say "note down ..." or "add ... to my tasks".

    {"version": 1,
     "notes": [{"id": "n-1a2b3c4d", "title": "Gym plan", "body": "Mon legs, Wed push ...", "created": "2026-11-22T10:15",
                "updated": "2026-11-22T10:20"}],
     "tasks": [{"id": "t-9f8e7d6c", "text": "Buy milk", "done": false, "created": "2026-11-22T10:16"}]}

Like the reminders, whoever changes the file reads it fresh, changes one thing and writes it back whole (see `userdata`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import userdata

MAX_NOTES = 500
MAX_TASKS = 500
MAX_TITLE = 80
MAX_BODY = 20_000
MAX_TASK = 200


@dataclass
class Note:
    id: str
    title: str
    body: str = ""
    created: datetime | None = None
    updated: datetime | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: object) -> Note | None:
        if not isinstance(raw, dict):
            return None
        title = userdata.clean(raw.get("title"), MAX_TITLE)
        body = raw.get("body") if isinstance(raw.get("body"), str) else ""
        if not title and not body.strip():
            return None
        known = {"id", "title", "body", "created", "updated"}
        return cls(userdata.clean(raw.get("id"), 40) or userdata.new_id("n"), title, body[:MAX_BODY], userdata.from_text(raw.get("created")),
                   userdata.from_text(raw.get("updated")), {k: v for k, v in raw.items() if k not in known})

    def to_dict(self) -> dict:
        out = dict(self.extra)
        out.update(id=self.id, title=self.title, body=self.body)
        for name in ("created", "updated"):
            when = getattr(self, name)
            if when is not None:
                out[name] = userdata.to_text(when)
        return out


@dataclass
class Task:
    id: str
    text: str
    done: bool = False
    created: datetime | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: object) -> Task | None:
        if not isinstance(raw, dict):
            return None
        text = userdata.clean(raw.get("text"), MAX_TASK)
        if not text:
            return None
        known = {"id", "text", "done", "created"}
        return cls(userdata.clean(raw.get("id"), 40) or userdata.new_id("t"), text, raw.get("done") is True, userdata.from_text(raw.get("created")),
                   {k: v for k, v in raw.items() if k not in known})

    def to_dict(self) -> dict:
        out = dict(self.extra)
        out.update(id=self.id, text=self.text, done=self.done)
        if self.created is not None:
            out["created"] = userdata.to_text(self.created)
        return out


class NotesStore:
    def __init__(self, file: Path | None = None):
        self.file = file
        self.notes: list[Note] = []
        self.tasks: list[Task] = []
        self._stamp: tuple[int, int] | None = None
        self._rest: dict = {}
        self.reload()

    @property
    def path(self) -> Path:
        return self.file or userdata.path_of(userdata.NOTES_FILE)

    def changed(self) -> bool:
        return userdata.stamp(self.path) != self._stamp

    def reload(self) -> None:
        self._stamp = userdata.stamp(self.path)
        data = userdata.read(self.path)
        notes, tasks = data.get("notes"), data.get("tasks")
        self.notes = [n for n in (Note.parse(x) for x in notes[:MAX_NOTES]) if n] if isinstance(notes, list) else []
        self.tasks = [t for t in (Task.parse(x) for x in tasks[:MAX_TASKS]) if t] if isinstance(tasks, list) else []
        self._rest = {k: v for k, v in data.items() if k not in ("version", "notes", "tasks")}

    def _change(self, apply):
        self.reload()
        result = apply()
        data = dict(self._rest)
        data["notes"] = [n.to_dict() for n in self.notes]
        data["tasks"] = [t.to_dict() for t in self.tasks]
        userdata.write(self.path, data)
        self._stamp = userdata.stamp(self.path)
        return result

    # ---- notes ----
    def recent_notes(self) -> list[Note]:
        """Newest change first (notes saved in the same minute: the one saved last)."""
        ranked = sorted(enumerate(self.notes), key=lambda i_n: (i_n[1].updated or i_n[1].created or datetime.min, i_n[0]), reverse=True)
        return [n for _, n in ranked]

    def add_note(self, text: str, now: datetime | None = None) -> Note:
        """A note from what was said: its first sentence is the title, and the rest (or all of it, if it is long) is the body."""
        now = (now or datetime.now()).replace(second=0, microsecond=0)
        body = text.strip()
        first = re.split(r"(?<=[.!?:])\s+|\n", body, maxsplit=1)[0].strip().rstrip(".!?:")
        title = " ".join(first.split())
        if len(title) > MAX_TITLE:
            cut = title[: MAX_TITLE - 1]
            title = (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
        title = title or "Note"
        note = Note(userdata.new_id("n"), title, "" if body.rstrip(".!?:") == title else body[:MAX_BODY], now, now)

        def apply() -> Note:
            if len(self.notes) >= MAX_NOTES:
                raise ValueError(f"that is as many notes as Orbit keeps ({MAX_NOTES}); delete one first")
            self.notes.append(note)
            return note

        return self._change(apply)

    # ---- tasks ----
    def open_tasks(self) -> list[Task]:
        return [t for t in self.tasks if not t.done]

    def add_task(self, text: str, now: datetime | None = None) -> Task:
        task = Task(userdata.new_id("t"), userdata.clean(text, MAX_TASK) or "Task", False, (now or datetime.now()).replace(second=0, microsecond=0))

        def apply() -> Task:
            if len(self.tasks) >= MAX_TASKS:
                raise ValueError(f"that is as many tasks as Orbit keeps ({MAX_TASKS}); clear some first")
            self.tasks.append(task)
            return task

        return self._change(apply)

    def find_tasks(self, words: str, open_only: bool = True) -> list[Task]:
        """The tasks that have all of these words in them."""
        wanted = [w for w in re.findall(r"[a-z0-9']+", words.lower()) if w not in _FILLER]
        if not wanted:
            return []
        return [t for t in self.tasks if (not open_only or not t.done) and all(w in t.text.lower() for w in wanted)]

    def finish_task(self, task_id: str, done: bool = True) -> bool:
        def apply() -> bool:
            for t in self.tasks:
                if t.id == task_id:
                    t.done = done
                    return True
            return False

        return self._change(apply)

    def clear_done(self) -> int:
        def apply() -> int:
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if not t.done]
            return before - len(self.tasks)

        return self._change(apply)


_FILLER = frozenset({"a", "an", "the", "my", "to", "of", "for", "task", "tasks", "it", "as", "done", "complete", "completed", "finished", "off"})

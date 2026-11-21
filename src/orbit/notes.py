"""Notes and tasks: `notes.json`, which the window edits and Orbit adds to when you say "note down ..." or "add ... to my tasks".

    {"version": 1,
     "notes": [{"id": "n-1a2b3c4d", "title": "Gym plan", "body": "Mon legs, Wed push ...", "created": "2026-11-22T10:15",
                "updated": "2026-11-22T10:20"}],
     "tasks": [{"id": "t-9f8e7d6c", "text": "Buy milk", "done": false, "created": "2026-11-22T10:16", "list": "shopping"}]}

A task may name the list it is on ("shopping", "packing"); with no `list` it is on the plain task list. "Add tomato and pasta to my
shopping list" puts two tasks on the "shopping" list.

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
MAX_LIST = 30


def clean_list(name: object) -> str:
    """A list's name as it is kept: lowercase words, letters and digits and hyphens, at most MAX_LIST characters ("" = the task list)."""
    if not isinstance(name, str):
        return ""
    words = re.findall(r"[a-z0-9][a-z0-9-]*", name.lower())
    return " ".join(words)[:MAX_LIST].strip()


def list_title(name: str) -> str:
    """How a list is called out loud and on the page: "Shopping", "Packing list" is just "Packing"; the plain one is "Tasks"."""
    return name.capitalize() if name else "Tasks"


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
    list: str = ""                                 # which list it is on ("" = the plain task list)
    extra: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: object) -> Task | None:
        if not isinstance(raw, dict):
            return None
        text = userdata.clean(raw.get("text"), MAX_TASK)
        if not text:
            return None
        known = {"id", "text", "done", "created", "list"}
        return cls(userdata.clean(raw.get("id"), 40) or userdata.new_id("t"), text, raw.get("done") is True, userdata.from_text(raw.get("created")),
                   clean_list(raw.get("list")), {k: v for k, v in raw.items() if k not in known})

    def to_dict(self) -> dict:
        out = dict(self.extra)
        out.update(id=self.id, text=self.text, done=self.done)
        if self.created is not None:
            out["created"] = userdata.to_text(self.created)
        if self.list:
            out["list"] = self.list
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
    def open_tasks(self, list_name: str | None = None) -> list[Task]:
        """What is still to do: on one list, or (None) on every list."""
        return [t for t in self.tasks if not t.done and (list_name is None or t.list == list_name)]

    def tasks_on(self, list_name: str) -> list[Task]:
        """Everything on one list, finished or not."""
        return [t for t in self.tasks if t.list == list_name]

    def list_names(self) -> list[tuple[str, int]]:
        """Every list that has something on it, with how many are still to do: the plain task list first, then in the order they began."""
        order: list[str] = []
        for t in self.tasks:
            if t.list not in order:
                order.append(t.list)
        order.sort(key=lambda name: name != "")
        return [(name, len(self.open_tasks(name))) for name in order]

    def add_task(self, text: str, now: datetime | None = None, list_name: str = "") -> Task:
        return self.add_tasks([text], now, list_name)[0]

    def add_tasks(self, texts: list[str], now: datetime | None = None, list_name: str = "") -> list[Task]:
        """Several tasks at once, on one list, saved as one change ("add tomato, pasta and milk to my shopping list")."""
        when = (now or datetime.now()).replace(second=0, microsecond=0)
        made = [Task(userdata.new_id("t"), userdata.clean(t, MAX_TASK) or "Task", False, when, clean_list(list_name)) for t in texts]

        def apply() -> list[Task]:
            if len(self.tasks) + len(made) > MAX_TASKS:
                raise ValueError(f"that is as many tasks as Orbit keeps ({MAX_TASKS}); clear some first")
            self.tasks.extend(made)
            return made

        return self._change(apply)

    def find_tasks(self, words: str, open_only: bool = True, list_name: str | None = None) -> list[Task]:
        """The tasks that have all of these words in them (on one list, or on every list)."""
        wanted = [w for w in re.findall(r"[a-z0-9']+", words.lower()) if w not in _FILLER]
        if not wanted:
            return []
        return [t for t in self.tasks if (not open_only or not t.done) and (list_name is None or t.list == list_name)
                and all(w in t.text.lower() for w in wanted)]

    def remove_tasks(self, ids: set[str]) -> int:
        def apply() -> int:
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if t.id not in ids]
            return before - len(self.tasks)

        return self._change(apply)

    def clear_list(self, list_name: str) -> int:
        """Takes everything off one list. How many went."""
        def apply() -> int:
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if t.list != list_name]
            return before - len(self.tasks)

        return self._change(apply)

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

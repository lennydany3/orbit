"""What is yours: reminders, notes and tasks, and your saved playlists. Said in plain words, answered without the language model.

    "remind me to call mom at 5pm"   "what are my reminders"   "cancel the reminder about mom"   "set a timer for ten minutes"
    "note down the wifi is on the router"   "what notes do I have"
    "add milk to my tasks"   "what's on my to do list"   "mark milk as done"
    "play my gym playlist"   "what playlists do I have"

Like the user's own commands this runs before the rules and the model, matches whole sentences, and never guesses: a sentence that is
not plainly one of these goes on to the normal path. The things it changes are the files the window shows (`userdata`), so what is
said here is on screen at once, and what is made on screen is known here at once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .. import reminders as reminders_mod
from ..notes import NotesStore
from ..playlists import MusicStore
from ..reminders import ReminderStore


@dataclass
class Reply:
    text: str
    ok: bool = True


_HEAD = re.compile(r"^(?:(?:hey |ok |okay |hi )?orbit[,.]?\s+)?(?:please\s+)?(?:can you\s+|could you\s+|would you\s+)?(?:please\s+)?", re.I)
_TAIL = re.compile(r"[\s.!?]*(?:,?\s*please)?[\s.!?]*$", re.I)

_LIST_WORDS = r"(?:what(?:'s|s| is| are| was)?|which|show|list|read|tell|do i have|have i got|how many|any)"
_AFTER = r"(?:\s+(?:do i have|i have|i've got|i made|i saved|so far|today|tomorrow|left|now|right now|again))*"


def _ask(noun: str) -> re.Pattern:
    """A question or request to hear what there is of `noun`: "what are my reminders", "do I have any notes today"."""
    return re.compile(rf"^{_LIST_WORDS}(?:\s+[\w']+){{0,5}}?\s+{noun}{_AFTER}$", re.I)


_REMINDER_NOUN = r"(?:reminders?|alarms?|timers?)"
_LIST_REMINDERS = _ask(_REMINDER_NOUN)
_CANCEL_REMINDER = re.compile(rf"^(?:cancel|delete|remove|clear|forget|get rid of|turn off|stop)\b(?P<rest>.*\b{_REMINDER_NOUN}\b.*)$", re.I)

_TASK_NOUN = r"(?:tasks?(?: list)?|to ?do(?:s| list)?|to-do(?:s| list)?|todo(?:s| list)?|checklist)"
_ADD_TASK_TO = re.compile(rf"^(?:add|put|place|stick)\s+(?P<t>.+?)\s+(?:to|on|onto|in|into)\s+(?:my\s+|the\s+)?{_TASK_NOUN}$", re.I)
_ADD_TASK = re.compile(r"^(?:add|create|make|new)\s+(?:me\s+)?(?:a\s+|another\s+)?(?:new\s+)?(?:task|to ?do|to-do)(?:\s+(?:to|for|called|named|that says))?[:,]?\s*(?P<t>.*)$", re.I)
_LIST_TASKS = _ask(_TASK_NOUN)
_DONE = r"(?:done|complete|completed|finished)"
_MARK_DONE = re.compile(rf"^(?:mark|tick|check|cross|strike)\s+(?:off\s+)?(?:the\s+|my\s+)?(?P<t>.+?)\s+(?:as\s+)?(?:{_DONE}|off(?:\s+(?:my|the)\s+(?:list|{_TASK_NOUN}))?)$", re.I)
_TICK_OFF = re.compile(rf"^(?:tick|check|cross|strike)\s+off\s+(?:the\s+|my\s+)?(?P<t>.+?)(?:\s+(?:from|on|off)\s+(?:my\s+|the\s+)?{_TASK_NOUN})?$", re.I)
_COMPLETE_TASK = re.compile(r"^(?:complete|finish|done with)\s+(?:the\s+|my\s+)?(?:task\s+)?(?P<t>.+)$", re.I)
_I_FINISHED = re.compile(r"^(?:i(?:'ve| have)?\s+)?(?:finished|completed|done|did)\s+(?:with\s+)?(?:the\s+|my\s+)?(?P<t>.+)$", re.I)
_CLEAR_DONE = re.compile(rf"^(?:clear|remove|delete|clean up)\s+(?:all\s+)?(?:my\s+|the\s+)?(?:completed|finished|done)\s+(?:{_TASK_NOUN}|items)$", re.I)

_NOTE = re.compile(
    r"^(?:note(?: down)?|jot(?: down)?|write(?: down)?|take(?: me)? a note|make(?: me)? a note|add(?: me)? a note|save(?: me)? a note|"
    r"create(?: me)? a note|new note|start a note|write(?: me)? a note)(?:\s+(?:of|that|about|saying|says|to self))?[:,]?\s*(?P<t>.*)$", re.I)
_LIST_NOTES = _ask(r"notes?")
_LAST_NOTE = re.compile(r"^(?:read|open|show|what(?:'s| is| was)?)\b.*\b(?:last|latest|newest|recent)\s+note\b.*$", re.I)

_PLAY = re.compile(r"^(?:play|put on|start|shuffle|queue up)\s+(?:some\s+)?(?:my\s+|the\s+)?(?P<q>.+?)(?:\s+on\s+spotify)?$", re.I)
_LIST_PLAYLISTS = _ask(r"(?:playlists?|saved music)")

_NUMBERS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


class Personal:
    """Understands those sentences. Each store reads its file again when it changed, so the window's edits are seen at once."""

    def __init__(self, reminders: ReminderStore | None = None, notes: NotesStore | None = None, music: MusicStore | None = None,
                 clock: Callable[[], datetime] = datetime.now):
        self.reminders = reminders or ReminderStore()
        self.notes = notes or NotesStore()
        self.music = music or MusicStore()
        self.clock = clock

    def handle(self, text: str, agent) -> Reply | None:
        """The reply to a sentence about reminders, notes, tasks or playlists; None if it is not one."""
        said = _TAIL.sub("", _HEAD.sub("", " ".join(text.split())))
        if not said:
            return None
        try:
            for step in (self._reminders, self._tasks, self._notes, self._music):
                reply = step(said, agent)
                if reply is not None:
                    return reply
        except ValueError as e:                                    # a limit (too many reminders), said as it is
            return Reply(f"Sorry, {e}.", ok=False)
        except OSError as e:
            return Reply(f"Sorry, I could not save that ({e.strerror or e}).", ok=False)
        return None

    # ---- reminders ----
    def _reminders(self, said: str, agent) -> Reply | None:
        now = self.clock()
        request = reminders_mod.parse_request(said, now)
        if isinstance(request, str):
            return Reply(request, ok=False)
        if request is not None:
            added = self.reminders.add(request.title, request.due, request.repeat, now)
            when = reminders_mod.spoken_when(added.due, added.repeat, now)
            if request.kind == "timer":
                return Reply(f"Okay, a timer for {when.removeprefix('in ')}.")
            if request.kind == "alarm":
                return Reply(f"Okay, I set an alarm for {when}.")
            return Reply(f"Okay, I'll remind you {'to ' if not added.title.lower().startswith(('to ', 'that ')) else ''}{self._lower_first(added.title)} {when}.")
        if _LIST_REMINDERS.match(said):
            return self._list_reminders(now)
        m = _CANCEL_REMINDER.match(said)
        if m:
            return self._cancel_reminders(m.group("rest"), agent)
        return None

    @staticmethod
    def _lower_first(title: str) -> str:
        return title if title[:2].isupper() or not title else title[0].lower() + title[1:]

    def _list_reminders(self, now: datetime) -> Reply:
        self.reminders.reload()
        active = self.reminders.active()
        if not active:
            return Reply("You have no reminders.")
        shown = [f"{r.title}, {reminders_mod.spoken_when(r.due, r.repeat, now)}" for r in active[:3]]
        more = f", and {len(active) - 3} more" if len(active) > 3 else ""
        return Reply(f"You have {_plural(len(active), 'reminder')}: " + _join(shown) + more + ".")

    def _cancel_reminders(self, rest: str, agent) -> Reply:
        self.reminders.reload()
        everything = self.reminders.reminders
        if not everything:
            return Reply("You have no reminders to cancel.")
        words = re.sub(r"\b(?:the|my|a|an|all|every|every one of|of|me|about|for|to|that|called|named|on|reminders?|alarms?|timers?)\b", " ", rest, flags=re.I).strip()
        if re.search(r"\b(?:all|every|everything)\b", rest, re.I) and not words:
            what = f"all {len(everything)} of your reminders" if len(everything) > 1 else "your reminder"
            if not agent.safety.confirm(f"delete_mine({{'what': {what!r}}}) is a risky action. Allow?"):
                return Reply("Okay, I kept your reminders.")
            self.reminders.remove({r.id for r in everything})
            return Reply(f"Okay, I deleted {what}.")
        if words:
            found = self.reminders.find(words)
            if not found:
                return Reply(f"I couldn't find a reminder about {words}.", ok=False)
            if len(found) > 1:
                return Reply(f"I found {len(found)} reminders about {words}. Say which one, or say cancel all my reminders.", ok=False)
            self.reminders.remove({found[0].id})
            return Reply(f"Okay, I cancelled the reminder: {found[0].title}.")
        if len(everything) == 1:
            self.reminders.remove({everything[0].id})
            return Reply(f"Okay, I cancelled the reminder: {everything[0].title}.")
        return Reply(f"You have {len(everything)} reminders. Say which one, like cancel the reminder about {self._lower_first(everything[0].title)}.", ok=False)

    # ---- tasks ----
    def _tasks(self, said: str, agent) -> Reply | None:
        m = _ADD_TASK_TO.match(said)
        if m and not _NOTE.match(said):
            return self._add_task(m.group("t"))
        m = _ADD_TASK.match(said)
        if m:
            return self._add_task(m.group("t")) if m.group("t").strip() else Reply("What should the task be? Say it together, like add a task: buy milk.", ok=False)
        if _CLEAR_DONE.match(said):
            count = self.notes.clear_done()
            return Reply(f"Okay, I cleared {_plural(count, 'finished task')}." if count else "There are no finished tasks to clear.")
        if _LIST_TASKS.match(said):
            return self._list_tasks()
        for pattern in (_MARK_DONE, _TICK_OFF):
            m = pattern.match(said)
            if m:
                return self._finish_task(m.group("t"), certain=True)
        for pattern in (_COMPLETE_TASK, _I_FINISHED):
            m = pattern.match(said)
            if m:
                return self._finish_task(m.group("t"), certain=False)  # "I finished my exam" is chat, unless it names one of your tasks
        return None

    def _add_task(self, text: str) -> Reply:
        task = self.notes.add_task(re.sub(r"^(?:to|that|for)\s+", "", text.strip(), flags=re.I).strip(" ,.;:"))
        left = len(self.notes.open_tasks())
        return Reply(f"Okay, I added {task.text} to your tasks. You have {_plural(left, 'task')} to do.")

    def _list_tasks(self) -> Reply:
        self.notes.reload()
        open_tasks = self.notes.open_tasks()
        if not open_tasks:
            return Reply("Your task list is empty.")
        shown = [t.text for t in open_tasks[:5]]
        more = f", and {len(open_tasks) - 5} more" if len(open_tasks) > 5 else ""
        return Reply(f"You have {_plural(len(open_tasks), 'task')} to do: " + _join(shown) + more + ".")

    def _finish_task(self, words: str, certain: bool) -> Reply | None:
        self.notes.reload()
        found = self.notes.find_tasks(words)
        if len(found) == 1:
            self.notes.finish_task(found[0].id)
            return Reply(f"Okay, I marked {found[0].text} as done.")
        if not certain:
            return None
        if not found:
            return Reply(f"I couldn't find a task called {words.strip()}.", ok=False) if self.notes.tasks else None
        return Reply(f"I found {len(found)} tasks like that. Say a bit more of its name.", ok=False)

    # ---- notes ----
    def _notes(self, said: str, agent) -> Reply | None:
        if _LAST_NOTE.match(said):
            self.notes.reload()
            recent = self.notes.recent_notes()
            if not recent:
                return Reply("You have no notes yet.")
            n = recent[0]
            body = re.sub(r"\s+", " ", n.body).strip()
            return Reply(f"Your latest note is {n.title}." + (f" {body[:300]}" if body else ""))
        m = _NOTE.match(said)
        if m:
            text = m.group("t").strip()
            if not text:
                return Reply("What should I note down? Say it together, like note down the wifi is on the router.", ok=False)
            note = self.notes.add_note(text[0].upper() + text[1:])
            return Reply(f"Okay, I saved a note: {note.title}.")
        if _LIST_NOTES.match(said):
            self.notes.reload()
            recent = self.notes.recent_notes()
            if not recent:
                return Reply("You have no notes yet.")
            shown = [n.title for n in recent[:3]]
            more = f", and {len(recent) - 3} more" if len(recent) > 3 else ""
            return Reply(f"You have {_plural(len(recent), 'note')}. The latest: " + _join(shown) + more + ".")
        return None

    # ---- music ----
    def _music(self, said: str, agent) -> Reply | None:
        if _LIST_PLAYLISTS.match(said):
            self.music.reload()
            named = [p for p in self.music.playlists if p.name]
            if not named:
                return Reply("You have not saved any playlists yet. Paste a Spotify link on the Music page.")
            shown = [p.name for p in named[:5]]
            return Reply(f"You have {_plural(len(named), 'saved playlist')}: " + _join(shown) + ".")
        m = _PLAY.match(said)
        if not m:
            return None
        self.music.reload()
        found = self.music.find(m.group("q"))
        if not found:
            return None                                              # not one of yours: the normal "play ..." path (a search) handles it
        return self._play(found[0], agent)

    def play_by_id(self, playlist_id: str, agent) -> Reply:
        """The Play button on the Music page: one of the saved playlists, by its id."""
        self.music.reload()
        playlist = self.music.get(playlist_id)
        if playlist is None:
            return Reply("That playlist is not saved any more.", ok=False)
        return self._play(playlist, agent)

    @staticmethod
    def _play(playlist, agent) -> Reply:
        registry = getattr(agent, "registry", None)
        if registry is not None and registry.get_exposed("api_spotify_play_saved") is None:
            return Reply("Spotify is not set up, so I cannot play that. Set SPOTIFY_CLIENT_ID and run auth spotify first.", ok=False)
        result = agent.execute("api_spotify_play_saved", {"uri": playlist.link.uri, "label": playlist.title})
        if not result.ok:
            return Reply(f"Failed: {result.error}", ok=False)
        return Reply(result.summary[0].upper() + result.summary[1:] + ".")

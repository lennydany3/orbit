"""What is yours: reminders, notes and tasks, and your saved playlists. Said in plain words, answered without the language model.

    "remind me to call mom at 5pm"   "what are my reminders"   "cancel the reminder about mom"   "set a timer for ten minutes"
    "note down the wifi is on the router"   "what notes do I have"
    "add milk to my tasks"   "what's on my to do list"   "mark milk as done"
    "add tomato, pasta and milk to my shopping list"   "what's on my shopping list"   "remove milk from my shopping list"
    "play my gym playlist"   "what playlists do I have"

Like the user's own commands this runs before the rules and the model, matches whole sentences, and never guesses: a sentence that is
not plainly one of these goes on to the normal path. The things it changes are the files the window shows (`userdata`), so what is
said here is on screen at once, and what is made on screen is known here at once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .. import reminders as reminders_mod
from .. import userdata
from ..notes import NotesStore, clean_list, list_title
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


_REMINDER_NOUN = r"(?:reminders?|remainders?|alarms?|timers?|countdowns?)"
_LIST_REMINDERS = _ask(_REMINDER_NOUN)
# "snooze", "remind me again in 10 minutes", "five more minutes": ring the one that just rang again, later
_SNOOZE = re.compile(r"^(?:snooze(?:\s+(?:it|that|the\s+(?:alarm|timer|reminder|remainder)))?|(?:remind|ring)\s+(?:me\s+)?again|"
                     r"(?:give\s+me\s+)?(?:\d+|a|an|one|two|three|five|ten|fifteen|twenty|thirty)\s+more\s+(?:minutes?|hours?))\b(?P<rest>.*)$", re.I)
SNOOZE_FROM_RING = timedelta(minutes=30)        # only something that rang this recently can be snoozed
DEFAULT_SNOOZE = timedelta(minutes=5)
_CANCEL_REMINDER = re.compile(rf"^(?:cancel|delete|remove|clear|forget|get rid of|turn off|stop)\b(?P<rest>.*\b{_REMINDER_NOUN}\b.*)$", re.I)

# what a list is called when someone says it: "my shopping list", "the to do list", "tasks", "groceries"
_TASK_NOUN = r"(?:tasks?(?: list)?|to ?do(?:s| list)?|to-do(?:s| list)?|todo(?:s| list)?|checklist)"
_REF = r"(?:(?:[a-z-]+\s+){0,2}(?:lists?|checklists?)|tasks?|to-?dos?|groceries|grocery|shopping)"
_GENERIC_LIST = frozenset({"to", "do", "todo", "to-do", "todos", "to-dos", "task", "tasks", "my", "the", "our", "a", "an", "new", "full", "whole", "entire",
                           "current", "complete", "your", "check", "of", "what", "is", "are", "on", "in", "s", "me", "all", "that", "this"})
_LIST_ALIASES = {"grocery": "shopping", "groceries": "shopping"}

_ADD_TO_LIST = re.compile(rf"^(?:add|put|place|stick|write|include|throw|pop)\s+(?P<items>.+?)\s+(?:to|on|onto|in|into|at)\s+(?:my\s+|the\s+|our\s+)?(?P<ref>{_REF})$", re.I)
_ADD_TASK = re.compile(r"^(?:add|create|make|new)\s+(?:me\s+)?(?:a\s+|another\s+)?(?:new\s+)?(?:task|to ?do|to-do)(?:\s+(?:to|for|called|named|that says))?[:,]?\s*(?P<t>.*)$", re.I)
_REMOVE_FROM_LIST = re.compile(rf"^(?:remove|delete|take|scratch|get rid of)\s+(?P<items>.+?)\s+(?:off|from|out of|on)\s+(?:my\s+|the\s+|our\s+)?(?P<ref>{_REF})$", re.I)
_CLEAR_LIST = re.compile(rf"^(?:clear|empty|wipe|reset|delete)\s+(?:out\s+)?(?:all\s+)?(?:of\s+)?(?:my\s+|the\s+|our\s+)?(?P<ref>{_REF})$", re.I)
_LIST_TASKS = _ask(rf"(?:{_REF})")
_LISTS_OVERVIEW = re.compile(r"^(?:what|which|show|read|tell me|list)(?:\s+[\w']+){0,3}?\s+lists(?:\s+do i have|\s+i have)?$", re.I)
_DONE = r"(?:done|complete|completed|finished)"
_MARK_DONE = re.compile(rf"^(?:mark|tick|check|cross|strike)\s+(?:off\s+)?(?:the\s+|my\s+)?(?P<t>.+?)\s+(?:as\s+)?(?:{_DONE}|off(?:\s+(?:my|the)\s+(?:list|{_TASK_NOUN}))?)$", re.I)
_TICK_OFF = re.compile(rf"^(?:tick|check|cross|strike)\s+off\s+(?:the\s+|my\s+)?(?P<t>.+?)(?:\s+(?:from|on|off)\s+(?:my\s+|the\s+)?(?:{_REF}))?$", re.I)
_COMPLETE_TASK = re.compile(r"^(?:complete|finish|done with)\s+(?:the\s+|my\s+)?(?:task\s+)?(?P<t>.+)$", re.I)
_I_FINISHED = re.compile(r"^(?:i(?:'ve| have)?\s+)?(?:finished|completed|done|did|bought|got)\s+(?:with\s+)?(?:the\s+|my\s+)?(?P<t>.+)$", re.I)
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
        if _SNOOZE.match(said):
            return self._snooze(said, now)
        request = reminders_mod.parse_request(said, now)
        if isinstance(request, str):
            return Reply(request, ok=False)
        if request is not None:
            added = self.reminders.add(request.title, request.due, request.repeat, now, request.kind)
            # "in 30 seconds" is said back as 30 seconds, not rounded to a minute
            when = f"in {reminders_mod.duration_text(request.seconds)}" if request.seconds else reminders_mod.spoken_when(added.due, added.repeat, now)
            if request.kind == "timer":
                return Reply(f"Okay, a timer for {when.removeprefix('in ')}." if request.seconds else f"Okay, a timer {when}.")
            if request.kind == "alarm":
                return Reply(f"Okay, I set an alarm for {when}.")
            if not request.has_own_title:                              # "remind me in a minute": nothing to say it is about
                return Reply(f"Okay, I'll remind you {when}.")
            return Reply(f"Okay, I'll remind you {'to ' if not added.title.lower().startswith(('to ', 'that ')) else ''}{self._lower_first(added.title)} {when}.")
        if _LIST_REMINDERS.match(said):
            return self._list_reminders(now)
        m = _CANCEL_REMINDER.match(said)
        if m:
            return self._cancel_reminders(m.group("rest"), agent)
        return None

    def _snooze(self, said: str, now: datetime) -> Reply:
        """Rings again what rang last (everything that rang together), later: in the time said, else in 5 minutes."""
        self.reminders.reload()
        fired = [(h, userdata.from_text(h.get("at"))) for h in self.reminders.history]
        fired = [(h, at) for h, at in fired if at is not None and timedelta(0) <= now - at <= SNOOZE_FROM_RING]
        if not fired:
            return Reply("There is nothing to snooze: nothing rang just now.", ok=False)
        newest = max(at for _, at in fired)
        wait = reminders_mod.spoken_length(said)
        delay = timedelta(seconds=wait) if wait else DEFAULT_SNOOZE
        for h, at in fired:
            if at == newest:
                title = str(h.get("title") or "Reminder")
                kind = {"timer": "timer", "alarm": "alarm"}.get(title.lower(), "reminder")
                self.reminders.add(title, now + delay, "none", now, kind)
        return Reply(f"Okay, I'll ring again in {reminders_mod.duration_text(delay.total_seconds())}.")

    @staticmethod
    def _lower_first(title: str) -> str:
        return title if title[:2].isupper() or not title else title[0].lower() + title[1:]

    @staticmethod
    def _cancelled(title: str) -> str:
        """"Okay, I cancelled the reminder." (one that has no title of its own is not read back as "Reminder: Reminder")."""
        if title in ("Reminder", "Timer", "Alarm"):
            return f"Okay, I cancelled the {title.lower()}."
        return f"Okay, I cancelled the reminder: {title}."

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
        words = re.sub(r"\b(?:the|my|a|an|all|every|every one of|of|me|about|for|to|that|called|named|on|reminders?|remainders?|alarms?|timers?|countdowns?)\b", " ", rest, flags=re.I)
        words = re.sub(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty|sixty|half|an?)\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", " ", words, flags=re.I)
        words = " ".join(words.split())                           # "the one minute remainder" names no thing, only which one: that is for the list to answer
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
            return Reply(self._cancelled(found[0].title))
        if len(everything) == 1:
            self.reminders.remove({everything[0].id})
            return Reply(self._cancelled(everything[0].title))
        return Reply(f"You have {len(everything)} reminders. Say which one, like cancel the reminder about {self._lower_first(everything[0].title)}.", ok=False)

    # ---- tasks and lists ----
    @staticmethod
    def _list_of(ref: str) -> str:
        """Which list a phrase like "shopping list", "my to do list" or "groceries" means: its name ("" = the plain task list)."""
        found = re.search(_REF, ref.lower())                       # only the words of the list's name, not of the whole sentence
        words = re.findall(r"[a-z][a-z-]*", found.group(0) if found else ref.lower())
        for w in words:
            if w in _LIST_ALIASES:
                return _LIST_ALIASES[w]
        named = [w for w in words if w not in _GENERIC_LIST and w not in ("list", "lists", "checklist", "checklists")]
        return clean_list(named[-1]) if named else ""

    @staticmethod
    def _items(text: str, split: bool = True) -> list[str]:
        """"tomato, pasta, and milkshake" -> three items ("a", "an" and "some" in front are dropped). split=False: it is one thing ("call mom and dad")."""
        parts = re.split(r"\s*(?:,|;|&|\band\b|\bplus\b|\bas well as\b)\s*", text.strip()) if split else [text]
        items: list[str] = []
        for part in parts:
            item = re.sub(r"^(?:and\s+|some\s+|a\s+|an\s+|the\s+|more\s+|to\s+|that\s+|for\s+)+", "", part.strip(" ,.;:!?"), flags=re.I).strip()
            if item and item.lower() not in {i.lower() for i in items}:
                items.append(item[0].upper() + item[1:])
        return items[:30]

    def _tasks(self, said: str, agent) -> Reply | None:
        m = _ADD_TO_LIST.match(said)
        if m and not _NOTE.match(said):
            return self._add_items(m.group("items"), self._list_of(m.group("ref")))
        m = _ADD_TASK.match(said)
        if m:
            return self._add_items(m.group("t"), "") if m.group("t").strip() else Reply("What should the task be? Say it together, like add a task: buy milk.", ok=False)
        if _CLEAR_DONE.match(said):
            count = self.notes.clear_done()
            return Reply(f"Okay, I cleared {_plural(count, 'finished task')}." if count else "There are no finished tasks to clear.")
        m = _REMOVE_FROM_LIST.match(said)
        if m:
            return self._remove_items(m.group("items"), self._list_of(m.group("ref")))
        m = _CLEAR_LIST.match(said)
        if m:
            return self._clear_list(self._list_of(m.group("ref")), agent)
        if _LISTS_OVERVIEW.match(said):
            return self._lists_overview()
        if _LIST_TASKS.match(said):
            return self._list_tasks(self._list_of(said))
        for pattern in (_MARK_DONE, _TICK_OFF):
            m = pattern.match(said)
            if m:
                return self._finish_task(m.group("t"), certain=True)
        for pattern in (_COMPLETE_TASK, _I_FINISHED):
            m = pattern.match(said)
            if m:
                return self._finish_task(m.group("t"), certain=False)  # "I finished my exam" is chat, unless it names one of your tasks
        return None

    def _add_items(self, text: str, list_name: str) -> Reply:
        items = self._items(text, split=bool(list_name))      # a named list takes several at once; a task is one thing ("call mom and dad")
        if not items:
            return Reply("What should I add? Say it together, like add milk to my shopping list.", ok=False)
        self.notes.add_tasks(items, self.clock(), list_name)
        where = "your tasks" if not list_name else f"your {list_name} list"
        left = len(self.notes.open_tasks(list_name))
        tail = f" You have {_plural(left, 'task')} to do." if not list_name else f" It has {_plural(left, 'thing')} on it now." if left != len(items) else ""
        return Reply(f"Okay, I added {_join(items)} to {where}.{tail}")

    def _list_tasks(self, list_name: str) -> Reply:
        self.notes.reload()
        open_tasks = self.notes.open_tasks(list_name)
        where = "task list" if not list_name else f"{list_name} list"
        if not open_tasks:
            return Reply(f"Your {where} is empty.")
        shown = [t.text for t in open_tasks[:8]]
        more = f", and {len(open_tasks) - 8} more" if len(open_tasks) > 8 else ""
        if not list_name:
            return Reply(f"You have {_plural(len(open_tasks), 'task')} to do: " + _join(shown) + more + ".")
        return Reply(f"On your {list_name} list you have {_join(shown)}{more}.")

    def _lists_overview(self) -> Reply:
        self.notes.reload()
        lists = [(name, n) for name, n in self.notes.list_names() if n]
        if not lists:
            return Reply("You have no lists with anything on them.")
        parts = [f"{list_title(name).lower()} with {_plural(n, 'thing')}" for name, n in lists]
        return Reply(f"You have {_plural(len(lists), 'list')}: " + _join(parts) + ".")

    def _remove_items(self, text: str, list_name: str) -> Reply:
        self.notes.reload()
        gone, missing = [], []
        for item in self._items(text, split=bool(list_name)):
            found = self.notes.find_tasks(item, open_only=False, list_name=list_name)
            exact = [t for t in found if t.text.lower() == item.lower()] or found
            (gone if exact else missing).append((item, exact))
        if gone:
            self.notes.remove_tasks({t.id for _, found in gone for t in found})
        where = "your tasks" if not list_name else f"your {list_name} list"
        said = f"Okay, I removed {_join([i for i, _ in gone])} from {where}." if gone else ""
        if missing:
            said += (" " if said else "") + f"I couldn't find {_join([i for i, _ in missing])} on {where}."
        return Reply(said, ok=not missing or bool(gone))

    def _clear_list(self, list_name: str, agent) -> Reply:
        self.notes.reload()
        count = len(self.notes.tasks_on(list_name))
        where = "task list" if not list_name else f"{list_name} list"
        if not count:
            return Reply(f"Your {where} is already empty.")
        what = f"everything on your {where} ({_plural(count, 'item')})" if count > 1 else f"the one thing on your {where}"
        if not agent.safety.confirm(f"delete_mine({{'what': {what!r}}}) is a risky action. Allow?"):
            return Reply(f"Okay, I kept your {where}.")
        self.notes.clear_list(list_name)
        return Reply(f"Okay, I cleared your {where}.")

    def _finish_task(self, words: str, certain: bool) -> Reply | None:
        self.notes.reload()
        found = self.notes.find_tasks(words)
        if len(found) == 1:
            self.notes.finish_task(found[0].id)
            where = f" on your {found[0].list} list" if found[0].list else ""
            return Reply(f"Okay, I marked {found[0].text} as done{where}.")
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

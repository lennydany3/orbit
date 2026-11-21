"""Reminders: the list the window edits, what Orbit says when one is due, and understanding "remind me to ... in 10 minutes".

The data is `reminders.json` (see `userdata`):

    {"version": 1,
     "reminders": [{"id": "r-3f9a1c2b", "title": "Stretch", "due": "2026-11-23T17:00", "repeat": "none", "enabled": true,
                    "created": "2026-11-22T10:15"}],
     "history": [{"id": "r-3f9a1c2b", "title": "Stretch", "at": "2026-11-22T17:00"}],     # what fired lately, newest first
     "seen": "2026-11-22T09:00"}                                                           # when the window last showed them

`repeat` is none, daily, weekdays, weekly or monthly. Orbit is the one that fires reminders (it speaks them and tells the
window); the window adds, edits, switches off and deletes them, and so does Orbit when you ask by voice. Whoever changes the
file reads it fresh first, changes the one thing, and writes it back whole, so the other side's change is not lost.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import userdata

REPEATS = ("none", "daily", "weekdays", "weekly", "monthly")
MAX_REMINDERS = 200
MAX_TITLE = 120
MAX_HISTORY = 30
LATE_GRACE = timedelta(hours=6)        # a reminder that came due while Orbit was off is still said if it is this recent
DEFAULT_HOUR = 9                       # "tomorrow" with no time means 9 in the morning

_DAY_PARTS = {"morning": 8, "afternoon": 15, "evening": 18, "night": 21, "tonight": 20}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY_ALIASES = {**{d: i for i, d in enumerate(_WEEKDAYS)}, "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3,
                    "fri": 4, "sat": 5, "sun": 6}
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45,
                 "fifty": 50, "sixty": 60, "ninety": 90}


# ---- the data ---------------------------------------------------------------------------------------------------------

@dataclass
class Reminder:
    id: str
    title: str
    due: datetime
    repeat: str = "none"
    enabled: bool = True
    created: datetime | None = None
    extra: dict = field(default_factory=dict)               # anything else the file had on it, written back untouched

    @classmethod
    def parse(cls, raw: object) -> Reminder | None:
        if not isinstance(raw, dict):
            return None
        title = userdata.clean(raw.get("title"), MAX_TITLE)
        due = userdata.from_text(raw.get("due"))
        if not title or due is None:
            return None
        repeat = raw.get("repeat") if raw.get("repeat") in REPEATS else "none"
        enabled = raw.get("enabled") if isinstance(raw.get("enabled"), bool) else True
        rid = userdata.clean(raw.get("id"), 40) or userdata.new_id("r")
        known = {"id", "title", "due", "repeat", "enabled", "created"}
        return cls(rid, title, due, repeat, enabled, userdata.from_text(raw.get("created")),
                   {k: v for k, v in raw.items() if k not in known})

    def to_dict(self) -> dict:
        out = dict(self.extra)
        out.update(id=self.id, title=self.title, due=userdata.to_text(self.due), repeat=self.repeat, enabled=self.enabled)
        if self.created is not None:
            out["created"] = userdata.to_text(self.created)
        return out


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def advance(due: datetime, repeat: str, after: datetime) -> datetime:
    """The next time a repeating reminder is due that is later than `after` (a one-off has none: it returns `due`)."""
    if repeat not in ("daily", "weekdays", "weekly", "monthly"):
        return due
    clock, day, step = due.time(), due.date(), 0
    while True:
        step += 1
        if repeat == "monthly":
            candidate = _add_months(due.date(), step)
        else:
            day = day + timedelta(days=7 if repeat == "weekly" else 1)
            if repeat == "weekdays" and day.weekday() >= 5:
                continue
            candidate = day
        moved = datetime.combine(candidate, clock)
        if moved > after:
            return moved


class ReminderStore:
    """`reminders.json`. Reads are cheap (`changed()` says if the file moved); every change reads fresh, changes one thing, saves."""

    def __init__(self, file: Path | None = None):
        self.file = file
        self.reminders: list[Reminder] = []
        self.history: list[dict] = []
        self.seen: datetime | None = None
        self._stamp: tuple[int, int] | None = None
        self._rest: dict = {}
        self.reload()

    @property
    def path(self) -> Path:
        return self.file or userdata.path_of(userdata.REMINDERS_FILE)

    def changed(self) -> bool:
        """Has the file changed since it was last read? (If so, `reload()` it.)"""
        return userdata.stamp(self.path) != self._stamp

    def reload(self) -> None:
        self._stamp = userdata.stamp(self.path)
        data = userdata.read(self.path)
        raw = data.get("reminders")
        self.reminders = [r for r in (Reminder.parse(x) for x in raw[:MAX_REMINDERS]) if r] if isinstance(raw, list) else []
        history = data.get("history")
        self.history = [h for h in history if isinstance(h, dict)][:MAX_HISTORY] if isinstance(history, list) else []
        self.seen = userdata.from_text(data.get("seen"))
        self._rest = {k: v for k, v in data.items() if k not in ("version", "reminders", "history", "seen")}

    def _save(self) -> None:
        data = dict(self._rest)
        data["reminders"] = [r.to_dict() for r in self.reminders]
        data["history"] = self.history[:MAX_HISTORY]
        if self.seen is not None:
            data["seen"] = userdata.to_text(self.seen)
        userdata.write(self.path, data)
        self._stamp = userdata.stamp(self.path)

    def _change(self, apply) -> object:
        self.reload()
        result = apply()
        self._save()
        return result

    # ---- asking ----
    def active(self) -> list[Reminder]:
        """The ones that are on, soonest first."""
        return sorted((r for r in self.reminders if r.enabled), key=lambda r: r.due)

    def find(self, words: str) -> list[Reminder]:
        """The reminders whose title has all of these words in it."""
        wanted = [w for w in re.findall(r"[a-z0-9']+", words.lower()) if w not in _FILLER]
        if not wanted:
            return []
        return [r for r in self.reminders if all(w in r.title.lower() for w in wanted)]

    # ---- changing ----
    def add(self, title: str, due: datetime, repeat: str = "none", now: datetime | None = None) -> Reminder:
        title = userdata.clean(title, MAX_TITLE) or "Reminder"
        reminder = Reminder(userdata.new_id("r"), title, due.replace(microsecond=0), repeat if repeat in REPEATS else "none", True,
                            (now or datetime.now()).replace(second=0, microsecond=0))

        def apply() -> Reminder:
            if len(self.reminders) >= MAX_REMINDERS:
                raise ValueError(f"that is as many reminders as Orbit keeps ({MAX_REMINDERS}); delete one first")
            self.reminders.append(reminder)
            return reminder

        return self._change(apply)

    def remove(self, ids: set[str]) -> int:
        def apply() -> int:
            before = len(self.reminders)
            self.reminders = [r for r in self.reminders if r.id not in ids]
            return before - len(self.reminders)

        return self._change(apply)

    def take_due(self, now: datetime) -> list[tuple[Reminder, bool]]:
        """Everything that is due by `now`, each as (reminder, late). Each one is taken: a one-off goes (into the history), a
        repeating one moves on to its next time. One that came due long ago (Orbit was off) is quietly put away, not announced."""
        self.reload()
        if not any(r.enabled and r.due <= now for r in self.reminders):
            return []

        def apply() -> list[tuple[Reminder, bool]]:
            said: list[tuple[Reminder, bool]] = []
            keep: list[Reminder] = []
            for r in self.reminders:
                if not (r.enabled and r.due <= now):
                    keep.append(r)
                    continue
                late = now - r.due > timedelta(minutes=2)
                stale = now - r.due > LATE_GRACE
                if not stale:
                    said.append((r, late))
                    self.history.insert(0, {"id": r.id, "title": r.title, "at": userdata.to_text(now.replace(microsecond=0))})
                if r.repeat != "none":
                    keep.append(Reminder(r.id, r.title, advance(r.due, r.repeat, now), r.repeat, True, r.created, r.extra))
            self.reminders = keep
            del self.history[MAX_HISTORY:]
            return said

        return self._change(apply)


# ---- saying and showing times ---------------------------------------------------------------------------------------------

def clock_text(when: datetime) -> str:
    """5:00 PM"""
    hour = when.hour % 12 or 12
    return f"{hour}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


def when_text(due: datetime, now: datetime) -> str:
    """How a time is said: "in 10 minutes", "today at 5:00 PM", "tomorrow at 9:00 AM", "Monday at 3:00 PM", "25 Nov at 9:00 AM"."""
    delta = due - now
    if timedelta(0) <= delta < timedelta(minutes=60):
        minutes = max(1, round(delta.total_seconds() / 60))
        return "in 1 minute" if minutes == 1 else f"in {minutes} minutes"
    days = (due.date() - now.date()).days
    at = f"at {clock_text(due)}"
    if days == 0:
        return f"today {at}"
    if days == 1:
        return f"tomorrow {at}"
    if 1 < days < 7:
        return f"{_WEEKDAYS[due.weekday()].title()} {at}"
    return f"{due.day} {calendar.month_abbr[due.month]} {at}" if due.year == now.year else f"{due.day} {calendar.month_abbr[due.month]} {due.year} {at}"


def repeat_text(repeat: str) -> str:
    return {"daily": "every day", "weekdays": "every weekday", "weekly": "every week", "monthly": "every month"}.get(repeat, "")


def spoken_when(due: datetime, repeat: str, now: datetime) -> str:
    if repeat == "none":
        return when_text(due, now)
    day = {"daily": "every day", "weekdays": "every weekday", "weekly": f"every {_WEEKDAYS[due.weekday()].title()}",
           "monthly": f"on the {_ordinal(due.day)} of every month"}[repeat]
    return f"{day} at {clock_text(due)}"


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def announcement(reminder: Reminder, late: bool = False) -> str:
    """What Orbit says when it is due."""
    title = reminder.title.strip().rstrip(".!?")
    if title.lower() in ("timer", "your timer"):
        return "Your timer is up."
    if title.lower() == "alarm":
        return "Your alarm is going off."
    return f"{'Reminder, a bit late: ' if late else 'Reminder: '}{title}."


# ---- understanding what was said --------------------------------------------------------------------------------------

_FILLER = frozenset({"a", "an", "the", "my", "me", "to", "that", "about", "of", "for", "reminder", "reminders", "please", "i", "it"})

_INTENT = re.compile(
    r"^(?:(?:hey )?(?:orbit )?)?(?:please\s+)?(?:(?:can|could|would) you\s+(?:please\s+)?)?"
    r"(?P<what>remind me|remind us|set (?:me )?(?:up )?(?:a |an |the )?(?:new )?(?P<kind>reminder|alarm|timer)|"
    r"(?:add|create|make|schedule|new) (?:me )?(?:a |an )?(?:new )?(?P<kind2>reminder|alarm|timer)|"
    r"wake me(?: up)?)\b[\s,:]*", re.I)

_NUM = r"(?:\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty five|forty|fifty|sixty|ninety)"
_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)"
_PAIR = rf"(?:half an? hour|(?:{_NUM}|a couple of|a few)\s*(?:and a half\s+)?{_UNIT}|{_NUM}\s*(?:h|m|s)\b)"
_RELATIVE = re.compile(rf"\b(?:in|after|for)\s+(?P<amount>{_PAIR}(?:(?:\s*,\s*|\s+and\s+|\s+){_PAIR})*)(?=\W|$)", re.I)
_CLOCK = re.compile(
    r"(?:\b(?:at|@|by|around)\s*)?\b(?:(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)\b|"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2})\b|(?:at|@)\s*(?P<h3>\d{1,2})(?!\d)(?:\s*o'?clock)?(?!\s*(?:%|percent|minutes?|hours?|seconds?|days?|weeks?))|"
    r"(?P<word>noon|midnight))", re.I)
_PART = re.compile(r"\b(?:this\s+|in the\s+|(?:tomorrow|today|tonight)\s+)?(?P<part>morning|afternoon|evening|night|tonight)\b", re.I)
_ISO_DATE = re.compile(r"\b(?:on\s+)?(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})\b")
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_MONTH = re.compile(rf"\b(?:on\s+)?(?:the\s+)?(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MONTH_NAMES})\b(?:\s+(?P<y>\d{{4}}))?", re.I)
_MONTH_DAY = re.compile(rf"\b(?:on\s+)?(?P<mon>{_MONTH_NAMES})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y>\d{{4}}))?", re.I)
_DAY_WORDS = re.compile(r"\b(?P<w>day after tomorrow|tomorrow|today)\b", re.I)
_WEEKDAY = re.compile(rf"\b(?:(?P<mod>next|this|on|coming)\s+)?(?P<day>{'|'.join(sorted(_WEEKDAY_ALIASES, key=len, reverse=True))})\b", re.I)
_REPEAT = re.compile(
    r"\b(?:every\s+(?:single\s+)?(?P<each>day|weekday|week|month|morning|afternoon|evening|night|"
    + "|".join(sorted(_WEEKDAY_ALIASES, key=len, reverse=True)) + r")s?|(?P<adv>daily|weekdays|weekly|monthly))\b", re.I)


@dataclass
class When:
    due: datetime
    repeat: str
    spans: list[tuple[int, int]]            # where in the sentence the time was said (so what is left is the thing to remember)


def _seconds_in(amount: str) -> float | None:
    total = 0.0
    text = amount.lower()
    for match in re.finditer(rf"(half an? hour|a couple of|a few|{_NUM})\s*(and a half\s+)?({_UNIT}|h|m|s)?\b", text):
        word, half, unit = match.group(1), match.group(2), match.group(3)
        if word.startswith("half"):
            total += 1800
            continue
        if word == "a couple of":
            count = 2.0
        elif word == "a few":
            count = 3.0
        elif word in _NUMBER_WORDS:
            count = float(_NUMBER_WORDS[word])
        else:
            count = float(word)
        if half:
            count += 0.5
        if not unit:
            return None
        unit = unit[0]
        total += count * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return total or None


def _clock_from(match: re.Match) -> tuple[time, bool] | None:
    """(the time of day, whether it could be morning or evening and is left for us to work out) from a _CLOCK match."""
    word = match.group("word")
    if word:
        return (time(12, 0), False) if word.lower() == "noon" else (time(0, 0), False)
    hour = match.group("h") or match.group("h2") or match.group("h3")
    h, m = int(hour), int(match.group("m") or match.group("m2") or "0")
    ap = (match.group("ap") or "").lower().replace(".", "")
    if m > 59:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
        return time(h % 12 + (12 if ap == "pm" else 0), m), False
    if h > 23:
        return None
    return time(h, m), 1 <= h <= 12 and not hour.startswith("0")


def _resolve_clock(at: time, unsure: bool, day: date | None, now: datetime, morning: bool = False) -> datetime:
    """The moment a time of day means: on `day`, or the next time it comes round. "At 5" is 5 in the afternoon if that is still
    ahead today, and with a day named, 1 to 6 is the afternoon and 7 to 11 the morning (as people say it). An alarm ("wake me
    at 6:30") is always the morning."""
    if not unsure:
        due = datetime.combine(day or now.date(), at)
        return due if day is not None or due > now else due + timedelta(days=1)
    h, m = at.hour, at.minute
    preferred = h if morning else 12 if h == 12 else (h + 12 if h <= 6 else h)
    hours = [h] if morning else [12] if h == 12 else sorted({h, h + 12})
    if day is not None:
        ahead = [o for o in (datetime.combine(day, time(hour, m)) for hour in hours) if o > now]
        if day != now.date():
            return datetime.combine(day, time(preferred, m))
        return ahead[0] if ahead else datetime.combine(day, time(preferred, m))
    ahead = [o for o in (datetime.combine(now.date(), time(hour, m)) for hour in hours) if o > now]
    return ahead[0] if ahead else datetime.combine(now.date() + timedelta(days=1), time(preferred, m))


def parse_when(text: str, now: datetime, morning: bool = False) -> When | None:
    """When something is to happen, from words like "in 10 minutes", "at 5pm", "tomorrow at 9", "every day at 8", "next friday".
    None if no time was said. `morning`: a time with no am or pm is the morning (an alarm)."""
    now = now.replace(microsecond=0)
    rep = _REPEAT.search(text)
    rel = _RELATIVE.search(text)
    if rel is not None and rep is None:
        seconds = _seconds_in(rel.group("amount"))
        if seconds is not None:
            return When(now + timedelta(seconds=round(seconds)), "none", [rel.span()])

    spans: list[tuple[int, int]] = []
    repeat, weekday, day, part_hour = "none", None, None, None
    if rep is not None:
        spans.append(rep.span())
        each = (rep.group("each") or rep.group("adv") or "").lower()
        repeat = {"day": "daily", "daily": "daily", "weekday": "weekdays", "weekdays": "weekdays", "week": "weekly", "weekly": "weekly",
                  "month": "monthly", "monthly": "monthly"}.get(each, "")
        if each in _DAY_PARTS:
            repeat, part_hour = "daily", _DAY_PARTS[each]
        elif each in _WEEKDAY_ALIASES:
            repeat, weekday = "weekly", _WEEKDAY_ALIASES[each]

    for pattern, kind in ((_ISO_DATE, "iso"), (_DAY_MONTH, "dm"), (_MONTH_DAY, "md")):
        m = pattern.search(text)
        if m is None:
            continue
        try:
            if kind == "iso":
                day = date(int(m.group("y")), int(m.group("mo")), int(m.group("d")))
            else:
                month, number = _MONTHS[m.group("mon").lower()], int(m.group("d"))
                year = int(m.group("y")) if m.group("y") else now.year
                day = date(year, month, number)
                if not m.group("y") and day < now.date():
                    day = date(year + 1, month, number)
        except ValueError:
            return None
        spans.append(m.span())
        break

    if day is None:
        word = _DAY_WORDS.search(text)
        if word is not None:
            spans.append(word.span())
            w = word.group("w").lower()
            day = now.date() + timedelta(days=0 if w == "today" else 2 if w.startswith("day after") else 1)
        elif weekday is None:
            m = _WEEKDAY.search(text)
            if m is not None and (m.group("day").lower() in _WEEKDAYS or m.group("mod")):
                spans.append(m.span())
                weekday = _WEEKDAY_ALIASES[m.group("day").lower()]
                if (m.group("mod") or "").lower() == "next" and weekday == now.weekday():
                    day = now.date() + timedelta(days=7)

    clock = None
    for m in _CLOCK.finditer(text):
        clock = _clock_from(m)
        if clock is not None:
            spans.append(m.span())
            break
    part = _PART.search(text)
    if part is not None:
        spans.append(part.span())
        if clock is None and part_hour is None:
            part_hour = _DAY_PARTS[part.group("part").lower()]
            if part.group("part").lower() == "tonight" and day is None and weekday is None:
                day = now.date()

    if rep is None and day is None and weekday is None and clock is None and part_hour is None:
        return None

    if clock is not None:
        at, unsure = clock
    else:
        at, unsure = time(part_hour if part_hour is not None else DEFAULT_HOUR, 0), False
    if weekday is not None and day is None:
        ahead = (weekday - now.weekday()) % 7
        day = now.date() + timedelta(days=ahead)
        if ahead == 0 and _resolve_clock(at, unsure, day, now, morning) <= now:
            day += timedelta(days=7)
    if repeat != "none" and day is None and unsure:
        # "every day at 8": the usual hour (7 to 11 morning, 1 to 6 afternoon), from today if it is still ahead, else the next time
        h = at.hour
        at, unsure = time(12 if h == 12 else (h if morning or h >= 7 else h + 12), at.minute), False
    due = _resolve_clock(at, unsure, day, now, morning)
    if repeat != "none" and due <= now:
        due = advance(due, repeat, now)
    return When(due, repeat, spans)


@dataclass
class Request:
    kind: str                # reminder | alarm | timer
    title: str
    due: datetime
    repeat: str


def _title_from(text: str, spans: list[tuple[int, int]], kind: str) -> str:
    """What is left of the sentence once the time words are taken out: the thing to be reminded of."""
    keep, cursor = [], 0
    for start, end in sorted(spans):
        if start < cursor:
            continue
        keep.append(text[cursor:start])
        cursor = end
    keep.append(text[cursor:])
    rest = " ".join("".join(keep).split())
    rest = re.sub(r"^(?:and\s+)?(?:to|that|about|of|for|on|at|in)\s+", "", rest, flags=re.I)
    rest = re.sub(r"\s+(?:at|on|in|by|for|around|and)$", "", rest, flags=re.I).strip(" ,.;:-")
    rest = re.sub(r"^(?:to|that|about)\s+", "", rest, flags=re.I)
    if re.fullmatch(r"(?:to|that|about|of|for|on|at|in|by|and|around)", rest, re.I):
        rest = ""
    if not rest:
        return {"alarm": "Alarm", "timer": "Timer"}.get(kind, "Reminder")
    return rest[0].upper() + rest[1:]


def parse_request(text: str, now: datetime) -> Request | str | None:
    """"remind me to call mom at 5pm" -> a Request. None if this is not a request to set a reminder; a sentence (what to tell the
    user) if something is missing or cannot be done ("when should I remind you?")."""
    said = " ".join(text.split())
    intent = _INTENT.match(said)
    if intent is None:
        return None
    kind = (intent.group("kind") or intent.group("kind2") or ("alarm" if intent.group("what").lower().startswith("wake") else "reminder")).lower()
    rest = said[intent.end():]
    when = parse_when(rest, now, morning=kind == "alarm")
    if when is None:
        if kind == "timer":
            return "For how long? Say, for example, set a timer for ten minutes."
        return "When should I remind you? Say it with a time, like remind me to stretch in ten minutes, or tomorrow at nine."
    if when.due <= now and when.repeat == "none":
        return "That time has already passed. Say a time that is still ahead."
    return Request(kind, _title_from(rest, when.spans, kind), when.due, when.repeat)

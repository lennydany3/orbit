"""Orbit's settings file: what the Settings page (the window) saves and Orbit reads.

One JSON file, `settings.json` in the project folder (or wherever ORBIT_SETTINGS points). The window writes it; this module only
reads it, and never trusts it: a missing file, broken JSON or a bad entry never stops Orbit, it just falls back to the defaults
or drops that one entry, and `Settings.problems` says what was ignored.

    {
      "version": 1,
      "hotkeys": {"orb": "ctrl+alt+o", "yes": "ctrl+alt+y", "no": "ctrl+alt+n", "talk": "ctrl_r"},
      "commands": [
        {"id": "work", "name": "Work mode", "enabled": true, "phrases": ["work mode"],
         "steps": [{"kind": "open", "target": "Spotify"}, {"kind": "keys", "keys": "win+shift+s"},
                   {"kind": "type", "text": "hello"}, {"kind": "wait", "seconds": 1.5}]}
      ]
    }

The window checks the hotkey chords itself (orb, yes, no); here only the hold-to-talk key and the commands are checked.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

SCHEMA_VERSION = 1
DEFAULT_HOTKEYS = {"orb": "ctrl+alt+o", "yes": "ctrl+alt+y", "no": "ctrl+alt+n", "talk": "ctrl_r"}

MAX_COMMANDS = 50
MAX_PHRASES = 8
MAX_STEPS = 20
MAX_NAME = 60
MAX_PHRASE = 80
MAX_TARGET = 300
MAX_WAIT_S = 30.0

STEP_KINDS = ("open", "keys", "type", "wait")

# Hold-to-talk keys, by their pynput names (voice/hotkey.py turns these into real keys). Or any single character.
TALK_KEYS = frozenset(
    ["ctrl_r", "ctrl_l", "alt_r", "alt_l", "alt_gr", "shift_r", "shift_l", "caps_lock", "scroll_lock", "num_lock", "pause", "insert",
     "home", "end", "page_up", "page_down", "menu", "print_screen", "tab", "space"] + [f"f{n}" for n in range(1, 25)])


def path() -> Path:
    """Where the settings file is."""
    return Path(os.environ.get("ORBIT_SETTINGS") or config.PROJECT_ROOT / "settings.json")


@dataclass(frozen=True)
class Step:
    kind: str                 # open | keys | type | wait
    value: str = ""           # open: what to open; keys: the combination; type: the text
    seconds: float = 0.0      # wait only

    def describe(self) -> str:
        """What this step does, as it is shown and read out when Orbit asks before running it."""
        if self.kind == "keys":
            return f"press {self.value}"
        if self.kind == "type":
            return f'type "{self.value}"'
        if self.kind == "wait":
            return f"wait {self.seconds:g} seconds"
        return f"open {self.value}"


@dataclass(frozen=True)
class Command:
    id: str
    name: str
    phrases: tuple[str, ...]
    steps: tuple[Step, ...]
    enabled: bool = True

    @property
    def sends_keys(self) -> bool:
        """Does it press keys or type into whatever window is in front? Those need a yes first."""
        return any(s.kind in ("keys", "type") for s in self.steps)

    def describe(self) -> str:
        return ", then ".join(s.describe() for s in self.steps)


@dataclass
class Settings:
    hotkeys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    commands: list[Command] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)     # what was ignored, and why

    @property
    def talk_key(self) -> str:
        return self.hotkeys["talk"]


def _clean(text: object, limit: int) -> str:
    return re.sub(r"\s+", " ", text).strip() if isinstance(text, str) and len(text) <= limit * 4 else ""


def normalize_phrase(text: str) -> str:
    """How a phrase is compared with what was said: lowercase, no punctuation, single spaces ("Work mode!" == "work  mode")."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower().replace("'", "")).split())


def valid_talk_key(name: object) -> bool:
    return isinstance(name, str) and (name in TALK_KEYS or (len(name) == 1 and name.isprintable() and not name.isspace()))


def parse_step(raw: object) -> tuple[Step | None, str]:
    """One step from the file -> (step, "") or (None, why not)."""
    if not isinstance(raw, dict):
        return None, "a step is not an object"
    kind = raw.get("kind")
    if kind == "open":
        target = _clean(raw.get("target"), MAX_TARGET)
        return (Step("open", target), "") if target else (None, "an open step has nothing to open")
    if kind == "keys":
        keys = raw.get("keys")
        if not isinstance(keys, str):
            return None, "a keys step has no keys"
        from .tools.os_layer import keyboard

        try:
            return Step("keys", str(keyboard.parse_chord(keys))), ""
        except keyboard.KeyError_ as e:
            return None, f"keys '{keys}': {e}"
    if kind == "type":
        text = raw.get("text")
        if not isinstance(text, str) or not text:
            return None, "a type step has no text"
        from .tools.os_layer import keyboard

        if len(text) > keyboard.MAX_TEXT_CHARS:
            return None, f"typed text is longer than {keyboard.MAX_TEXT_CHARS} characters"
        return Step("type", text), ""
    if kind == "wait":
        seconds = raw.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= MAX_WAIT_S:
            return None, f"a wait must be more than 0 and at most {MAX_WAIT_S:g} seconds"
        return Step("wait", seconds=float(seconds)), ""
    return None, f"unknown step '{kind}'"


def parse_command(raw: object, seen_phrases: dict[str, str]) -> tuple[Command | None, list[str]]:
    """One command from the file -> (command, notes) or (None, why not). `seen_phrases` maps a phrase to the command that has it."""
    if not isinstance(raw, dict):
        return None, ["a command is not an object"]
    name = _clean(raw.get("name"), MAX_NAME)[:MAX_NAME]
    label = f"'{name}'" if name else "a command with no name"
    if not name:
        return None, [f"{label} was ignored"]
    notes: list[str] = []
    phrases: list[str] = []
    for p in raw.get("phrases") if isinstance(raw.get("phrases"), list) else []:
        phrase = normalize_phrase(p) if isinstance(p, str) and len(p) <= MAX_PHRASE * 2 else ""
        if not phrase or len(phrase) > MAX_PHRASE:
            continue
        if phrase in seen_phrases:
            notes.append(f"{label}: '{phrase}' already belongs to '{seen_phrases[phrase]}', so it was skipped")
        elif phrase not in phrases and len(phrases) < MAX_PHRASES:
            phrases.append(phrase)
    steps: list[Step] = []
    for raw_step in raw.get("steps") if isinstance(raw.get("steps"), list) else []:
        step, why = parse_step(raw_step)
        if step is None:
            return None, notes + [f"{label} was ignored: {why}"]
        steps.append(step)
    if not steps:
        return None, notes + [f"{label} was ignored: it has no steps"]
    if len(steps) > MAX_STEPS:
        return None, notes + [f"{label} was ignored: more than {MAX_STEPS} steps"]
    if not phrases:
        return None, notes + [f"{label} was ignored: it has nothing to say to start it"]
    cid = _clean(raw.get("id"), 40)[:40] or re.sub(r"\W+", "-", name.lower()).strip("-") or "command"
    enabled = raw.get("enabled", True)
    return Command(cid, name, tuple(phrases), tuple(steps), enabled if isinstance(enabled, bool) else True), notes


def parse(data: object) -> Settings:
    """The settings from decoded JSON. Anything wrong is left out and listed in `problems`."""
    settings = Settings()
    if not isinstance(data, dict):
        settings.problems.append("the settings file is not a JSON object, so the defaults are used")
        return settings
    hotkeys = data.get("hotkeys")
    if isinstance(hotkeys, dict):
        for key, default in DEFAULT_HOTKEYS.items():
            value = hotkeys.get(key)
            if value is None:
                continue
            if key == "talk":
                if valid_talk_key(value):
                    settings.hotkeys[key] = value
                else:
                    settings.problems.append(f"the talk key {value!r} is not one Orbit knows, so {default} is used")
            elif isinstance(value, str) and value.strip() and len(value) <= 40:
                settings.hotkeys[key] = value.strip().lower()
    raw_commands = data.get("commands")
    if isinstance(raw_commands, list):
        seen: dict[str, str] = {}
        for raw in raw_commands[:MAX_COMMANDS]:
            command, notes = parse_command(raw, seen)
            settings.problems += notes
            if command is None:
                continue
            if command.enabled:
                seen.update({p: command.name for p in command.phrases})
            settings.commands.append(command)
        if len(raw_commands) > MAX_COMMANDS:
            settings.problems.append(f"only the first {MAX_COMMANDS} commands are used")
    return settings


def load(file: Path | None = None) -> Settings:
    """The settings, or the defaults if there is no file (or it cannot be read)."""
    file = file or path()
    try:
        text = file.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return Settings()
    except OSError as e:
        return Settings(problems=[f"could not read {file.name}: {e}"])
    try:
        return parse(json.loads(text))
    except ValueError as e:
        return Settings(problems=[f"{file.name} is not valid JSON ({e}), so the defaults are used"])

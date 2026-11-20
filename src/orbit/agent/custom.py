"""The user's own commands (made in the Settings page): a phrase that starts a list of steps.

Matching is exact (after dropping filler like "please" and a leading "run"/"start"), so a custom command never steals an ordinary
request. A command that presses keys or types text goes through Safety first, as one question listing everything it will do.
The steps themselves are not the language model's to decide: it never sees these commands and cannot press keys or type.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import settings
from ..tools.base import ToolResult
from ..tools.os_layer import folders, keyboard
from . import rules

_RUN_VERBS = re.compile(r"^(?:run|start|do|activate|trigger|launch|execute|begin)\s+(?:my\s+|the\s+)?", re.I)
_URL = re.compile(r"^(?:https?://\S+|(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:[/?#]\S*)?)$", re.I)
_NOT_WEBSITES = frozenset({"exe", "lnk", "bat", "cmd", "msi", "ps1", "dll", "txt", "py", "js", "json", "md", "zip", "pdf"})
_PATH_LIKE = re.compile(r"^(?:[a-z]:[\\/]|\\\\|~|%|\.{1,2}[\\/])|[\\/]", re.I)
SETTLE_S = 0.4                                   # a moment between steps: windows need time to appear and take the keys


def classify_target(target: str) -> tuple[str, str]:
    """What an "open" step means: ("url", address) | ("folder", path) | ("app", name)."""
    t = target.strip()
    if re.match(r"^https?://\S+$", t, re.I):
        return "url", t
    if _URL.match(t) and re.split(r"[/?#]", t, maxsplit=1)[0].rsplit(".", 1)[-1].lower() not in _NOT_WEBSITES:
        return "url", "https://" + t                # "github.com", "www.youtube.com/watch?v=1"
    if _PATH_LIKE.search(t):
        return "folder", t
    return "app", t


class CustomCommands:
    """The commands in the settings file, reloaded whenever the file changes (so a command made in the window works at once)."""

    def __init__(self, file: Path | None = None):
        self.file = file
        self._stamp: tuple[int, int] | None = None
        self._loaded = settings.Settings()
        self.refresh(force=True)

    def _path(self) -> Path:
        return self.file or settings.path()

    def refresh(self, force: bool = False) -> bool:
        """Reads the file again if it changed. True if it did."""
        try:
            st = self._path().stat()
            stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if not force and stamp == self._stamp:
            return False
        self._stamp, self._loaded = stamp, settings.load(self._path())
        return True

    @property
    def commands(self) -> list[settings.Command]:
        return self._loaded.commands

    @property
    def problems(self) -> list[str]:
        return self._loaded.problems

    def match(self, text: str) -> settings.Command | None:
        """The enabled command whose phrase is what was said, if any."""
        self.refresh()
        said = settings.normalize_phrase(rules.normalize(text))
        if not said:
            return None
        forms = {said, settings.normalize_phrase(_RUN_VERBS.sub("", said))}
        for command in self.commands:
            if command.enabled and forms & set(command.phrases):
                return command
        return None


@dataclass
class Hooks:
    """What a command's steps use to touch the PC. Replaceable, so tests never press a real key."""
    press_keys: Callable[[str], object] = keyboard.press_keys
    type_text: Callable[[str], object] = keyboard.type_text
    open_folder: Callable[[str], ToolResult] = folders.open_folder
    sleep: Callable[[float], None] = time.sleep


def _fail(result: ToolResult) -> str:
    """The reason, short enough to say aloud ("could not complete ... Last error: X" -> "X")."""
    error = result.error or "it did not work"
    return error.split("Last error: ", 1)[-1].strip()


def run(command: settings.Command, agent, hooks: Hooks | None = None) -> tuple[bool, str]:
    """Runs a command's steps one after another. Returns (ok, what to tell the user). Stops at the first step that fails."""
    hooks = hooks or Hooks()
    if command.sends_keys:
        ok, why = agent.safety.approve_command(command.name, command.describe())
        if not ok:
            return True, f"Okay, I did not run {command.name}."          # declining is an answer, not a failure
    total = len(command.steps)
    for number, step in enumerate(command.steps, 1):
        agent.log("custom", f"{command.name} step {number}/{total}: {step.describe()}")
        problem = _run_step(step, agent, hooks)
        if problem:
            return False, f"Stopped {command.name} at step {number} of {total} ({step.describe()}): {problem}"
        if step.kind != "wait" and number < total:
            hooks.sleep(SETTLE_S)
    return True, f"Done: {command.name}."


def _open_like_spoken(target: str, agent) -> str:
    """An "open" step that is not an address or a folder does what saying "open <it>" does: "my github" is your GitHub page,
    "youtube" the site, "spotify" the app. Nothing else is read into it."""
    match = agent._rule_for(f"open {target}")
    if match is not None:
        ok, reply, _ = agent._run_rule(match)
        return "" if ok else reply.removeprefix("Failed: ").split("Last error: ", 1)[-1].strip()
    result = agent.execute("os_open_app", {"name": target})
    return "" if result.ok else _fail(result)


def _run_step(step: settings.Step, agent, hooks: Hooks) -> str:
    """"" if it worked, otherwise what went wrong."""
    try:
        if step.kind == "wait":
            hooks.sleep(step.seconds)
        elif step.kind == "keys":
            hooks.press_keys(step.value)
        elif step.kind == "type":
            hooks.type_text(step.value)
        else:
            kind, target = classify_target(step.value)
            if kind == "folder":
                result = hooks.open_folder(target)
            elif kind == "url":
                result = agent.execute("browser_open", {"url": target})
            else:
                return _open_like_spoken(target, agent)
            if not result.ok:
                return _fail(result)
    except (keyboard.KeyError_, ValueError, OSError) as e:
        return str(e)
    return ""

"""The user's own commands: matching what was said, running the steps in order, the one yes for keys and typing."""
import json
import os
import time

import pytest
from pydantic import BaseModel

from orbit.agent.custom import CustomCommands, Hooks, classify_target, run
from orbit.agent.loop import Agent
from orbit.agent.router import Router
from orbit.agent.safety import Safety
from orbit.tools.base import Registry, Tool, ToolResult
from orbit.tools.os_layer import folders
from orbit.voice.loop import describe_action


def write(path, commands, **extra):
    path.write_text(json.dumps({"commands": commands, **extra}), encoding="utf-8")


def bump(path, seconds=5):
    """Makes the file look newer than whatever was read before (two writes in one test can share a timestamp)."""
    os.utime(path, ns=(time.time_ns() + seconds * 1_000_000_000,) * 2)


def cmd(name="Work mode", phrases=("work mode",), steps=None, **kw):
    return {"name": name, "phrases": list(phrases), "steps": steps or [{"kind": "open", "target": "Spotify"}]} | kw


class Open(BaseModel):
    name: str = ""
    url: str = ""


class Rig:
    """An agent with stand-in open tools, hooks that record instead of pressing, and a settings file in a temp folder."""

    def __init__(self, tmp_path, commands, confirm=lambda q: True, fail=()):
        self.file = tmp_path / "settings.json"
        write(self.file, commands)
        self.said, self.asked, self.logs, self.opened, self.naps = [], [], [], [], []
        registry = Registry()

        def opener(label):
            def run_(p):
                target = p.name or p.url
                self.opened.append((label, target))
                return ToolResult.failure(f"{target} not found", retryable=False) if target in fail else ToolResult.success("ok")
            return run_

        for name, label in (("os_open_app", "app"), ("browser_open", "url")):
            registry.register(Tool(name=name, description="x", params=Open, layer="os" if label == "app" else "browser", risk="low",
                                   run=opener(label)))

        def ask(question):
            self.asked.append(question)
            return confirm(question)

        log = lambda kind, msg: self.logs.append((kind, msg))  # noqa: E731
        self.agent = Agent(None, registry, Router(log), Safety(confirm=ask), log, friend_mode=False, custom=CustomCommands(self.file))
        self.hooks = Hooks(press_keys=lambda k: self.said.append(("keys", k)), type_text=lambda t: self.said.append(("type", t)),
                           open_folder=lambda p: (self.said.append(("folder", p)), ToolResult.success("opened"))[1], sleep=self.naps.append)


@pytest.fixture
def recorded(monkeypatch):
    """The agent builds its own Hooks when it runs a command: make those the recording ones."""
    holder = {}
    monkeypatch.setattr("orbit.agent.custom.Hooks", lambda: holder["hooks"])
    return holder


# ---------------------------------------------------------------- matching
@pytest.mark.parametrize("said", ["work mode", "Work mode.", "WORK  MODE!", "please work mode", "can you run work mode please",
                                  "start my work mode", "Hey Orbit, work mode"])
def test_what_was_said_matches_a_phrase_however_it_was_dressed_up(tmp_path, said):
    assert Rig(tmp_path, [cmd()]).agent.custom.match(said).name == "Work mode"


@pytest.mark.parametrize("said", ["work", "work mode now please do it", "open work mode", "what is work mode", "", "play music"])
def test_something_merely_similar_does_not_start_a_command(tmp_path, said):
    assert Rig(tmp_path, [cmd()]).agent.custom.match(said) is None


def test_a_switched_off_command_is_not_matched(tmp_path):
    assert Rig(tmp_path, [cmd(enabled=False)]).agent.custom.match("work mode") is None


def test_a_change_saved_in_the_window_is_picked_up_without_a_restart(tmp_path):
    rig = Rig(tmp_path, [cmd()])
    assert rig.agent.custom.match("movie night") is None
    write(rig.file, [cmd(), cmd("Movie night", ["movie night"])])
    bump(rig.file, 5)
    assert rig.agent.custom.match("movie night").name == "Movie night"
    write(rig.file, [])
    bump(rig.file, 9)
    assert rig.agent.custom.match("work mode") is None, "a deleted command stops answering"


def test_a_missing_or_broken_file_means_no_commands_not_a_crash(tmp_path):
    rig = Rig(tmp_path, [cmd()])
    rig.file.write_text("{{{{", encoding="utf-8")
    bump(rig.file)
    assert rig.agent.custom.match("work mode") is None and rig.agent.custom.problems
    rig.file.unlink()
    assert rig.agent.custom.match("work mode") is None


# ---------------------------------------------------------------- what an "open" step means
@pytest.mark.parametrize("target, expected", [
    ("Spotify", ("app", "Spotify")), ("Visual Studio Code", ("app", "Visual Studio Code")), ("spotify.exe", ("app", "spotify.exe")),
    ("https://github.com/x?y=1", ("url", "https://github.com/x?y=1")), ("github.com", ("url", "https://github.com")),
    ("www.youtube.com/watch?v=1", ("url", "https://www.youtube.com/watch?v=1")),
    ("C:\\Users\\me\\Downloads", ("folder", "C:\\Users\\me\\Downloads")), ("~/Documents", ("folder", "~/Documents")),
    ("%USERPROFILE%\\Desktop", ("folder", "%USERPROFILE%\\Desktop")), ("\\\\server\\share", ("folder", "\\\\server\\share")),
    ("javascript:alert(1)", ("app", "javascript:alert(1)")),
])
def test_an_open_step_is_an_address_a_folder_or_an_app(target, expected):
    assert classify_target(target) == expected


# ---------------------------------------------------------------- running
def test_steps_run_in_order_with_a_short_pause_between_them(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "Spotify"}, {"kind": "open", "target": "github.com"},
                                    {"kind": "keys", "keys": "ctrl+t"}, {"kind": "type", "text": "hi"}])])
    assert run(rig.agent.custom.commands[0], rig.agent, rig.hooks) == (True, "Done: Work mode.")
    assert rig.opened == [("app", "Spotify"), ("url", "https://github.com")]
    assert rig.said == [("keys", "ctrl+t"), ("type", "hi")]
    assert rig.naps == [0.4, 0.4, 0.4], "a pause after every step but the last"


def test_a_wait_step_waits_exactly_that_long_and_adds_no_extra_pause(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "Spotify"}, {"kind": "wait", "seconds": 2.5},
                                    {"kind": "open", "target": "Brave"}])])
    run(rig.agent.custom.commands[0], rig.agent, rig.hooks)
    assert rig.naps == [0.4, 2.5]


def test_a_folder_step_opens_the_folder(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "C:\\Users\\Public"}])])
    assert run(rig.agent.custom.commands[0], rig.agent, rig.hooks)[0]
    assert rig.said == [("folder", "C:\\Users\\Public")] and rig.opened == []


def test_a_failing_step_stops_the_command_and_names_the_step(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "Spotify"}, {"kind": "open", "target": "Nope"},
                                    {"kind": "type", "text": "never typed"}])], fail=("Nope",))
    ok, reply = run(rig.agent.custom.commands[0], rig.agent, rig.hooks)
    assert not ok
    assert reply.startswith("Stopped Work mode at step 2 of 3 (open Nope):") and "Nope not found" in reply
    assert rig.said == [], "nothing after the failure ran"


def test_a_key_press_that_windows_refuses_stops_the_command(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "keys", "keys": "ctrl+c"}, {"kind": "open", "target": "Spotify"}])])

    def refuse(_):
        raise OSError("Windows accepted 0 of 4 key events")

    rig.hooks.press_keys = refuse
    ok, reply = run(rig.agent.custom.commands[0], rig.agent, rig.hooks)
    assert not ok and "step 1 of 2 (press ctrl+c)" in reply and "accepted 0 of 4" in reply and rig.opened == []


def test_an_open_step_does_what_saying_open_it_does(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "my github"}, {"kind": "open", "target": "youtube"},
                                    {"kind": "open", "target": "vs code"}])])
    assert run(rig.agent.custom.commands[0], rig.agent, rig.hooks)[0]
    assert rig.opened == [("url", "https://github.com/lennydany3"), ("url", "https://www.youtube.com"), ("app", "Vs Code")]


def test_a_failure_is_worded_to_be_said_aloud_not_read_as_a_log(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "Nope"}])], fail=("Nope",))
    ok, reply = run(rig.agent.custom.commands[0], rig.agent, rig.hooks)
    assert not ok and reply == "Stopped Work mode at step 1 of 1 (open Nope): Nope not found"


# ---------------------------------------------------------------- the one yes
def test_opening_things_needs_no_question(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "Spotify"}, {"kind": "open", "target": "github.com"}])])
    run(rig.agent.custom.commands[0], rig.agent, rig.hooks)
    assert rig.asked == []


def test_pressing_keys_or_typing_asks_once_and_lists_everything_it_will_do(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "Notepad"}, {"kind": "type", "text": "hello"},
                                    {"kind": "keys", "keys": "ctrl+s"}])])
    ok, _ = run(rig.agent.custom.commands[0], rig.agent, rig.hooks)
    assert ok and len(rig.asked) == 1
    assert 'open Notepad, then type "hello", then press ctrl+s' in rig.asked[0]
    assert rig.asked[0].startswith("custom_command(") and rig.asked[0].endswith("is a risky action. Allow?")


def test_saying_no_runs_nothing_at_all_and_is_not_a_failure(tmp_path):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "Notepad"}, {"kind": "keys", "keys": "ctrl+s"}])], confirm=lambda q: False)
    assert run(rig.agent.custom.commands[0], rig.agent, rig.hooks) == (True, "Okay, I did not run Work mode.")
    assert rig.opened == [] and rig.said == []


def test_the_spoken_question_names_the_command_and_its_steps():
    q = "custom_command({'name': 'Work mode', 'steps': 'open Notepad, then type \"hi\"'}) is a risky action. Allow?"
    assert describe_action(q) == 'run your Work mode command: open Notepad, then type "hi"'


# ---------------------------------------------------------------- inside the agent
def test_the_agent_runs_a_command_without_the_model_and_before_any_other_rule(tmp_path, recorded):
    rig = Rig(tmp_path, [cmd("Open it", ["open spotify"], [{"kind": "open", "target": "Brave"}])])
    recorded["hooks"] = rig.hooks
    assert rig.agent.llm is None
    assert rig.agent.handle("open spotify") == "Done: Open it."
    assert rig.opened == [("app", "Brave")], "the user's command beat the built-in 'open spotify' rule"
    assert any(k == "custom" for k, _ in rig.logs)
    assert not rig.agent.last_failed


def test_a_failed_command_marks_the_request_failed(tmp_path, recorded):
    rig = Rig(tmp_path, [cmd(steps=[{"kind": "open", "target": "Nope"}])], fail=("Nope",))
    recorded["hooks"] = rig.hooks
    assert rig.agent.handle("work mode").startswith("Stopped Work mode at step 1")
    assert rig.agent.last_failed


def test_a_sentence_that_only_contains_a_phrase_is_left_to_the_normal_rules(tmp_path, recorded):
    rig = Rig(tmp_path, [cmd()])
    recorded["hooks"] = rig.hooks
    rig.agent.handle("open spotify and then work mode")
    assert not any(k == "custom" for k, _ in rig.logs), "'work mode' was only part of the sentence"


def test_an_agent_built_without_custom_commands_is_unchanged(tmp_path):
    rig = Rig(tmp_path, [cmd()])
    rig.agent.custom = None
    assert rig.agent.handle("work mode").startswith("No rule matches that")


# ---------------------------------------------------------------- folders
def test_a_folder_opens_only_if_it_is_a_folder_that_exists(tmp_path):
    seen = []
    assert folders.open_folder(str(tmp_path), seen.append).ok and seen == [str(tmp_path.resolve())]
    gone = folders.open_folder(str(tmp_path / "missing"), seen.append)
    assert not gone.ok and gone.not_found and "does not exist" in gone.error
    file = tmp_path / "a.exe"
    file.write_text("x")
    refused = folders.open_folder(str(file), seen.append)
    assert not refused.ok and "not a folder" in refused.error and len(seen) == 1, "a program is never started this way"


def test_folder_paths_understand_variables_and_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_DIR", str(tmp_path))
    assert folders.resolve("%MY_DIR%") == tmp_path.resolve()
    assert folders.resolve('"~"') == folders.resolve(os.path.expanduser("~"))

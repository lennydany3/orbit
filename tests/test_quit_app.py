"""Closing windows honestly: administrator windows, apps that hide in the tray, and quitting an app completely.
The real Windows and process calls are replaced by fakes (the live behaviour was checked by hand on the real machine)."""
import psutil
import pytest

from orbit.agent import rules
from orbit.agent.router import Router
from orbit.tools.base import Registry
from orbit.tools.os_layer import CloseParams, ArrangeParams, QuitParams, close_window, arrange_window, quit_app, register_os_tools, processes, windows
from orbit.tools.os_layer.windows import Window


class FakeProc:
    def __init__(self, pid=1000, exe=r"C:\apps\chatgpt.exe", deny=False):
        self.pid, self._exe, self.deny, self.terminated = pid, exe, deny, False

    def terminate(self):
        if self.deny:
            raise psutil.AccessDenied()
        self.terminated = True


# ------------------------------------------------------------------ administrator windows
@pytest.fixture
def one_window(monkeypatch):
    win = Window(7, "Task Manager", "taskmgr")
    monkeypatch.setattr(windows, "list_windows", lambda: [win])
    monkeypatch.setattr(windows, "console_hwnd", lambda: 0)
    return win


def test_closing_an_administrator_window_says_why_instead_of_pretending(one_window, monkeypatch):
    monkeypatch.setattr(windows, "needs_admin", lambda hwnd: True)
    monkeypatch.setattr(windows, "close", lambda hwnd: pytest.fail("must not even try: Windows would refuse"))
    r = close_window(CloseParams(title="task manager"))
    assert not r.ok and not r.retryable and not r.not_found
    assert "runs as administrator" in r.error and "Run as administrator" in r.error


def test_a_refused_close_request_is_reported_not_hidden(one_window, monkeypatch):
    monkeypatch.setattr(windows, "needs_admin", lambda hwnd: False)       # elevation unknown, but Windows says access denied
    monkeypatch.setattr(windows, "close", lambda hwnd: 5)
    r = close_window(CloseParams(title="task manager"))
    assert not r.ok and "refused" in r.error and "Run as administrator" in r.error


@pytest.mark.parametrize("action, verb", [("minimize", "minimize"), ("maximize", "maximize"), ("left", "move")])
def test_minimizing_or_snapping_an_administrator_window_is_refused_clearly(one_window, monkeypatch, action, verb):
    monkeypatch.setattr(windows, "needs_admin", lambda hwnd: True)
    r = arrange_window(ArrangeParams(title="task manager", action=action))
    assert not r.ok and f"does not let Orbit {verb} it" in r.error


def test_elevation_check_is_safe_for_handles_that_are_not_windows():
    assert windows.is_elevated(0) is None and windows.needs_admin(0) is False


# ------------------------------------------------------------------ apps that hide in the tray
@pytest.fixture
def tray(monkeypatch):
    win = Window(9, "ChatGPT", "chatgpt")
    state = {"windows": [win]}
    monkeypatch.setattr(windows, "list_windows", lambda: list(state["windows"]))
    monkeypatch.setattr(windows, "console_hwnd", lambda: 0)
    monkeypatch.setattr(windows, "needs_admin", lambda hwnd: False)
    monkeypatch.setattr(windows, "close", lambda hwnd: state["windows"].clear() or 0)       # the window goes away
    monkeypatch.setattr(processes, "window_exe_path", lambda hwnd: r"C:\apps\chatgpt.exe")
    monkeypatch.setattr(processes, "wait_until_gone", lambda path, seconds: state.get("left", [FakeProc()]))
    return state


def test_closing_a_tray_app_warns_that_it_is_still_running(tray):
    r = close_window(CloseParams(title="chatgpt"))
    assert r.ok and "still running in the background" in r.summary and "quit ChatGPT" in r.summary


def test_a_normal_close_stays_a_plain_report(tray):
    tray["left"] = []                                                       # the process ended with its window
    r = close_window(CloseParams(title="chatgpt"))
    assert r.ok and r.summary == "asked 'ChatGPT' to close"


def test_no_warning_when_another_window_of_the_same_app_is_still_open(tray, monkeypatch):
    other = Window(10, "ChatGPT - second window", "chatgpt")
    monkeypatch.setattr(windows, "list_windows", lambda: [other] if not tray["windows"] else [tray["windows"][0], other])
    r = close_window(CloseParams(title="chatgpt"))
    assert r.ok and "background" not in r.summary


def test_the_desktop_shell_never_counts_as_hiding_in_the_tray(tray, monkeypatch):
    monkeypatch.setattr(windows, "list_windows", lambda: list(tray["windows"]))
    explorer = Window(11, "kora - File Explorer", "explorer")
    tray["windows"][:] = [explorer]
    r = close_window(CloseParams(title="file explorer"))
    assert r.ok and "background" not in r.summary


# ------------------------------------------------------------------ quitting completely
def test_quit_is_registered_as_risky_so_the_safety_gate_asks_first():
    registry = Registry()
    register_os_tools(registry)
    assert next(t for t in registry.all() if t.name == "os_quit_app").risk == "confirm"


@pytest.mark.parametrize("stem", ["explorer", "svchost", "ollama", "python", "windowsterminal"])
def test_system_and_orbits_own_programs_are_never_quit(monkeypatch, stem):
    app = processes.AppProcesses("x", rf"C:\Windows\{stem}.exe", stem, [])
    assert "won't quit" in processes.refusal_for(app)


def test_the_program_orbit_runs_inside_is_never_quit(monkeypatch):
    me = psutil.Process()
    monkeypatch.setattr(processes, "running_with_exe", lambda path: [me])
    assert "Orbit is running inside it" in processes.refusal_for(processes.AppProcesses("x", r"C:\apps\code.exe", "code", []))


def test_quit_an_app_that_is_not_running(monkeypatch):
    monkeypatch.setattr(processes, "find_app", lambda q, wins: "nothing running matches 'zzz'")
    r = quit_app(QuitParams(name="zzz"))
    assert not r.ok and r.not_found and "nothing running" in r.error


def _found(monkeypatch, exe_stem="chatgpt", hwnds=()):
    app = processes.AppProcesses("ChatGPT", rf"C:\apps\{exe_stem}.exe", exe_stem, list(hwnds))
    monkeypatch.setattr(processes, "find_app", lambda q, wins: app)
    monkeypatch.setattr(processes, "refusal_for", lambda a: "")
    monkeypatch.setattr(windows, "list_windows", lambda: [])
    monkeypatch.setattr(windows, "orbit_is_admin", lambda: False)
    monkeypatch.setattr(windows, "is_elevated", lambda h: False)
    return app


def test_quit_ends_every_process_and_reports_how_many(monkeypatch):
    _found(monkeypatch)
    monkeypatch.setattr(processes, "quit_all", lambda app: (12, ""))
    r = quit_app(QuitParams(name="ChatGPT"))
    assert r.ok and r.summary == "quit ChatGPT completely (12 process(es) ended)"


def test_quit_reports_an_administrator_app_it_cannot_end(monkeypatch):
    _found(monkeypatch)
    monkeypatch.setattr(processes, "quit_all", lambda app: (0, "access denied"))
    r = quit_app(QuitParams(name="ChatGPT"))
    assert not r.ok and "runs as administrator" in r.error


def test_quit_refuses_when_the_window_is_elevated_before_trying_anything(monkeypatch):
    _found(monkeypatch, hwnds=[3])
    monkeypatch.setattr(windows, "is_elevated", lambda h: True)
    monkeypatch.setattr(processes, "quit_all", lambda app: pytest.fail("must not try"))
    r = quit_app(QuitParams(name="ChatGPT"))
    assert not r.ok and "runs as administrator" in r.error


def test_quit_that_cannot_finish_says_so(monkeypatch):
    _found(monkeypatch)
    monkeypatch.setattr(processes, "quit_all", lambda app: (3, "2 process(es) would not end"))
    r = quit_app(QuitParams(name="ChatGPT"))
    assert not r.ok and "would not end" in r.error


# ------------------------------------------------------------------ what you can say
@pytest.mark.parametrize("text, name", [
    ("quit chatgpt", "chatgpt"), ("Quit ChatGPT.", "ChatGPT"), ("exit spotify", "spotify"), ("kill chrome", "chrome"),
    ("force close the calculator", "calculator"), ("shut down discord", "discord"), ("quit chatgpt completely", "chatgpt"),
    ("close chatgpt completely", "chatgpt"), ("please close the clock for good", "clock"),
])
def test_quit_phrases_go_to_quit_app_and_keep_your_capitalisation(text, name):
    m = rules.match(text.rstrip("."))
    assert m and m.tool == "os_quit_app" and m.args == {"name": name}


@pytest.mark.parametrize("text", ["close chatgpt", "close the clock", "exit fullscreen", "quit", "exit", "quit it", "quit orbit", "close it"])
def test_plain_close_and_other_commands_are_left_alone(text):
    m = rules.match(text)
    assert m is None or m.tool != "os_quit_app"
    if text == "close chatgpt":
        assert m.tool == "os_close_window"
    if text == "exit fullscreen":
        assert m.rule == "fullscreen-off"

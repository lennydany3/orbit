"""Monitors and moving windows between them, with fake monitors and windows (the real thing is checked by hand)."""
import pytest

from orbit.agent.router import Router
from orbit.tools.base import Registry
from orbit.tools.os_layer import ArrangeParams, MoveParams, NoParams, monitors, register_os_tools, windows
from orbit.tools.os_layer.monitors import Monitor, MonitorNotFound, resolve
from orbit.tools.os_layer.windows import Window

# This PC: a 1920x1080 primary, and a secondary to the LEFT of it.
PRIMARY = Monitor(2, 0, 0, 1920, 1080, (0, 0, 1920, 1040), True)
SECOND = Monitor(1, -1920, 0, 0, 1080, (-1920, 0, 0, 1080), False)
MONS = sorted([PRIMARY, SECOND], key=lambda m: (not m.primary, m.left))


@pytest.mark.parametrize("spec, current, expected", [
    ("primary", SECOND, PRIMARY), ("my main monitor", SECOND, PRIMARY), ("monitor 1", SECOND, PRIMARY),
    ("secondary", PRIMARY, SECOND), ("the second screen", PRIMARY, SECOND), ("my secondary monitor", PRIMARY, SECOND),
    ("other", PRIMARY, SECOND), ("the other screen", SECOND, PRIMARY), ("next", PRIMARY, SECOND),
    ("left", PRIMARY, SECOND), ("right monitor", SECOND, PRIMARY), ("monitor 2", PRIMARY, SECOND),
])
def test_resolve_monitor_words(spec, current, expected):
    assert resolve(spec, MONS, current) == expected


def test_one_monitor_is_a_clear_error():
    with pytest.raises(MonitorNotFound, match="only one monitor"):
        resolve("secondary", [PRIMARY], PRIMARY)


def test_unknown_monitor_lists_the_real_ones():
    with pytest.raises(MonitorNotFound, match="monitor 1 .primary.*monitor 2 .secondary."):
        resolve("purple", MONS, PRIMARY)


class FakeDesktop:
    """Windows with a rectangle, a monitor and a maximized/minimized state; SetWindowPos-like behaviour."""

    def __init__(self):
        self.win = Window(10, "(201) YouTube - Brave", "brave")
        self.rect = [100, 100, 1100, 800]   # visible rect, on the primary monitor
        self.maximized = False
        self.minimized = False
        self.ignore_moves = False

    def install(self, mp):
        mp.setattr(windows, "list_windows", lambda: [self.win])
        mp.setattr(windows, "console_hwnd", lambda: 0)
        mp.setattr(monitors, "list_monitors", lambda: MONS)
        mp.setattr(monitors, "monitor_of", lambda hwnd, found=None: self.monitor())
        mp.setattr(windows, "is_maximized", lambda h: self.maximized)
        mp.setattr(windows, "is_minimized", lambda h: self.minimized)
        mp.setattr(windows, "maximize", lambda h: setattr(self, "maximized", True))
        mp.setattr(windows, "minimize", lambda h: setattr(self, "minimized", True))
        mp.setattr(windows, "restore", lambda h: (setattr(self, "maximized", False), setattr(self, "minimized", False)))
        mp.setattr(windows, "visible_rect", lambda h: tuple(self.rect))
        mp.setattr(windows, "set_rect", self.set_rect)

    def monitor(self):
        cx = (self.rect[0] + self.rect[2]) // 2
        return next(m for m in MONS if m.left <= cx < m.right)

    def set_rect(self, hwnd, left, top, width, height):
        if not self.ignore_moves:
            self.rect = [left, top, left + width, top + height]


@pytest.fixture
def desk(monkeypatch, logs):
    d = FakeDesktop()
    d.install(monkeypatch)
    monkeypatch.setattr("orbit.tools.os_layer.SETTLE_TIMEOUT_S", 0.2)
    reg = Registry()
    register_os_tools(reg)
    router = Router(lambda k, m: logs.append((k, m)))
    d.run = lambda name, params: router.run(reg.alternatives(reg.get_exposed(name)), params)
    return d


def test_move_to_the_secondary_monitor_and_verify(desk):
    r = desk.run("os_move_window", MoveParams(title="brave", monitor="my secondary monitor"))
    assert r.ok and r.verified and desk.monitor() is SECOND and "monitor 2 (secondary)" in r.summary
    assert desk.rect[2] - desk.rect[0] == 1000  # same size, just somewhere else


def test_move_keeps_the_relative_position(desk):
    desk.rect = [960, 0, 1920, 1080]  # the right half of the primary monitor
    desk.run("os_move_window", MoveParams(title="brave", monitor="secondary"))
    assert desk.rect[0] == -1920 + 960 and desk.monitor() is SECOND  # still the right half, now of the other monitor


def test_a_maximized_window_is_restored_moved_and_maximized_again(desk):
    desk.maximized = True
    desk.rect = [0, 0, 1920, 1040]
    r = desk.run("os_move_window", MoveParams(title="brave", monitor="secondary"))
    assert r.ok and desk.maximized and desk.monitor() is SECOND and "still maximized" in r.summary


def test_a_window_bigger_than_the_target_is_shrunk_to_fit(desk, monkeypatch):
    desk.rect = [0, 0, 1900, 1040]
    desk.run("os_move_window", MoveParams(title="brave", monitor="secondary"))
    assert desk.rect[2] - desk.rect[0] <= 1920 and desk.monitor() is SECOND


def test_already_there_does_nothing(desk):
    r = desk.run("os_move_window", MoveParams(title="brave", monitor="primary"))
    assert r.ok and "already on monitor 1" in r.summary and desk.rect == [100, 100, 1100, 800]


def test_a_move_that_the_app_refuses_is_reported_not_faked(desk):
    desk.ignore_moves = True
    r = desk.run("os_move_window", MoveParams(title="brave", monitor="secondary"))
    assert not r.ok and "still on a different monitor" in r.error


def test_moving_never_repeats_itself(desk, logs):
    desk.ignore_moves = True
    desk.run("os_move_window", MoveParams(title="brave", monitor="secondary"))
    assert "retry" not in [k for k, _ in logs]


def test_unknown_window_lists_the_open_ones(desk):
    r = desk.run("os_move_window", MoveParams(title="photoshop", monitor="secondary"))
    assert not r.ok and r.not_found and "YouTube" in r.error


def test_one_monitor_gives_a_clear_failure(desk, monkeypatch):
    monkeypatch.setattr(monitors, "list_monitors", lambda: [PRIMARY])
    r = desk.run("os_move_window", MoveParams(title="brave", monitor="secondary"))
    assert not r.ok and "only one monitor" in r.error


@pytest.mark.parametrize("action, check", [
    ("maximize", lambda d: d.maximized), ("minimize", lambda d: d.minimized),
])
def test_maximize_and_minimize(desk, action, check):
    r = desk.run("os_arrange_window", ArrangeParams(title="brave", action=action))
    assert r.ok and r.verified and check(desk)


def test_restore_after_maximize(desk):
    desk.maximized = True
    r = desk.run("os_arrange_window", ArrangeParams(title="brave", action="restore"))
    assert r.ok and r.verified and not desk.maximized


@pytest.mark.parametrize("action, rect", [
    ("left", [0, 0, 960, 1040]), ("right", [960, 0, 1920, 1040]), ("top", [0, 0, 1920, 520]), ("bottom", [0, 520, 1920, 1040]),
])
def test_snap_to_half_of_the_monitor_the_window_is_on(desk, action, rect):
    r = desk.run("os_arrange_window", ArrangeParams(title="brave", action=action))
    assert r.ok and r.verified and desk.rect == rect  # uses the work area: the taskbar is not covered


def test_snap_on_the_secondary_monitor_uses_that_monitors_area(desk):
    desk.rect = [-1500, 100, -500, 700]
    desk.run("os_arrange_window", ArrangeParams(title="brave", action="right"))
    assert desk.rect == [-960, 0, 0, 1080]


def test_snap_that_the_app_refuses_is_reported(desk):
    desk.ignore_moves = True
    r = desk.run("os_arrange_window", ArrangeParams(title="brave", action="left"))
    assert not r.ok and "did not end up left" in r.error


def test_monitor_list_tool(desk):
    r = desk.run("os_list_monitors", NoParams())
    assert r.ok and "monitor 1 (primary)" in r.data and "monitor 2 (secondary)" in r.data

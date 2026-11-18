"""OS layer logic tested with the real Windows calls replaced by fakes (live checks are done by hand via the CLI)."""
import dataclasses

import pytest

from orbit.agent.router import Router
from orbit.tools.base import Registry
from orbit.tools.os_layer import (
    ChangeVolumeParams, MediaKeyParams, MuteParams, OpenAppParams, SetVolumeParams, apps, media, register_os_tools,
    volume, windows,
)
from orbit.tools.os_layer.apps import App, resolve_app
from orbit.tools.os_layer.windows import Window, match_windows

APPS = [
    App("Spotify", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"),
    App("Google Chrome", "Chrome"),
    App("Notepad", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"),
    App("Android Studio", r"{6D809377}\Android\Android Studio\bin\studio64.exe"),
    App("Visual Studio Code", r"{X}\Microsoft VS Code\Code.exe"),
    App("Visual Studio Installer", r"{X}\Installer\setup.exe"),
    App("Speedtest", "Ookla.Speedtest_abc!App"),
]


# ---------- app name resolution ----------
@pytest.mark.parametrize("query, expected", [
    ("spotify", "Spotify"), ("SPOTIFY", "Spotify"), ("chrome", "Google Chrome"), ("notepad", "Notepad"),
    ("android studio", "Android Studio"), ("spotfy", "Spotify"),  # typo
    ("visual studio code", "Visual Studio Code"),
])
def test_resolve_finds_app(query, expected):
    app, _ = resolve_app(query, APPS)
    assert app and app.name == expected


@pytest.mark.parametrize("query", ["Speedtest by Ookla", "the speedtest app", "speedtest by ookla desktop"])
def test_resolve_when_the_model_adds_extra_words(query):
    app, _ = resolve_app(query, APPS)
    assert app and app.name == "Speedtest"


def test_resolve_extra_words_picks_the_longest_matching_name():
    apps_ = [App("Visual Studio", "a"), App("Visual Studio Code", "b")]
    app, _ = resolve_app("open visual studio code please", apps_)
    assert app and app.name == "Visual Studio Code"


def test_resolve_refuses_to_guess_when_ambiguous():
    app, suggestions = resolve_app("visual studio", APPS)
    assert app is None and set(suggestions) == {"Visual Studio Code", "Visual Studio Installer"}


def test_resolve_unknown_gives_no_app():
    app, _ = resolve_app("zzzzqqq", APPS)
    assert app is None


def test_app_hints_come_from_the_id():
    assert "spotify" in APPS[0].hints()
    assert "chrome" in APPS[1].hints()
    assert "studio64" in APPS[3].hints()
    assert "app" not in APPS[2].hints()  # generic Store id suffix is ignored


# ---------- window matching ----------
WINS = [
    Window(1, "Chrome extensions - Brave", "brave"),
    Window(2, "New Tab - Google Chrome", "chrome"),
    Window(3, "Artist - Song", "spotify"),
]


def test_match_prefers_process_name_over_title_mention():
    assert [w.hwnd for w in match_windows(WINS, ["chrome"])][0] == 2


def test_match_by_process_when_title_has_no_app_name():
    assert [w.hwnd for w in match_windows(WINS, ["spotify"])] == [3]


@pytest.mark.parametrize("query", ["Speedtest by Ookla", "speed test", "the Speedtest app"])
def test_match_when_the_query_has_extra_or_split_words(query):
    wins = [Window(1, "Speedtest", "speedtest"), Window(2, "Claude", "claude")]
    assert [w.hwnd for w in match_windows(wins, [query])] == [1]


def test_match_space_insensitive_on_title_for_store_apps():
    wins = [Window(1, "Speedtest", "applicationframehost"), Window(2, "Claude", "claude")]  # Store apps share one host exe
    assert [w.hwnd for w in match_windows(wins, ["Speed Test"])] == [1]


def test_match_nothing():
    assert match_windows(WINS, ["firefox"]) == []


# ---------- tools through the real router, with fakes underneath ----------
@pytest.fixture
def env(monkeypatch, logs):
    reg = Registry()
    register_os_tools(reg)
    router = Router(lambda k, m: logs.append((k, m)))

    def run(tool_name, params):
        tool = reg.get_exposed(tool_name)
        return router.run(reg.alternatives(tool), params)

    return run


class FakeVolume:
    def __init__(self, level=50, muted=False, stuck=False):
        self.level, self.muted, self.stuck = level, muted, stuck

    def install(self, mp):
        mp.setattr(volume, "get_volume", lambda: self.level)
        mp.setattr(volume, "set_volume", lambda v: None if self.stuck else setattr(self, "level", v))
        mp.setattr(volume, "is_muted", lambda: self.muted)
        mp.setattr(volume, "set_muted", lambda m: setattr(self, "muted", m))


def test_set_volume_verified(env, monkeypatch):
    FakeVolume().install(monkeypatch)
    r = env("os_set_volume", SetVolumeParams(level=30))
    assert r.ok and r.verified and "30%" in r.summary


def test_set_volume_that_does_not_stick_is_reported_failed(env, monkeypatch):
    FakeVolume(stuck=True).install(monkeypatch)
    r = env("os_set_volume", SetVolumeParams(level=30))
    assert not r.ok and "reads back as 50%" in r.error


def test_change_volume_clamps(env, monkeypatch):
    fake = FakeVolume(level=95)
    fake.install(monkeypatch)
    r = env("os_change_volume", ChangeVolumeParams(delta=10))
    assert r.ok and fake.level == 100


def test_volume_range_is_validated_before_running():
    with pytest.raises(ValueError):
        SetVolumeParams(level=150)


def test_mute_verified(env, monkeypatch):
    fake = FakeVolume()
    fake.install(monkeypatch)
    assert env("os_set_mute", MuteParams(muted=True)).verified and fake.muted


class FakeMedia:
    """A fake player: pressing play_pause toggles, next changes the track."""

    def __init__(self, status="playing", responsive=True):
        self.s = media.MediaState(status, "Song A", "Artist", "Player", 30.0)
        self.responsive = responsive
        self.pressed: list[str] = []

    def install(self, mp):
        mp.setattr(media, "state", lambda: self.s)
        mp.setattr(media, "press", self.press)

    def press(self, key):
        self.pressed.append(key)
        if not self.responsive:
            return
        if key == "play_pause":
            self.s = dataclasses.replace(self.s, status="paused" if self.s.status == "playing" else "playing")
        elif key == "next":
            self.s = media.MediaState(self.s.status, "Song B", "Artist", "Player", 0.0)


def test_pause_when_playing_presses_and_verifies(env, monkeypatch):
    fake = FakeMedia("playing")
    fake.install(monkeypatch)
    r = env("os_media_key", MediaKeyParams(key="pause"))
    assert r.ok and r.verified and fake.pressed == ["play_pause"]


def test_pause_is_idempotent_when_already_paused(env, monkeypatch):
    fake = FakeMedia("paused")
    fake.install(monkeypatch)
    r = env("os_media_key", MediaKeyParams(key="pause"))
    assert r.ok and fake.pressed == []  # must NOT press, or it would start playing


def test_next_verified_by_track_change(env, monkeypatch):
    fake = FakeMedia()
    fake.install(monkeypatch)
    r = env("os_media_key", MediaKeyParams(key="next"))
    assert r.ok and r.verified


def test_unresponsive_player_is_reported_not_faked(env, monkeypatch):
    monkeypatch.setattr("orbit.tools.os_layer.SETTLE_TIMEOUT_S", 0.3)
    FakeMedia("playing", responsive=False).install(monkeypatch)
    r = env("os_media_key", MediaKeyParams(key="pause"))
    assert not r.ok and "did not change" in r.error


def test_no_media_session_is_a_clear_error(env, monkeypatch):
    monkeypatch.setattr(media, "state", lambda: media.MediaState("none"))
    r = env("os_media_key", MediaKeyParams(key="pause"))
    assert not r.ok and "no media session" in r.error


def test_open_app_unknown_suggests_names(env, monkeypatch):
    monkeypatch.setattr(apps, "installed_apps", lambda: tuple(APPS))
    r = env("os_open_app", OpenAppParams(name="visual studio"))
    assert not r.ok and "Visual Studio Code" in r.error


def test_open_app_launches_and_verifies_foreground(env, monkeypatch):
    monkeypatch.setattr(apps, "installed_apps", lambda: tuple(APPS))
    state = {"launched": False}
    spotify = Window(7, "Spotify Free", "spotify")
    monkeypatch.setattr(apps, "launch", lambda app: state.update(launched=True))
    monkeypatch.setattr(windows, "list_windows", lambda: [spotify] if state["launched"] else [])
    monkeypatch.setattr(windows, "foreground_window", lambda: spotify if state["launched"] else None)
    monkeypatch.setattr(windows, "focus", lambda hwnd: True)
    r = env("os_open_app", OpenAppParams(name="Spotify"))
    assert r.ok and r.verified and state["launched"]


def test_open_app_already_running_is_not_launched_twice(env, monkeypatch):
    monkeypatch.setattr(apps, "installed_apps", lambda: tuple(APPS))
    spotify = Window(7, "Spotify Free", "spotify")
    monkeypatch.setattr(apps, "launch", lambda app: pytest.fail("must not launch a second copy"))
    monkeypatch.setattr(windows, "list_windows", lambda: [spotify])
    monkeypatch.setattr(windows, "foreground_window", lambda: spotify)
    monkeypatch.setattr(windows, "focus", lambda hwnd: True)
    r = env("os_open_app", OpenAppParams(name="spotify"))
    assert r.ok and "already open" in r.summary


def test_open_app_window_never_reaches_foreground_is_a_failure(env, monkeypatch):
    monkeypatch.setattr("orbit.tools.os_layer.SETTLE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(apps, "installed_apps", lambda: tuple(APPS))
    spotify = Window(7, "Spotify Free", "spotify")
    monkeypatch.setattr(windows, "list_windows", lambda: [spotify])
    monkeypatch.setattr(windows, "foreground_window", lambda: Window(1, "Some other app", "other"))
    monkeypatch.setattr(windows, "focus", lambda hwnd: False)  # Windows refuses to bring it forward
    r = env("os_open_app", OpenAppParams(name="spotify"))
    assert not r.ok and "not in the foreground" in r.error


# ---------- close window ----------
from orbit.agent.safety import Safety  # noqa: E402
from orbit.tools.os_layer import CloseParams  # noqa: E402


def test_close_window_closes_and_verifies(env, monkeypatch):
    clock = Window(5, "Clock", "applicationframehost")
    open_wins = [clock]
    monkeypatch.setattr(windows, "list_windows", lambda: list(open_wins))
    monkeypatch.setattr(windows, "console_hwnd", lambda: 0)
    monkeypatch.setattr(windows, "close", lambda hwnd: open_wins.clear())
    r = env("os_close_window", CloseParams(title="clock"))
    assert r.ok and r.verified


def test_close_window_that_stays_open_reports_the_save_dialog_case(env, monkeypatch):
    monkeypatch.setattr("orbit.tools.os_layer.SETTLE_TIMEOUT_S", 0.2)
    note = Window(5, "Untitled - Notepad", "notepad")
    monkeypatch.setattr(windows, "list_windows", lambda: [note])
    monkeypatch.setattr(windows, "console_hwnd", lambda: 0)
    monkeypatch.setattr(windows, "close", lambda hwnd: None)  # app ignores it: it is showing "save changes?"
    r = env("os_close_window", CloseParams(title="notepad"))
    assert not r.ok and "asking to save" in r.error


def test_close_window_is_never_retried(env, monkeypatch, logs):
    monkeypatch.setattr("orbit.tools.os_layer.SETTLE_TIMEOUT_S", 0.2)
    closed = []
    w = Window(5, "Docs", "app")
    monkeypatch.setattr(windows, "list_windows", lambda: [w])
    monkeypatch.setattr(windows, "console_hwnd", lambda: 0)
    monkeypatch.setattr(windows, "close", lambda hwnd: closed.append(hwnd))
    env("os_close_window", CloseParams(title="docs"))
    assert closed == [5]  # a retry could close a second matching window


def test_close_window_never_closes_orbits_own_console(env, monkeypatch):
    own = Window(9, "Windows PowerShell - orbit", "powershell")
    monkeypatch.setattr(windows, "list_windows", lambda: [own])
    monkeypatch.setattr(windows, "console_hwnd", lambda: 9)
    monkeypatch.setattr(windows, "close", lambda hwnd: pytest.fail("closed Orbit's own window"))
    r = env("os_close_window", CloseParams(title="orbit"))
    assert not r.ok and "no open window" in r.error


def test_close_asks_first_when_the_title_suggests_unsaved_work(monkeypatch):
    reg = Registry()
    register_os_tools(reg)
    tool = reg.get_exposed("os_close_window")
    monkeypatch.setattr(windows, "console_hwnd", lambda: 0)
    asked = []
    safety = Safety(confirm=lambda q: asked.append(q) or False)
    monkeypatch.setattr(windows, "list_windows", lambda: [Window(5, "*notes - Notepad", "notepad")])
    assert safety.approve([tool], CloseParams(title="notepad"))[0] is False and asked
    asked.clear()
    monkeypatch.setattr(windows, "list_windows", lambda: [Window(6, "Clock", "x")])
    assert safety.approve([tool], CloseParams(title="clock"))[0] is True and not asked

"""OS layer tools: launch apps, windows, media keys, volume. Each action is verified by reading the state back."""
import time
from typing import Callable, Literal

from pydantic import BaseModel, Field

from ..base import Registry, Tool, ToolResult, VerifyResult, read_only_verify
from . import apps, media, volume, windows

APP_START_TIMEOUT_S = 12.0
SETTLE_TIMEOUT_S = 3.0


def _wait_for(check: Callable[[], object], timeout: float, interval: float = 0.25):
    """Poll `check()` until it returns something truthy or the timeout passes. Returns the last value."""
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


# ---------------------------------------------------------------- open / focus / list windows
class OpenAppParams(BaseModel):
    name: str = Field(description="app name, e.g. Spotify")


class FocusParams(BaseModel):
    title: str = Field(description="part of the window title or app name, e.g. Chrome")


class NoParams(BaseModel):
    pass


def _app_windows(app: apps.App) -> list[windows.Window]:
    return windows.match_windows(windows.list_windows(), app.hints())


def _resolve(name: str) -> tuple[apps.App | None, str]:
    app, suggestions = apps.resolve_app(name, apps.installed_apps())
    if app:
        return app, ""
    hint = f"; closest: {', '.join(repr(s) for s in suggestions)}" if suggestions else ""
    return None, f"app '{name}' not found{hint}"


def open_app(p: OpenAppParams) -> ToolResult:
    app, err = _resolve(p.name)
    if app is None:
        return ToolResult.failure(err, retryable=False)
    existing = _app_windows(app)
    if existing:
        windows.focus(existing[0].hwnd)
        return ToolResult.success(f"{app.name} was already open; brought it to the front")
    apps.launch(app)
    found = _wait_for(lambda: _app_windows(app), APP_START_TIMEOUT_S)
    if not found:
        return ToolResult.failure(
            f"launched {app.name} but no window appeared within {APP_START_TIMEOUT_S:.0f}s "
            "(it may be slow to start or live in the tray)"
        )
    windows.focus(found[0].hwnd)
    return ToolResult.success(f"opened {app.name}")


def verify_open_app(p: OpenAppParams, r: ToolResult) -> VerifyResult:
    app, err = _resolve(p.name)
    if app is None:
        return VerifyResult(False, err)

    def front():
        fg = windows.foreground_window()
        return fg if fg and windows.match_windows([fg], app.hints()) else None

    fg = _wait_for(front, SETTLE_TIMEOUT_S)
    if fg:
        return VerifyResult(True, f"foreground window is '{fg.title}'")
    # Not in front: make one more attempt before reporting that Windows would not let us.
    existing = _app_windows(app)
    if existing and windows.focus(existing[0].hwnd):
        return VerifyResult(True, f"foreground window is '{existing[0].title}'")
    return VerifyResult(False, f"{app.name} is running but its window is not in the foreground")


def focus_window(p: FocusParams) -> ToolResult:
    matches = windows.match_windows(windows.list_windows(), [p.title])
    if not matches:
        open_now = ", ".join(repr(w.title[:40]) for w in windows.list_windows()[:8])
        return ToolResult.failure(f"no window matches '{p.title}'. Open windows include: {open_now}", retryable=False)
    windows.focus(matches[0].hwnd)
    return ToolResult.success(f"focused '{matches[0].title}'")


def verify_focus_window(p: FocusParams, r: ToolResult) -> VerifyResult:
    def front():
        fg = windows.foreground_window()
        return fg if fg and windows.match_windows([fg], [p.title]) else None

    fg = _wait_for(front, SETTLE_TIMEOUT_S)
    if fg:
        return VerifyResult(True, f"foreground window is '{fg.title}'")
    return VerifyResult(False, "the window is not in the foreground")


def list_windows(p: NoParams) -> ToolResult:
    wins = windows.list_windows()
    lines = [w.label() for w in wins[:30]]
    return ToolResult.success(f"{len(wins)} windows open", data="\n".join(lines))


# ---------------------------------------------------------------- media
class MediaKeyParams(BaseModel):
    key: Literal["play", "pause", "play_pause", "next", "prev"] = Field(
        description="play and pause are safe to repeat; play_pause toggles"
    )


def media_key(p: MediaKeyParams) -> ToolResult:
    before = media.state()
    if before.status == "none":
        return ToolResult.failure("no media session is active; start playback first (for example open Spotify)", retryable=False)
    if p.key == "play" and before.status == "playing":
        return ToolResult.success(f"already playing ({before.describe()})", None)
    if p.key == "pause" and before.status != "playing":
        return ToolResult.success(f"already not playing ({before.describe()})", None)
    media.press("play_pause" if p.key in ("play", "pause") else p.key)
    result = ToolResult.success(f"pressed {p.key} (was {before.describe()})")
    result.context["before"] = before
    return result


def verify_media_key(p: MediaKeyParams, r: ToolResult) -> VerifyResult:
    before: media.MediaState | None = r.context.get("before")
    if before is None:  # nothing was pressed; just confirm the state we relied on still holds
        now = media.state()
        return VerifyResult(True, now.describe())

    if p.key in ("play", "pause", "play_pause"):
        want_playing = p.key == "play" or (p.key == "play_pause" and before.status != "playing")
        done = lambda s: (s.status == "playing") == want_playing  # noqa: E731
    elif p.key == "next":
        done = lambda s: (s.title, s.artist) != (before.title, before.artist)  # noqa: E731
    else:  # prev may restart the same track, which shows as the position jumping back
        done = lambda s: (s.title, s.artist) != (before.title, before.artist) or s.position_s < before.position_s - 0.5  # noqa: E731

    now = _wait_for(lambda: (lambda s: s if done(s) else None)(media.state()), SETTLE_TIMEOUT_S, 0.4)
    if now:
        return VerifyResult(True, now.describe())
    return VerifyResult(False, f"media state did not change as expected; still {media.state().describe()}")


def media_status(p: NoParams) -> ToolResult:
    return ToolResult.success(media.state().describe())


# ---------------------------------------------------------------- volume
class SetVolumeParams(BaseModel):
    level: int = Field(ge=0, le=100, description="volume percent, 0 to 100")


class ChangeVolumeParams(BaseModel):
    delta: int = Field(ge=-100, le=100, description="percent to add (up) or subtract (down), e.g. 10 or -10")


class MuteParams(BaseModel):
    muted: bool = Field(description="true to mute, false to unmute")


def set_volume(p: SetVolumeParams) -> ToolResult:
    volume.set_volume(p.level)
    note = " (note: audio is still muted)" if volume.is_muted() else ""
    return ToolResult.success(f"set volume to {p.level}%{note}")


def verify_set_volume(p: SetVolumeParams, r: ToolResult) -> VerifyResult:
    now = volume.get_volume()
    return VerifyResult(abs(now - p.level) <= 1, f"volume reads back as {now}%")


def change_volume(p: ChangeVolumeParams) -> ToolResult:
    before = volume.get_volume()
    target = max(0, min(100, before + p.delta))
    volume.set_volume(target)
    result = ToolResult.success(f"volume {before}% -> {target}%")
    result.context["target"] = target
    return result


def verify_change_volume(p: ChangeVolumeParams, r: ToolResult) -> VerifyResult:
    now = volume.get_volume()
    return VerifyResult(abs(now - r.context["target"]) <= 1, f"volume reads back as {now}%")


def get_volume(p: NoParams) -> ToolResult:
    return ToolResult.success(f"volume is {volume.get_volume()}%, {'muted' if volume.is_muted() else 'not muted'}")


def set_mute(p: MuteParams) -> ToolResult:
    volume.set_muted(p.muted)
    return ToolResult.success("muted" if p.muted else "unmuted")


def verify_set_mute(p: MuteParams, r: ToolResult) -> VerifyResult:
    now = volume.is_muted()
    return VerifyResult(now == p.muted, f"mute reads back as {now}")


# ---------------------------------------------------------------- registration
def register_os_tools(registry: Registry) -> None:
    def add(name, description, params, run, verify):
        registry.register(Tool(name=name, description=description, params=params, layer="os", risk="low", run=run, verify=verify))

    add("os_open_app", "Open an installed app by name (or bring it to the front if already open).", OpenAppParams, open_app, verify_open_app)
    add("os_focus_window", "Bring an already open window to the front by part of its title or app name.", FocusParams, focus_window, verify_focus_window)
    add("os_list_windows", "List the open windows.", NoParams, list_windows, read_only_verify)
    add("os_media_key", "Play, pause, skip to next or go to previous track of the current media.", MediaKeyParams, media_key, verify_media_key)
    add("os_media_status", "Say what media is playing now and whether it is paused.", NoParams, media_status, read_only_verify)
    add("os_set_volume", "Set the system volume to a percent from 0 to 100.", SetVolumeParams, set_volume, verify_set_volume)
    add("os_change_volume", "Turn the system volume up or down by a number of percent.", ChangeVolumeParams, change_volume, verify_change_volume)
    add("os_get_volume", "Read the current system volume and mute state.", NoParams, get_volume, read_only_verify)
    add("os_set_mute", "Mute or unmute the system sound.", MuteParams, set_mute, verify_set_mute)

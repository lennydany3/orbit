"""OS layer tools: launch apps, windows, media keys, volume. Each action is verified by reading the state back."""
import time
from datetime import datetime
from typing import Callable, Literal

from pydantic import BaseModel, Field

from ..base import Registry, Tool, ToolResult, VerifyResult, read_only_verify
from . import apps, media, monitors, processes, uiclick, volume, windows

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
        return ToolResult.failure(err, retryable=False, not_found=True)
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
        return ToolResult.failure(
            f"no window matches '{p.title}'. Open windows include: {open_now}. "
            "If one of these is what the user meant, call again with that title.", retryable=False, not_found=True)
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


class CloseParams(BaseModel):
    title: str = Field(description="part of the window title or app name to close, e.g. Clock")


def _admin_block(win: windows.Window, doing: str) -> ToolResult | None:
    """Windows will not let a normal program close, minimize or move an administrator window. Say so, instead of pretending."""
    if windows.needs_admin(win.hwnd):
        return ToolResult.failure(
            f"'{win.title[:40]}' runs as administrator, so Windows does not let Orbit {doing} it. {processes.ADMIN_HINT}",
            retryable=False)
    return None


def _closable(query: str) -> list[windows.Window]:
    own = windows.console_hwnd()
    return [w for w in windows.match_windows(windows.list_windows(), [query]) if w.hwnd != own]


def close_window(p: CloseParams) -> ToolResult:
    matches = _closable(p.title)
    if not matches:
        open_now = ", ".join(repr(w.title[:40]) for w in windows.list_windows()[:8])
        return ToolResult.failure(
            f"no open window matches '{p.title}'. Open windows include: {open_now}. "
            "If one of these is what the user meant, call again with that title.", retryable=False, not_found=True)
    target = matches[0]
    blocked = _admin_block(target, "close")
    if blocked:
        return blocked
    exe_path = processes.window_exe_path(target.hwnd)          # read before closing: the process may be gone afterwards
    error = windows.close(target.hwnd)
    if error not in (None, 0):
        return ToolResult.failure(f"Windows refused the request to close '{target.title[:40]}' (error {error}). "
                                  + (processes.ADMIN_HINT if error == 5 else ""), retryable=False)
    summary = f"asked '{target.title}' to close"
    if _wait_for(lambda: all(w.hwnd != target.hwnd for w in windows.list_windows()), 1.0, interval=0.1):
        summary += processes.background_note(target, exe_path)   # e.g. ChatGPT: the window is gone but the app lives in the tray
    result = ToolResult.success(summary)
    result.context["hwnd"] = target.hwnd
    return result


def verify_close_window(p: CloseParams, r: ToolResult) -> VerifyResult:
    gone = _wait_for(lambda: all(w.hwnd != r.context["hwnd"] for w in windows.list_windows()), SETTLE_TIMEOUT_S)
    if gone:
        return VerifyResult(True, "the window is gone")
    return VerifyResult(False, "the window is still open (the app may be asking to save or confirm something)")


class QuitParams(BaseModel):
    name: str = Field(description="the app to quit completely, e.g. ChatGPT (also when it only lives in the system tray)")


def quit_app(p: QuitParams) -> ToolResult:
    found = processes.find_app(p.name, windows.list_windows())
    if isinstance(found, str):
        return ToolResult.failure(found, retryable=False, not_found=True)
    refusal = processes.refusal_for(found)
    if refusal:
        return ToolResult.failure(refusal, retryable=False)
    if not windows.orbit_is_admin() and any(windows.is_elevated(h) for h in found.hwnds):
        return ToolResult.failure(f"'{p.name}' runs as administrator, so Windows does not let Orbit quit it. {processes.ADMIN_HINT}",
                                  retryable=False)
    ended, error = processes.quit_all(found)
    if error == "access denied":
        return ToolResult.failure(f"'{p.name}' runs as administrator, so Windows does not let Orbit quit it. {processes.ADMIN_HINT}",
                                  retryable=False)
    if error:
        return ToolResult.failure(f"could not quit {p.name}: {error}", retryable=False)
    result = ToolResult.success(f"quit {p.name} completely ({ended} process(es) ended)")
    result.context["exe_path"] = found.exe_path
    return result


def verify_quit_app(p: QuitParams, r: ToolResult) -> VerifyResult:
    left = processes.wait_until_gone(r.context["exe_path"], SETTLE_TIMEOUT_S)
    return VerifyResult(not left, "no process of that app is running any more" if not left else f"{len(left)} process(es) still running")


def _close_needs_confirm(p: CloseParams) -> bool:
    """Titles that suggest unsaved work (Notepad and many editors mark it with '*')."""
    matches = _closable(p.title)
    return bool(matches) and (matches[0].title.startswith("*") or "unsaved" in matches[0].title.lower())


# ---------------------------------------------------------------- monitors and window layout
class MoveParams(BaseModel):
    title: str = Field(description="part of the window title or app name, e.g. Brave")
    monitor: str = Field(description="which monitor: primary, secondary, other, left, right, or a number")


class ArrangeParams(BaseModel):
    title: str = Field(description="part of the window title or app name, e.g. Spotify")
    action: Literal["maximize", "minimize", "restore", "left", "right", "top", "bottom", "center"] = Field(
        description="maximize, minimize, restore, or snap to the left/right/top/bottom half of its monitor, or center")


def _movable(query: str) -> list[windows.Window]:
    return _closable(query)  # same rules as closing: best match first, never Orbit's own terminal window


def _no_window(query: str) -> ToolResult:
    open_now = ", ".join(repr(w.title[:40]) for w in windows.list_windows()[:8])
    return ToolResult.failure(
        f"no open window matches '{query}'. Open windows include: {open_now}. "
        "If one of these is what the user meant, call again with that title.", retryable=False, not_found=True)


def list_monitors(p: NoParams) -> ToolResult:
    found = monitors.list_monitors()
    return ToolResult.success(f"{len(found)} monitor(s)", data="\n".join(m.describe(i) for i, m in enumerate(found, 1)))


def move_window(p: MoveParams) -> ToolResult:
    matches = _movable(p.title)
    if not matches:
        return _no_window(p.title)
    win = matches[0]
    blocked = _admin_block(win, "move")
    if blocked:
        return blocked
    found = monitors.list_monitors()
    src = monitors.monitor_of(win.hwnd, found)
    try:
        dst = monitors.resolve(p.monitor, found, src)
    except monitors.MonitorNotFound as e:
        return ToolResult.failure(str(e), retryable=False, not_found=True)
    result = ToolResult.success("")
    result.context.update(hwnd=win.hwnd, target=dst.handle)
    names = {m.handle: f"monitor {i} ({'primary' if m.primary else 'secondary'})" for i, m in enumerate(found, 1)}
    if src is not None and src.handle == dst.handle:
        result.summary = f"'{win.title[:40]}' is already on {names[dst.handle]}"
        return result
    was_maximized, was_minimized = windows.is_maximized(win.hwnd), windows.is_minimized(win.hwnd)
    if was_maximized or was_minimized:
        windows.restore(win.hwnd)  # a maximized window must be normal-sized before it can be moved
    left, top, right, bottom = windows.visible_rect(win.hwnd)
    width, height = right - left, bottom - top
    sl, st, sr, sb = src.work if src else dst.work
    dl, dt, dr, db = dst.work
    new_w, new_h = min(width, dr - dl), min(height, db - dt)
    # keep the window at the same relative spot (a window in the middle stays in the middle)
    rel_x = (left - sl) / max(1, sr - sl)
    rel_y = (top - st) / max(1, sb - st)
    new_l = min(max(dl + round(rel_x * (dr - dl)), dl), dr - new_w)
    new_t = min(max(dt + round(rel_y * (db - dt)), dt), db - new_h)
    windows.set_rect(win.hwnd, new_l, new_t, new_w, new_h)
    if was_maximized:
        windows.maximize(win.hwnd)
    result.summary = f"moved '{win.title[:40]}' to {names[dst.handle]}" + (" (still maximized)" if was_maximized else "")
    return result


def verify_move_window(p: MoveParams, r: ToolResult) -> VerifyResult:
    def on_target():
        here = monitors.monitor_of(r.context["hwnd"])
        return here is not None and here.handle == r.context["target"]

    if _wait_for(on_target, SETTLE_TIMEOUT_S):
        return VerifyResult(True, "Windows reports the window on that monitor")
    return VerifyResult(False, "the window is still on a different monitor")


def arrange_window(p: ArrangeParams) -> ToolResult:
    matches = _movable(p.title)
    if not matches:
        return _no_window(p.title)
    win = matches[0]
    blocked = _admin_block(win, p.action if p.action in ("maximize", "minimize", "restore") else "move")
    if blocked:
        return blocked
    result = ToolResult.success(f"{p.action}: '{win.title[:40]}'")
    result.context.update(hwnd=win.hwnd, action=p.action)
    if p.action == "maximize":
        windows.maximize(win.hwnd)
    elif p.action == "minimize":
        windows.minimize(win.hwnd)
    elif p.action == "restore":
        windows.restore(win.hwnd)
    else:
        if windows.is_maximized(win.hwnd) or windows.is_minimized(win.hwnd):
            windows.restore(win.hwnd)
        here = monitors.monitor_of(win.hwnd) or monitors.list_monitors()[0]
        wl, wt, wr, wb = here.work
        w, h = wr - wl, wb - wt
        cur = windows.visible_rect(win.hwnd)
        target = {
            "left": (wl, wt, w // 2, h), "right": (wl + w // 2, wt, w // 2, h),
            "top": (wl, wt, w, h // 2), "bottom": (wl, wt + h // 2, w, h // 2),
            "center": (wl + (w - (cur[2] - cur[0])) // 2, wt + (h - (cur[3] - cur[1])) // 2, cur[2] - cur[0], cur[3] - cur[1]),
        }[p.action]
        windows.set_rect(win.hwnd, *target)
        result.context["target_rect"] = (target[0], target[1], target[0] + target[2], target[1] + target[3])
    return result


def verify_arrange_window(p: ArrangeParams, r: ToolResult) -> VerifyResult:
    hwnd, action = r.context["hwnd"], r.context["action"]

    def done():
        if action == "maximize":
            return windows.is_maximized(hwnd)
        if action == "minimize":
            return windows.is_minimized(hwnd)
        if action == "restore":
            return not windows.is_maximized(hwnd) and not windows.is_minimized(hwnd)
        got, want = windows.visible_rect(hwnd), r.context["target_rect"]
        return all(abs(a - b) <= 24 for a, b in zip(got, want))  # apps may enforce a minimum size; allow a little slack

    if _wait_for(done, SETTLE_TIMEOUT_S):
        return VerifyResult(True, f"the window is now {action}")
    return VerifyResult(False, f"the window did not end up {action} (the app may enforce its own size)")


class ClickParams(BaseModel):
    text: str = Field(description="the name or words of the button/control to click, e.g. GO or Start")
    window: str = Field(default="", description="the app window it is in, e.g. Speedtest (empty = the window in front)")


class ReadWindowParams(BaseModel):
    window: str = Field(default="", description="the app window to read, e.g. Speedtest (empty = the window in front)")


def ui_click(p: ClickParams) -> ToolResult:
    return uiclick.click(p.text.strip(), p.window.strip())


def verify_ui_click(p: ClickParams, r: ToolResult) -> VerifyResult:
    return uiclick.verify_click(r)


def read_window(p: ReadWindowParams) -> ToolResult:
    return uiclick.read_window(p.window.strip())


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


# ---------------------------------------------------------------- clock
class TimeParams(BaseModel):
    part: Literal["time", "date", "both"] = Field(default="time", description="time = the time now, date = today's date and weekday, both = all of it")


def describe_now(now: datetime, part: str = "time") -> str:
    clock = f"{now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}"
    day = f"{now:%A}, {now:%B} {now.day}, {now.year}"
    if part == "date":
        return f"Today is {day}."
    if part == "both":
        return f"It's {clock} on {day}."
    return f"It's {clock}."


def get_time(p: TimeParams) -> ToolResult:
    """The PC's own clock (local time zone). Nothing to verify: it only reads."""
    return ToolResult.success(describe_now(datetime.now(), p.part))


# ---------------------------------------------------------------- registration
def register_os_tools(registry: Registry) -> None:
    def add(name, description, params, run, verify):
        registry.register(Tool(name=name, description=description, params=params, layer="os", risk="low", run=run, verify=verify))

    add("os_open_app", "Open an installed app by name (or bring it to the front if already open).", OpenAppParams, open_app, verify_open_app)
    add("os_focus_window", "Bring an already open window to the front by part of its title or app name.", FocusParams, focus_window, verify_focus_window)
    registry.register(Tool(
        name="os_close_window", description="Close an open window or app by part of its title or name.",
        params=CloseParams, layer="os", risk="low", run=close_window, verify=verify_close_window,
        idempotent=False, needs_confirm=_close_needs_confirm))
    registry.register(Tool(
        name="os_quit_app",
        description="Quit an app completely (all its processes), also when it keeps running in the system tray after its window closed. "
                    "Use only when the user says quit, exit, kill or force close; to just close a window use os_close_window.",
        params=QuitParams, layer="os", risk="confirm", run=quit_app, verify=verify_quit_app, idempotent=False))
    registry.register(Tool(
        name="os_move_window", description="Move a window to another monitor (primary, secondary, left, right, other).",
        params=MoveParams, layer="os", risk="low", run=move_window, verify=verify_move_window, idempotent=False))
    registry.register(Tool(
        name="os_arrange_window", description="Maximize, minimize, restore, or snap a window to the left/right/top/bottom half of its monitor.",
        params=ArrangeParams, layer="os", risk="low", run=arrange_window, verify=verify_arrange_window, idempotent=False))
    add("os_list_monitors", "List the monitors (primary, secondary, sizes).", NoParams, list_monitors, read_only_verify)
    registry.register(Tool(
        name="os_click", description="Click a button or text in a desktop app's window by its name (not web pages), e.g. GO in Speedtest.",
        params=ClickParams, layer="os", risk="low", run=ui_click, verify=verify_ui_click, idempotent=False,
        needs_confirm=lambda p: bool(uiclick._RISKY.search(p.text))))
    add("os_read_window", "Read the buttons and text of a desktop app's window.", ReadWindowParams, read_window, read_only_verify)
    add("os_list_windows", "List the open windows.", NoParams, list_windows, read_only_verify)
    add("os_media_key", "Play, pause, skip to next or go to previous track of the current media.", MediaKeyParams, media_key, verify_media_key)
    add("os_media_status", "Say what media is playing now and whether it is paused.", NoParams, media_status, read_only_verify)
    add("os_set_volume", "Set the system volume to a percent from 0 to 100.", SetVolumeParams, set_volume, verify_set_volume)
    add("os_change_volume", "Turn the system volume up or down by a number of percent.", ChangeVolumeParams, change_volume, verify_change_volume)
    add("os_get_volume", "Read the current system volume and mute state.", NoParams, get_volume, read_only_verify)
    add("os_get_time", "Tell the current time, and/or today's date and weekday, from the PC clock. Use for any question about the time, date or day.",
        TimeParams, get_time, read_only_verify)
    add("os_set_mute", "Mute or unmute the system sound.", MuteParams, set_mute, verify_set_mute)

"""Interact with YOUR OWN Brave window (your profile, your logins): read the page, click, fullscreen, play YouTube.

Done through Windows UI Automation (see os_layer/uia.py), not remote debugging, so it works on the browser you already
have open. Pages are read by control names, not pixels, so it sees links, buttons and fields but not images or video
frames. Everything read from a page is untrusted data.
"""
import re
import time
from typing import Callable

from ..base import ToolResult, VerifyResult
from ..os_layer import media, uia, windows
from . import real

_VIDEO_LINK = re.compile(r"\b\d+ (?:seconds?|minutes?|hours?)\b", re.I)
_RISKY = re.compile(
    r"\b(buy|purchase|pay|checkout|order|send|post|publish|delete|remove|submit|confirm|unsubscribe|subscribe|"
    r"sign out|log ?out|transfer|donate|install|uninstall)\b", re.I)
SETTLE_S = 5.0
RESULTS_WAIT_S = 15.0
PAGE_WAIT_S = 12.0  # "open youtube and play some video": how long to wait for the page to be YouTube


def _wait(check: Callable[[], object], timeout: float, interval: float = 0.4):
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = check()
        except Exception:  # a page that is changing can fail a read for a moment; that is "not yet", not a crash
            value = None
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def brave_window() -> windows.Window | None:
    """The Brave window to work in: the one in front, else the top-most one."""
    brave = [w for w in windows.list_windows() if w.exe == "brave"]
    fg = windows.foreground_window()
    return fg if fg and fg.exe == "brave" else (brave[0] if brave else None)


def _window_or_error() -> tuple[windows.Window | None, ToolResult | None]:
    win = brave_window()
    if win is None:
        return None, ToolResult.failure("Brave is not open. Ask me to open a site first", retryable=False, not_found=True)
    return win, None


def _snapshot(hwnd: int) -> tuple[str, str]:
    title = next((w.title for w in windows.list_windows() if w.hwnd == hwnd), "")
    return title, uia.address_bar(hwnd)


def _some_names(items: list[uia.Control], n: int = 8) -> str:
    """A few things on the page the user could mean (links first, then buttons)."""
    visible = [c for c in items if not c.offscreen]
    chosen = ([c for c in visible if c.kind == "link"] + [c for c in visible if c.kind == "button"])[:n]
    return "; ".join(c.name[:50] for c in chosen) or "nothing readable (is the page still loading?)"


def wait_until_loaded(hwnd: int, timeout: float | None = None) -> str:
    """The window title once the page has one ("Untitled" / "New Tab" mean it is still loading)."""
    def titled():
        title = next((w.title for w in windows.list_windows() if w.hwnd == hwnd), "")
        return title if title and not re.match(r"^(untitled|new tab|about:blank)", title, re.I) else None
    return _wait(titled, PAGE_WAIT_S if timeout is None else timeout, interval=0.5) or next((w.title for w in windows.list_windows() if w.hwnd == hwnd), "")


# ---------------------------------------------------------------- click
def click(text: str) -> ToolResult:
    win, err = _window_or_error()
    if err:
        return err
    items = uia.controls(win.hwnd)
    hits = uia.find(items, text)
    if not hits:
        return ToolResult.failure(
            f"nothing in your Brave window is called '{text}'. Some things I can see: {_some_names(items)}",
            retryable=False, not_found=True)
    target = hits[0]
    if _RISKY.search(target.name) and not _RISKY.search(text):
        return ToolResult.failure(
            f"the closest match is '{target.name[:60]}', which looks risky and you did not name it. "
            "Say its exact name if you really mean it.", retryable=False)
    before = _snapshot(win.hwnd)
    how = uia.click(target)
    result = ToolResult.success(f"clicked '{target.name[:70]}' ({target.kind}) in your Brave")
    result.context.update(mine=True, hwnd=win.hwnd, before=before, how=how, clicked=target.name)
    return result


def verify_click(r: ToolResult) -> VerifyResult:
    """Success = the page (title/address) changed, or the control we pressed changed or went away (a toggle flips its label)."""
    hwnd, before, clicked = r.context["hwnd"], r.context["before"], r.context["clicked"]

    def changed():
        title, url = _snapshot(hwnd)
        if (title, url) != before:
            return f"the page changed: now '{title[:60]}' {url}"
        if clicked not in {c.name for c in uia.controls(hwnd)}:
            return f"'{clicked[:50]}' changed or went away"
        return None

    why = _wait(changed, SETTLE_S)
    return VerifyResult(True, why) if why else VerifyResult(False, "the page looks the same after the click (it may have had no visible effect)")


# ---------------------------------------------------------------- read
def page_info() -> ToolResult:
    win, err = _window_or_error()
    if err:
        return err
    items = uia.controls(win.hwnd)
    title, url = _snapshot(win.hwnd)
    return ToolResult.success(f"on {url or '?'} ('{title}') in your Brave", data="; ".join(c.name[:60] for c in items if not c.offscreen)[:900])


# ---------------------------------------------------------------- fullscreen
def _fullscreen_state(items: list[uia.Control]) -> bool | None:
    """True/False from YouTube's own button label ("Exit full screen (f)" / "Full screen (f)"), None if no player."""
    names = [c.name.lower() for c in items if c.kind == "button"]
    if any(n.startswith("exit full screen") for n in names):
        return True
    return False if any(n.startswith("full screen") for n in names) else None


def fullscreen(on: bool) -> ToolResult:
    win, err = _window_or_error()
    if err:
        return err
    items = uia.controls(win.hwnd)
    state = _fullscreen_state(items)
    if state is None:
        return ToolResult.failure("I can't find a video player's full-screen button in your Brave window", retryable=False, not_found=True)
    if state == on:
        nothing_to_do = ToolResult.success("already " + ("fullscreen" if on else "not fullscreen"))
        nothing_to_do.context["mine"] = True  # so it is checked the same way (nothing was pressed)
        return nothing_to_do
    button = uia.find([c for c in items if c.kind == "button"], "exit full screen" if state else "full screen")[0]
    uia.click(button)
    result = ToolResult.success("entered fullscreen" if on else "left fullscreen")
    result.context.update(mine=True, hwnd=win.hwnd, want=on)
    return result


def verify_fullscreen(r: ToolResult) -> VerifyResult:
    if "want" not in r.context:
        return VerifyResult(True, r.summary)
    ok = _wait(lambda: _fullscreen_state(uia.controls(r.context["hwnd"])) == r.context["want"], SETTLE_S)
    return VerifyResult(bool(ok), "the player's full-screen button now reads " + ("'Exit full screen'" if r.context["want"] else "'Full screen'"))


# ---------------------------------------------------------------- youtube
def _first_video(items: list[uia.Control]) -> uia.Control | None:
    links = [c for c in items if c.kind == "link" and not c.offscreen and "shorts" not in c.name.lower()]
    return next((c for c in links if _VIDEO_LINK.search(c.name)), None) or next((c for c in links if "watching" in c.name.lower()), None)


def play_first_video(query: str, open_results: Callable[[str], ToolResult], results_url: str) -> ToolResult:
    """With a query: open the search results in your Brave first. Then click the first video."""
    if query:
        opened = open_results(results_url)
        if not opened.ok:
            return opened
        real.brave_in_front()
    win, err = _window_or_error()
    if err:
        return err
    if not query:  # "open youtube and play some video": the page may still be loading
        _wait(lambda: "youtube" in _snapshot(brave_window().hwnd)[0].lower(), PAGE_WAIT_S, interval=0.5)
    if not query and "youtube" not in _snapshot(win.hwnd)[0].lower():
        return ToolResult.failure("your Brave is not on YouTube; say what to play, e.g. 'play lofi on youtube'", retryable=False, not_found=True)

    def video():
        win_now = brave_window()
        return _first_video(uia.controls(win_now.hwnd)) if win_now else None

    target = _wait(video, RESULTS_WAIT_S, interval=1.0)
    if target is None:
        return ToolResult.failure("I did not find a video to click on the page (is it still loading?)")
    win = brave_window()
    before = _snapshot(win.hwnd)
    uia.click(target)
    result = ToolResult.success(f"clicked the first video: '{target.name[:80]}'")
    result.context.update(mine=True, hwnd=win.hwnd, before=before, clicked=target.name)
    return result


def _same_video(playing_title: str, clicked_name: str) -> bool:
    """Is the video Windows says is playing the one we clicked? (A link's name = the title plus length, views, channel.)"""
    title_words = set(re.findall(r"\w+", playing_title.lower()))
    link_words = set(re.findall(r"\w+", clicked_name.lower()))
    return bool(title_words) and len(title_words & link_words) / len(title_words) >= 0.6


def verify_play(r: ToolResult) -> VerifyResult:
    """The address must be a NEW video page and Windows must report THAT video playing. (The previous video may still
    be playing for a moment, so "something is playing" is not enough.)"""
    hwnd, clicked, before_url = r.context["hwnd"], r.context.get("clicked", ""), r.context["before"][1]
    on_new_video = _wait(lambda: (lambda u: "/watch" in u and u != before_url)(uia.address_bar(hwnd)), 8.0)
    if not on_new_video:
        return VerifyResult(False, f"the browser did not go to a new video page (address bar: '{uia.address_bar(hwnd)}')")

    def playing_that_video():
        state = media.state()
        ok = state.status == "playing" and "brave" in state.app.lower() and (not clicked or _same_video(state.title, clicked))
        return state if ok else None

    state = _wait(playing_that_video, 10.0)
    if state:
        return VerifyResult(True, f"Windows reports Brave playing '{state.title[:60]}'")
    now = media.state()
    return VerifyResult(False, f"on a video page, but Windows reports '{now.title[:50]}' ({now.status}), not the video I clicked")

"""Interact with YOUR OWN Brave window (your profile, your logins): read the page, click, fullscreen, play YouTube.

Done through Windows UI Automation (see os_layer/uia.py), not remote debugging, so it works on the browser you already
have open. Pages are read by control names, not pixels, so it sees links, buttons and fields but not images or video
frames. Everything read from a page is untrusted data.
"""
import re
import time
from typing import Callable
from urllib.parse import urlparse

from ..base import ToolResult, VerifyResult
from ..os_layer import glide, media, screen, scrolling, symbols, uia, windows
from . import real

_VIDEO_LINK = re.compile(r"\b\d+ (?:seconds?|minutes?|hours?)\b", re.I)
_RISKY = re.compile(
    r"\b(buy|purchase|pay|checkout|order|send|post|publish|delete|remove|submit|confirm|unsubscribe|subscribe|"
    r"sign out|log ?out|transfer|donate|install|uninstall)\b", re.I)
SETTLE_S = 5.0
RESULTS_WAIT_S = 15.0
LINK_GRACE_S = 2.5  # how long a clicked link gets to change the address before we try a real mouse click
NTH_WAIT_S = 6.0    # how long "click the first result" keeps looking while a results page is still loading
FOCUS_WAIT_S = 2.5  # how long a clicked text box gets to take the keyboard
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
def _page_controls(hwnd: int) -> list[uia.Control]:
    """The page's controls, waiting for a page that is still loading (right after "open youtube")."""
    return _wait(lambda: uia.controls(hwnd) or None, PAGE_WAIT_S, interval=1.0) or []


def _bring_to_front(win: windows.Window) -> None:
    """Orbit's pointer is drawn over the screen, so the page it points at must really be the one in front."""
    fg = windows.foreground_window()
    if fg is None or fg.hwnd != win.hwnd:
        windows.focus(win.hwnd)


_NOT_A_RESULT = re.compile(r"^(skip to|accessibility|sign in|log in|images?|videos?|news|maps|shopping|books|flights|finance|more|tools|"
                           r"settings|privacy|terms|feedback|help|advertising|about|search tools|all filters)\b", re.I)


def _site(host: str) -> str:
    labels = host.lower().removeprefix("www.").split(".")
    return labels[-2] if len(labels) >= 2 else labels[0]


def result_links(items: list[uia.Control], strict: bool = False) -> list[uia.Control]:
    """The links that are the page's actual results (a search page's hits), in page order: real web addresses with a title-like
    name, each address once, and the search engine's own menu links left out. Off a search page, if every link is the site's own
    (a GitHub page, say), those are used; on a search page (`strict`) they never are: no hits yet means the page is still loading."""
    chosen: list[tuple[uia.Control, str]] = []
    seen: set[str] = set()
    for c in items:
        if c.kind != "link" or len(c.name) < 10 or _NOT_A_RESULT.match(c.name):
            continue
        href = uia.link_target(c)
        if not href.startswith(("http://", "https://")):
            continue
        key = href.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        chosen.append((c, _site(urlparse(href).hostname or "")))
    outside = [c for c, site in chosen if site != "google"]
    return outside if strict else (outside or [c for c, _ in chosen])


def _nth_link(items: list[uia.Control], n: int, hwnd: int) -> tuple[uia.Control | None, str]:
    """The `n`th result on the page (1 = first, -1 = last) as (control, '') or (None, why not)."""
    address = uia.address_bar(hwnd).lower()
    on_youtube = "youtube.com" in address
    pool = ([c for c in items if c.kind == "link" and not c.offscreen and "shorts" not in c.name.lower() and _VIDEO_LINK.search(c.name)]
            if on_youtube else result_links(items, strict="google." in address and "/search" in address))
    if not pool:
        return None, f"I don't see any results on this page to pick from. Some things I can see: {_some_names(items)}"
    index = n - 1 if n > 0 else len(pool) + n
    if not 0 <= index < len(pool):
        return None, f"there are only {len(pool)} results I can see on this page, so there is no number {abs(n)}"
    return pool[index], ""


def _fields_on_page(items: list[uia.Control], n: int = 8) -> str:
    names = [c.name[:40] for c in items if c.kind in uia.FIELD_KINDS and not c.offscreen]
    return "; ".join(names[:n]) or "no text boxes (is the page still loading?)"


def _ocr_spot(win: windows.Window, text: str) -> tuple[int, int, str, str, screen.Shot] | None:
    """Read the page's pixels with OCR and find the words, or the symbol (a character OCR reads: x, +, a heart...), that were asked
    for: (screen x, screen y, what was found, "words" | "symbol", the picture). None when nothing matches."""
    left, top, right, bottom = uia.page_rect(win.hwnd) or windows.visible_rect(win.hwnd)
    try:
        picture = screen.capture(left, top, right - left, bottom - top)
        words = screen.read_words(picture)
    except (screen.ScreenError, OSError):
        return None
    spot, via = screen.find_text(words, text), "words"
    if spot is None:
        symbol = symbols.identify(text)
        spot, via = (screen.find_glyph(words, symbol.glyphs) if symbol else None), "symbol"
    if spot is None:
        return None
    x, y, found = spot
    return left + x, top + y, found, via, picture


def _click_what_is_drawn(win: windows.Window, text: str, seen: tuple[int, int, str, str, screen.Shot]) -> ToolResult:
    """Click where OCR found the words or the symbol (a real mouse click: the page does not name it, so it cannot be pressed
    the accessible way). Checked afterwards by whether the page changed."""
    x, y, found, via, picture = seen
    if _RISKY.search(found) and not _RISKY.search(text):
        return ToolResult.failure(f"the words I found on the page ('{found[:40]}') look risky and you did not name them", retryable=False)
    _bring_to_front(win)
    before = _snapshot(win.hwnd)
    screen.click_at(x, y)
    what = "the symbol" if via == "symbol" else "the words"
    result = ToolResult.success(f"clicked {what} '{found[:50]}' that I read on your Brave page")
    result.context.update(mine=True, hwnd=win.hwnd, before=before, how="ocr", clicked=found, kind="ocr", picture=picture)
    return result


def click(text: str, kind: str = "any", nth: int = 0) -> ToolResult:
    """Click a control by its name or picture ("the search icon"), a text box by its name (it gets the cursor), or the Nth result."""
    win, err = _window_or_error()
    if err:
        return err
    items = _page_controls(win.hwnd)
    if nth:
        target, problem = _nth_link(items, nth, win.hwnd)
        if target is None:                                  # a results page that is still loading: look again for a few seconds
            def results_arrived():
                found, _ = _nth_link(uia.controls(win.hwnd), nth, win.hwnd)
                return found
            target = _wait(results_arrived, NTH_WAIT_S, interval=0.6)
        if target is None:
            return ToolResult.failure(problem, retryable=False, not_found=True)
    else:
        hits = uia.find(items, text, kind)
        if not hits and kind != "field":
            seen = _ocr_spot(win, text)            # the page names nothing like it: read what is drawn on screen (words in images, symbols)
            if seen is not None:
                return _click_what_is_drawn(win, text, seen)
        if not hits:
            if kind == "field":
                return ToolResult.failure(f"no text box on your Brave page is called '{text}'. Text boxes I can see: {_fields_on_page(items)}",
                                          retryable=False, not_found=True)
            return ToolResult.failure(
                f"nothing in your Brave window is called '{text}'. Some things I can see: {_some_names(items)}",
                retryable=False, not_found=True)
        target = hits[0]
    if _RISKY.search(target.name) and not _RISKY.search(text):
        return ToolResult.failure(
            f"the closest match is '{target.name[:60]}', which looks risky and you did not name it. "
            "Say its exact name if you really mean it.", retryable=False)
    _bring_to_front(win)
    before = _snapshot(win.hwnd)
    how = uia.click(target)
    if target.kind == "link" and not _wait(lambda: uia.address_bar(win.hwnd) != before[1], LINK_GRACE_S):
        # A link that did not go anywhere: some page elements ignore the accessible "press". A link knows its address,
        # so load that address in this tab (no mouse involved).
        href = uia.link_target(target)
        if href.startswith(("http://", "https://")):
            windows.focus(win.hwnd)
            if uia.set_address(win.hwnd, href):
                how = "address"
    if target.kind in uia.FIELD_KINDS:
        summary = f"put the cursor in the '{target.name[:50]}' {target.kind} in your Brave"
    else:
        summary = f"clicked '{target.name[:70]}' ({target.kind}) in your Brave"
    result = ToolResult.success(summary)
    result.context.update(mine=True, hwnd=win.hwnd, before=before, how=how, clicked=target.name, kind=target.kind, element=target.element)
    return result


def verify_click(r: ToolResult) -> VerifyResult:
    """Success = the page (title/address) changed, or the control we pressed changed or went away (a toggle flips its label).
    For a text box: it has the cursor now."""
    hwnd, before, clicked = r.context["hwnd"], r.context["before"], r.context["clicked"]
    is_link = r.context.get("kind") == "link"
    if r.context.get("kind") in uia.FIELD_KINDS:
        def has_cursor():
            if uia._has_focus(r.context["element"]):
                return True
            field = uia.focused_field()
            return field is not None and field.kind in uia.FIELD_KINDS and field.name == clicked
        ok = _wait(has_cursor, FOCUS_WAIT_S, interval=0.2)
        return VerifyResult(bool(ok), "the text box has the cursor" if ok else "the text box did not get the cursor")

    if r.context.get("kind") == "ocr":
        def moved_on():
            title, url = _snapshot(hwnd)
            if (title, url) != before:
                return f"the page changed: now '{title[:60]}' {url}"
            left, top, right, bottom = uia.page_rect(hwnd) or windows.visible_rect(hwnd)
            if screen.changed_pixels(r.context["picture"], screen.capture(left, top, right - left, bottom - top)) >= 40:
                return "the page changed on screen"
            return None
        why = _wait(moved_on, SETTLE_S)
        return VerifyResult(True, why) if why else VerifyResult(False, "the page looks the same after the click (it may have had no visible effect)")

    def changed():
        title, url = _snapshot(hwnd)
        if is_link:  # a link goes somewhere: a flickering title on the same page does not count
            return f"the page changed: now '{title[:60]}' {url}" if url != before[1] else None
        if (title, url) != before:
            return f"the page changed: now '{title[:60]}' {url}"
        if clicked not in {c.name for c in uia.controls(hwnd)}:
            return f"'{clicked[:50]}' changed or went away"
        return None

    why = _wait(changed, SETTLE_S)
    return VerifyResult(True, why) if why else VerifyResult(False, "the page looks the same after the click (it may have had no visible effect)")


# ---------------------------------------------------------------- scroll
def scroll(direction: str, amount: str, times: int) -> ToolResult:
    """Scroll the page in your Brave with the mouse wheel, Orbit's pointer gliding over it first."""
    win, err = _window_or_error()
    if err:
        return err
    _bring_to_front(win)
    region = uia.page_rect(win.hwnd) or windows.visible_rect(win.hwnd)
    outcome = scrolling.scroll_region(region, direction, amount, times, glide.dpi_scale())
    result = scrolling.result_for(outcome, direction, amount, times, win.hwnd)
    result.summary += " in your Brave"
    result.context["mine"] = True
    return result


def verify_scroll(r: ToolResult) -> VerifyResult:
    return scrolling.verify(r)


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


# ---------------------------------------------------------------- go back / forward / reload / home / go to an address
def _host(url: str) -> str:
    from urllib.parse import urlparse
    url = url if "://" in url else "https://" + url
    return (urlparse(url).hostname or "").removeprefix("www.")


def same_address(shown: str, wanted: str) -> bool:
    """Is the address bar showing the address we asked for? ("youtube.com/shorts/x" is NOT the youtube.com home page.)"""
    from urllib.parse import urlparse

    def parts(u: str) -> tuple[str, str]:
        parsed = urlparse(u if "://" in u else "https://" + u)
        return (parsed.hostname or "").removeprefix("www."), parsed.path.rstrip("/")

    (host_a, path_a), (host_b, path_b) = parts(shown), parts(wanted)
    return host_a == host_b and (path_a == path_b or (path_b != "" and path_a.startswith(path_b)))


def go_to(url: str) -> ToolResult:
    """Load an address in the CURRENT tab (so "go to the youtube home page" does not pile up new tabs)."""
    win, err = _window_or_error()
    if err:
        return err
    before = _snapshot(win.hwnd)
    windows.focus(win.hwnd)
    if not uia.set_address(win.hwnd, url):
        return ToolResult.failure("I can't find the address bar in your Brave window", retryable=False)
    result = ToolResult.success(f"went to {url} in the current tab of your Brave")
    result.context.update(mine=True, hwnd=win.hwnd, before=before, url=url, nav="to")
    return result


def navigate(action: str) -> ToolResult:
    win, err = _window_or_error()
    if err:
        return err
    if action == "home":
        current = uia.address_bar(win.hwnd)
        host = _host(current)
        if not host:
            return ToolResult.failure("I can't tell which site you are on, so I don't know its home page", retryable=False)
        return go_to("https://" + host)
    label = {"back": "Back", "forward": "Forward", "reload": "Reload"}[action]
    button = uia.toolbar_button(win.hwnd, label)
    if button is not None and not button.enabled:
        # The toolbar updates a moment after a page change ("click a video, then go back"): look again before refusing.
        button = _wait(lambda: (lambda b: b if b and b.enabled else None)(uia.toolbar_button(win.hwnd, label)), 2.5) or button
    if button is None:
        return ToolResult.failure(f"I can't find the {action} button in your Brave window", retryable=False)
    if not button.enabled:
        return ToolResult.failure(f"there is no {'earlier' if action == 'back' else 'later'} page to go {action} to", retryable=False)
    before = _snapshot(win.hwnd)
    uia.click(button)
    result = ToolResult.success(f"pressed {action} in your Brave")
    result.context.update(mine=True, hwnd=win.hwnd, before=before, nav=action)
    return result


def verify_navigation(r: ToolResult) -> VerifyResult:
    hwnd, before, nav = r.context["hwnd"], r.context["before"], r.context["nav"]
    if nav == "reload":
        title = wait_until_loaded(hwnd, 6.0)
        return VerifyResult(bool(title), f"the page has loaded again ('{title[:50]}'); a reload cannot be told apart from the old page")
    if nav == "to":
        wanted = r.context["url"]
        arrived = _wait(lambda: same_address(uia.address_bar(hwnd), wanted), SETTLE_S + 3)
        if arrived:
            return VerifyResult(True, f"the address bar shows {uia.address_bar(hwnd)}")
        return VerifyResult(False, f"the address bar shows '{uia.address_bar(hwnd)}', not {wanted}")
    now = _wait(lambda: (lambda s: s if s != before else None)(_snapshot(hwnd)), SETTLE_S)
    return VerifyResult(True, f"now '{now[0][:60]}' {now[1]}") if now else VerifyResult(False, "the page did not change")


# ---------------------------------------------------------------- tabs
def _tab_matches(tab: uia.Control, match: str) -> bool:
    return not match or uia.match_score(match, tab.name) > 0 or match.lower() in tab.name.lower()


def tab_list() -> ToolResult:
    win, err = _window_or_error()
    if err:
        return err
    found = uia.tabs(win.hwnd)
    lines = [f"{i}. {t.name[:70]}{'  [current]' if t.selected else ''}" for i, t in enumerate(found, 1)]
    return ToolResult.success(f"{len(found)} tab(s) open in your Brave", data="\n".join(lines))


def _targets(tabs: list[uia.Control], scope: str, match: str) -> list[uia.Control]:
    current = next((t for t in tabs if t.selected), None)
    if scope == "this":
        return [current] if current else []
    return [t for t in tabs if (scope == "matching" or (current is None or t.rid != current.rid)) and _tab_matches(t, match)]


def count_targets(scope: str, match: str) -> int:
    """How many tabs a close request would close (0 if it cannot be told). Used to decide whether to ask first."""
    try:
        win = brave_window()
        return len(_targets(uia.tabs(win.hwnd), scope, match)) if win else 0
    except Exception:
        return 0


def close_tabs(scope: str, match: str) -> ToolResult:
    win, err = _window_or_error()
    if err:
        return err
    tabs = uia.tabs(win.hwnd)
    targets = _targets(tabs, scope, match)
    if not targets:
        what = f"tabs matching '{match}'" if match else "tabs to close"
        return ToolResult.failure(f"there are no {what}. Open tabs: {'; '.join(t.name[:40] for t in tabs[:10])}", retryable=False, not_found=True)
    if len(targets) >= len(tabs):
        return ToolResult.failure("that would close every tab, which closes the whole window. Say 'close brave' if you want that", retryable=False)
    keep = next((t for t in tabs if t.selected), None)
    keep_rid = keep.rid if keep else None
    wanted = {t.rid for t in targets}
    closed = 0
    for _ in range(len(targets) + 2):
        now = uia.tabs(win.hwnd)
        victim = next((t for t in now if t.rid in wanted), None)
        if victim is None:
            break
        uia.select_tab(victim)
        windows.focus(win.hwnd)
        front = windows.foreground_window()
        if not front or front.hwnd != win.hwnd:  # never send Ctrl+W to some other program
            return ToolResult.failure(f"closed {closed} tab(s), then Brave lost the keyboard focus, so I stopped", retryable=False)
        windows.press_ctrl("w")
        if not _wait(lambda: len(uia.tabs(win.hwnd)) < len(now), 3.0, interval=0.2):
            break
        closed += 1
        wanted.discard(victim.rid)
    if keep_rid is not None and scope != "this":  # go back to the tab the user was on
        again = next((t for t in uia.tabs(win.hwnd) if t.rid == keep_rid), None)
        if again is not None:
            uia.select_tab(again)
    result = ToolResult.success(f"closed {closed} tab(s) in your Brave")
    result.context.update(mine=True, hwnd=win.hwnd, expected=len(tabs) - len(targets), wanted=len(targets), closed=closed)
    return result


def verify_close_tabs(r: ToolResult) -> VerifyResult:
    hwnd, expected = r.context["hwnd"], r.context["expected"]
    ok = _wait(lambda: len(uia.tabs(hwnd)) == expected, 3.0)
    now = len(uia.tabs(hwnd))
    return VerifyResult(bool(ok), f"{now} tab(s) are open now" + ("" if ok else f" (expected {expected})"))


def switch_tab(text: str) -> ToolResult:
    win, err = _window_or_error()
    if err:
        return err
    tabs = uia.tabs(win.hwnd)
    hits = sorted((t for t in tabs if _tab_matches(t, text)), key=lambda t: -uia.match_score(text, t.name))
    if not hits:
        return ToolResult.failure(f"no tab matches '{text}'. Open tabs: {'; '.join(t.name[:40] for t in tabs[:10])}", retryable=False, not_found=True)
    uia.select_tab(hits[0])
    result = ToolResult.success(f"switched to the tab '{hits[0].name[:60]}'")
    result.context.update(mine=True, hwnd=win.hwnd, rid=hits[0].rid)
    return result


def verify_switch_tab(r: ToolResult) -> VerifyResult:
    hwnd, rid = r.context["hwnd"], r.context["rid"]
    ok = _wait(lambda: any(t.selected and t.rid == rid for t in uia.tabs(hwnd)), 3.0)
    return VerifyResult(bool(ok), "that tab is now the current one" if ok else "the tab did not become current")

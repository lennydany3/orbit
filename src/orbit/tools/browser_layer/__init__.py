"""Browser layer tools: open pages, search, click, read the page, play YouTube, fullscreen.

Everything read from a page is untrusted data. Each action is verified from page state
(URL, title, HTTP status, the <video> element, document.fullscreenElement).
"""
import re
import time
from typing import Callable, Literal
from urllib.parse import quote_plus, urlparse

from playwright.sync_api import Error as PlaywrightError
from pydantic import BaseModel, Field

from ... import config
from ..base import Registry, Tool, ToolResult, VerifyResult, read_only_verify
from . import mine, real
from .session import BrowserUnavailable, find_brave, session

SETTLE_TIMEOUT_S = 4.0
_ALLOWED_SCHEMES = {"http", "https"}
_COMMON_SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}
# Clicking something with one of these words may spend money, send something or destroy data: ask first.
_RISKY_CLICK = re.compile(
    r"\b(buy|purchase|pay|checkout|order|place order|send|post|publish|delete|remove|submit|confirm|"
    r"unsubscribe|subscribe|sign out|log ?out|transfer|donate|install|uninstall)\b", re.I)


def _wait_for(check: Callable[[], object], timeout: float = SETTLE_TIMEOUT_S, interval: float = 0.25):
    deadline = time.monotonic() + timeout
    while True:
        value = check()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def _first_line(e: Exception) -> str:
    text = str(e).strip()
    return text.splitlines()[0] if text else type(e).__name__


def _page_or_error():
    try:
        return session.page(), None
    except BrowserUnavailable as e:
        return None, ToolResult.failure(str(e), retryable=False)


# ---------------------------------------------------------------- url helpers (pure, unit-tested)
def normalize_url(raw: str) -> str:
    """'github.com' -> 'https://github.com'. Only http(s) is allowed (no file:, javascript:, chrome:)."""
    raw = raw.strip()
    if not raw:
        raise ValueError("empty url")
    if "://" not in raw and not re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError(f"only http and https links are allowed, got '{raw[:60]}'")
    return raw


def _brand(host: str) -> str:
    """'www.google.co.in' -> 'google': lets google.com -> google.co.in count as the same site."""
    labels = host.lower().removeprefix("www.").split(".")
    if len(labels) >= 3 and labels[-2] in _COMMON_SECOND_LEVEL:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else labels[0]


def same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).hostname or "", urlparse(b).hostname or ""
    return bool(ha and hb) and (ha == hb or _brand(ha) == _brand(hb))


# ---------------------------------------------------------------- open
class OpenParams(BaseModel):
    url: str = Field(description="web address, e.g. https://github.com")


class SearchParams(BaseModel):
    query: str = Field(description="what to search the web for")


class NoParams(BaseModel):
    pass


def _use_my_browser() -> bool:
    return config.BROWSER_MODE == "mine" and session.browser == "brave"


def _open_in_my_browser(url: str) -> ToolResult:
    exe = find_brave()
    if exe is None:
        return ToolResult.failure("Brave was not found. Install it, or use --browser chrome", retryable=False)
    try:
        folder = real.profile_folder(config.BRAVE_PROFILE)
    except real.ProfileNotFound as e:
        return ToolResult.failure(str(e), retryable=False)
    real.open_link(url, exe, folder)
    result = ToolResult.success(f"opened {url} in your Brave ({config.BRAVE_PROFILE} profile)")
    result.context.update(requested=url, mine=True)
    return result


class NavigateParams(BaseModel):
    action: Literal["back", "forward", "reload", "home"] = Field(description="back / forward in history, reload, or the home page of the site you are on")


class GoToParams(BaseModel):
    url: str = Field(description="web address to load in the current tab, e.g. https://www.youtube.com")


class CloseTabsParams(BaseModel):
    scope: Literal["this", "others", "matching"] = Field(
        description="this = the current tab; others = every tab except the current one; matching = every tab whose title matches")
    match: str = Field(default="", description="only tabs whose title fits this, e.g. youtube (empty = any)")


class SwitchTabParams(BaseModel):
    text: str = Field(description="part of the tab's title, e.g. github")


_ONLY_YOUR_BRAVE = "Tabs can only be managed in your own Brave window, not in Orbit's separate browser."


def list_tabs(p: NoParams) -> ToolResult:
    return mine.tab_list() if _use_my_browser() else ToolResult.failure(_ONLY_YOUR_BRAVE, retryable=False)


def close_tabs(p: CloseTabsParams) -> ToolResult:
    return mine.close_tabs(p.scope, p.match.strip()) if _use_my_browser() else ToolResult.failure(_ONLY_YOUR_BRAVE, retryable=False)


def verify_close_tabs(p: CloseTabsParams, r: ToolResult) -> VerifyResult:
    return mine.verify_close_tabs(r)


def switch_tab(p: SwitchTabParams) -> ToolResult:
    return mine.switch_tab(p.text) if _use_my_browser() else ToolResult.failure(_ONLY_YOUR_BRAVE, retryable=False)


def verify_switch_tab(p: SwitchTabParams, r: ToolResult) -> VerifyResult:
    return mine.verify_switch_tab(r)


def navigate(p: NavigateParams) -> ToolResult:
    if _use_my_browser():
        return mine.navigate(p.action)
    page, err = _page_or_error()
    if err:
        return err
    try:
        if p.action == "home":
            parts = urlparse(page.url)
            page.goto(f"{parts.scheme}://{parts.netloc}/", wait_until="domcontentloaded")
        else:
            {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[p.action](wait_until="domcontentloaded")
    except PlaywrightError as e:
        return ToolResult.failure(f"could not go {p.action}: {_first_line(e)}")
    result = ToolResult.success(f"{p.action}: now on {page.url}")
    result.context.update(requested=page.url, status=None)
    return result


def verify_navigate(p: NavigateParams, r: ToolResult) -> VerifyResult:
    if r.context.get("mine"):
        return mine.verify_navigation(r)
    page, err = _page_or_error()
    if err:
        return VerifyResult(False, err.error)
    return VerifyResult(not page.url.startswith(("chrome-error:", "about:blank")), f"on {page.url}")


def go_to(p: GoToParams) -> ToolResult:
    try:
        url = normalize_url(p.url)
    except ValueError as e:
        return ToolResult.failure(str(e), retryable=False)
    if _use_my_browser():
        return mine.go_to(url)
    return open_url(OpenParams(url=url))


def verify_go_to(p: GoToParams, r: ToolResult) -> VerifyResult:
    if r.context.get("mine"):
        return mine.verify_navigation(r)
    return verify_open(OpenParams(url=p.url), r)


def open_url(p: OpenParams) -> ToolResult:
    try:
        url = normalize_url(p.url)
    except ValueError as e:
        return ToolResult.failure(str(e), retryable=False)
    if _use_my_browser():
        return _open_in_my_browser(url)
    page, err = _page_or_error()
    if err:
        return err
    try:
        response = page.goto(url, wait_until="domcontentloaded")
        page.bring_to_front()
        title = page.title()
    except PlaywrightError as e:
        return ToolResult.failure(f"could not load {url}: {_first_line(e)}")
    result = ToolResult.success(f"opened {page.url} ('{title}')")
    result.context.update(requested=url, status=response.status if response else None)
    return result


def verify_open(p: OpenParams, r: ToolResult) -> VerifyResult:
    if r.context.get("mine"):
        # Orbit cannot see inside your own browser, only that Brave answered and is in front.
        fg = real.brave_in_front()
        if fg:
            title = mine.wait_until_loaded(fg.hwnd)
            return VerifyResult(True, f"Brave is in front, showing '{title}'")
        return VerifyResult(False, "Brave did not come to the front")
    page, err = _page_or_error()
    if err:
        return VerifyResult(False, err.error)
    status = r.context.get("status")
    if status and status >= 400:
        return VerifyResult(False, f"the site answered HTTP {status}")
    if page.url.startswith(("chrome-error:", "about:blank")):
        return VerifyResult(False, f"the browser is on an error page ({page.url})")
    if not same_site(page.url, r.context["requested"]):
        return VerifyResult(False, f"expected {r.context['requested']} but the browser is on {page.url}")
    return VerifyResult(True, f"on {page.url}, title '{page.title()}'")


def search_web(p: SearchParams) -> ToolResult:
    return open_url(OpenParams(url="https://duckduckgo.com/?q=" + quote_plus(p.query)))


# ---------------------------------------------------------------- read the page
def page_info(p: NoParams) -> ToolResult:
    if _use_my_browser():
        return mine.page_info()
    page, err = _page_or_error()
    if err:
        return err
    try:
        text = page.evaluate("() => (document.body ? document.body.innerText : '').slice(0, 800)")
        return ToolResult.success(f"on {page.url} ('{page.title()}')", data=text)
    except PlaywrightError as e:
        return ToolResult.failure(f"could not read the page: {_first_line(e)}")


# ---------------------------------------------------------------- click
class ClickParams(BaseModel):
    text: str = Field(description="visible text of the button or link to click")


def _snapshot(page) -> tuple:
    body = page.evaluate("() => (document.body ? document.body.innerText : '').length")
    return page.url, page.title(), body


def click_text(p: ClickParams) -> ToolResult:
    if _use_my_browser():
        return mine.click(p.text)
    page, err = _page_or_error()
    if err:
        return err
    try:
        before = _snapshot(page)
        target = None
        for locator in (
            page.get_by_role("button", name=p.text),
            page.get_by_role("link", name=p.text),
            page.get_by_text(p.text),
        ):
            if locator.count() > 0:
                target = locator.first
                break
        if target is None:
            return ToolResult.failure(f"nothing on the page with the text '{p.text}'", retryable=False)
        target.click()
    except PlaywrightError as e:
        return ToolResult.failure(f"could not click '{p.text}': {_first_line(e)}")
    result = ToolResult.success(f"clicked '{p.text}'")
    result.context["before"] = before
    return result


def verify_click(p: ClickParams, r: ToolResult) -> VerifyResult:
    if r.context.get("mine"):
        return mine.verify_click(r)
    page, err = _page_or_error()
    if err:
        return VerifyResult(False, err.error)

    def changed():
        try:
            now = _snapshot(page)
        except PlaywrightError:
            return True  # the page was navigating away, which is a change
        return now if now != r.context["before"] else None

    now = _wait_for(changed)
    if now:
        return VerifyResult(True, "the page changed after the click")
    return VerifyResult(False, "the page looks the same after the click (it may have had no visible effect)")


# ---------------------------------------------------------------- youtube
class YoutubeParams(BaseModel):
    query: str = Field(default="", description="what to search on YouTube and play, e.g. lofi beats; empty = the first video on the YouTube page already open")


_VIDEO_STATE_JS = """() => {
  const v = document.querySelector('video');
  return v ? {paused: v.paused, time: v.currentTime, ready: v.readyState} : null;
}"""


def youtube_play(p: YoutubeParams) -> ToolResult:
    if _use_my_browser():
        url = "https://www.youtube.com/results?search_query=" + quote_plus(p.query) if p.query.strip() else ""
        return mine.play_first_video(p.query.strip(), _open_in_my_browser, url)
    page, err = _page_or_error()
    if err:
        return err
    try:
        page.goto("https://www.youtube.com/results?search_query=" + quote_plus(p.query), wait_until="domcontentloaded")
        page.bring_to_front()
        if "consent." in page.url:
            return ToolResult.failure("YouTube is asking for cookie consent; accept it once in the Orbit browser window", retryable=False)
        first = page.locator("ytd-video-renderer a#video-title").first
        first.wait_for(state="visible")
        first.click()
        page.wait_for_url("**/watch*")
        page.locator("video").first.wait_for(state="attached")
        page.evaluate("() => { const v = document.querySelector('video'); if (v && v.paused) v.play(); }")
        # The tab title is just "YouTube" until the video's own title loads.
        _wait_for(lambda: page.title() not in ("", "YouTube"), timeout=5.0)
        title = page.title().removesuffix(" - YouTube")
    except PlaywrightError as e:
        return ToolResult.failure(f"could not play '{p.query}' on YouTube: {_first_line(e)}")
    return ToolResult.success(f"playing '{title}' on YouTube")


def verify_youtube_play(p: YoutubeParams, r: ToolResult) -> VerifyResult:
    if r.context.get("mine"):
        return mine.verify_play(r)
    page, err = _page_or_error()
    if err:
        return VerifyResult(False, err.error)
    if "/watch" not in page.url:
        return VerifyResult(False, f"not on a video page ({page.url})")

    def playing():
        state = page.evaluate(_VIDEO_STATE_JS)
        return state if state and not state["paused"] and state["ready"] >= 2 else None

    state = _wait_for(playing, timeout=8.0)
    if state:
        return VerifyResult(True, f"video is playing at {state['time']:.0f}s")
    return VerifyResult(False, "the video element is not playing")


# ---------------------------------------------------------------- fullscreen
class FullscreenParams(BaseModel):
    on: bool = Field(default=True, description="true to enter fullscreen, false to leave it")


def _is_fullscreen(page) -> bool:
    return bool(page.evaluate("() => !!document.fullscreenElement"))


def set_fullscreen(p: FullscreenParams) -> ToolResult:
    if _use_my_browser():
        return mine.fullscreen(p.on)
    page, err = _page_or_error()
    if err:
        return err
    try:
        if _is_fullscreen(page) == p.on:
            return ToolResult.success("already " + ("fullscreen" if p.on else "not fullscreen"))
        if not p.on:
            page.evaluate("() => document.exitFullscreen()")
        else:
            if page.locator("video").count() == 0:
                return ToolResult.failure("there is no video on this page to make fullscreen", retryable=False)
            button = page.locator("button.ytp-fullscreen-button")
            if button.count() > 0:  # YouTube: use its own button (a real click is needed to enter fullscreen)
                page.locator("#movie_player").hover()
                button.first.click()
            else:
                page.locator("video").first.dblclick()
    except PlaywrightError as e:
        return ToolResult.failure(f"could not change fullscreen: {_first_line(e)}")
    return ToolResult.success("entered fullscreen" if p.on else "left fullscreen")


def verify_fullscreen(p: FullscreenParams, r: ToolResult) -> VerifyResult:
    if r.context.get("mine"):
        return mine.verify_fullscreen(r)
    page, err = _page_or_error()
    if err:
        return VerifyResult(False, err.error)
    ok = _wait_for(lambda: _is_fullscreen(page) == p.on)
    return VerifyResult(bool(ok), f"document.fullscreenElement is {'set' if _is_fullscreen(page) else 'empty'}")


# ---------------------------------------------------------------- registration
def register_browser_tools(registry: Registry) -> None:
    def add(name, description, params, run, verify, **kw):
        registry.register(Tool(name=name, description=description, params=params, layer="browser",
                               risk="low", run=run, verify=verify, **kw))

    add("browser_open", "Open a website address in the browser.", OpenParams, open_url, verify_open)
    add("browser_navigate", "Go back, forward, reload, or to the site's home page, in the browser tab that is open.", NavigateParams, navigate, verify_navigate,
        idempotent=False)
    add("browser_go_to", "Load a web address in the CURRENT browser tab (open does it in a new tab).", GoToParams, go_to, verify_go_to)
    add("browser_tabs", "List the tabs open in the browser.", NoParams, list_tabs, read_only_verify)
    add("browser_close_tabs", "Close the current tab, all the other tabs, or tabs whose title matches (e.g. all other youtube tabs).",
        CloseTabsParams, close_tabs, verify_close_tabs, idempotent=False,
        needs_confirm=lambda p: p.scope != "this" and _use_my_browser() and mine.count_targets(p.scope, p.match.strip()) >= 4)
    add("browser_switch_tab", "Switch to an open browser tab by part of its title.", SwitchTabParams, switch_tab, verify_switch_tab)
    add("browser_search", "Search the web for a phrase in the browser.", SearchParams, search_web, verify_open)
    add("browser_page_info", "Read the title, address and text of the page now open in the browser.", NoParams, page_info, read_only_verify)
    add("browser_click", "Click a button or link on the open page by its visible text.", ClickParams, click_text, verify_click,
        idempotent=False, needs_confirm=lambda p: bool(_RISKY_CLICK.search(p.text)))
    add("browser_youtube_play", "Search YouTube for something and play the first video.", YoutubeParams, youtube_play, verify_youtube_play,
        idempotent=False)  # a retry would click another video
    add("browser_fullscreen", "Make the video on the open page fullscreen, or leave fullscreen.", FullscreenParams, set_fullscreen, verify_fullscreen)

"""Click a named thing in ANY app's window: through its accessible controls when it has them, otherwise by reading the
words on screen (OCR) and clicking where they are. Verified afterwards by whether the window actually changed."""
import re
import time
from typing import Callable

from ..base import ToolResult, VerifyResult
from . import screen, symbols, uia, windows

_RISKY = re.compile(
    r"\b(buy|purchase|pay|checkout|order|send|post|publish|delete|remove|submit|confirm|unsubscribe|subscribe|"
    r"sign out|log ?out|transfer|donate|install|uninstall|format|erase|reset)\b", re.I)
SETTLE_S = 4.0
CHANGE_PIXELS = 12  # how many sampled pixels must differ for "the window changed" (a new digit is a few dozen)


def _wait(check: Callable[[], object], timeout: float, interval: float = 0.3):
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = check()
        except Exception:  # a window that is changing can fail a read for a moment: "not yet", not a crash
            value = None
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def target_window(query: str) -> windows.Window | None:
    """The window to work in: the best match for the name, or (no name) the one in front that is not Orbit's own terminal."""
    own = windows.console_hwnd()
    found = [w for w in windows.match_windows(windows.list_windows(), [query] if query else []) if w.hwnd != own]
    if query:
        return found[0] if found else None
    fg = windows.foreground_window()
    return fg if fg and fg.hwnd != own else None


def _shot_of(win: windows.Window) -> screen.Shot:
    left, top, right, bottom = windows.visible_rect(win.hwnd)
    return screen.capture(left, top, right - left, bottom - top)


def click(text: str, window: str, kind: str = "any") -> ToolResult:
    win = target_window(window)
    if win is None:
        what = f"a window called '{window}'" if window else "a window to work in"
        open_now = ", ".join(repr(w.title[:40]) for w in windows.list_windows()[:8])
        return ToolResult.failure(f"I can't find {what}. Open windows include: {open_now}", retryable=False, not_found=True)
    if windows.is_minimized(win.hwnd):
        windows.restore(win.hwnd)
    windows.focus(win.hwnd)  # the clicks go to what is on top at that spot, so the window must be in front
    time.sleep(0.25)
    try:
        before = _shot_of(win)
        names = [c for c in uia.window_controls(win.hwnd)]
        hits = uia.find(names, text, kind)
        how, matched = "", ""
        if hits:
            target = hits[0]
            if _RISKY.search(target.name) and not _RISKY.search(text):
                return ToolResult.failure(f"the closest match is '{target.name[:60]}', which looks risky and you did not name it. "
                                          "Say its exact name if you really mean it.", retryable=False)
            how, matched = uia.click(target), target.name
        else:
            words = screen.read_words(before)
            spot = screen.find_text(words, text)
            symbol = symbols.identify(text)
            by_symbol = False
            if spot is None and symbol is not None:
                spot = screen.find_glyph(words, symbol.glyphs)      # a picture the OCR engine reads as a character: x, +, a heart...
                by_symbol = spot is not None
            if spot is None:
                seen = "; ".join(c.name[:40] for c in names[:10]) or "no named controls"
                on_screen = " ".join(w.text for w in words[:25]) or "no readable text"
                return ToolResult.failure(
                    f"nothing in '{win.title[:40]}' is called '{text}'. Controls I can see: {seen}. Words on screen: {on_screen}",
                    retryable=False, not_found=True)
            x, y, matched = spot
            if _RISKY.search(matched) and not _RISKY.search(text):
                return ToolResult.failure(f"the words I found there ('{matched}') look risky and you did not name them", retryable=False)
            screen.click_at(before.left + x, before.top + y)
            how = "symbol" if by_symbol else "ocr"
    except (screen.ScreenError, OSError) as e:
        return ToolResult.failure(f"could not look at the window: {e}", retryable=False)
    means = {"invoke": "pressed its control", "focus": "put the cursor in it", "mouse": "clicked it",
             "ocr": "clicked the words on screen", "symbol": "clicked the symbol on screen"}.get(how, how)
    result = ToolResult.success(f"clicked '{matched[:60]}' in '{win.title[:40]}' ({means})")
    result.context.update(hwnd=win.hwnd, before=before, title=win.title, how=how)
    return result


def verify_click(r: ToolResult) -> VerifyResult:
    hwnd, before = r.context["hwnd"], r.context["before"]

    def changed() -> str | None:
        now = next((w for w in windows.list_windows() if w.hwnd == hwnd), None)
        if now is None:
            return "the window closed"
        if now.title != r.context["title"]:
            return f"the window title changed to '{now.title[:50]}'"
        left, top, right, bottom = windows.visible_rect(hwnd)
        after = screen.capture(left, top, right - left, bottom - top)
        pixels = screen.changed_pixels(before, after)
        return f"the window changed on screen ({pixels} pixels differ)" if pixels >= CHANGE_PIXELS else None

    why = _wait(changed, SETTLE_S)
    return VerifyResult(True, why) if why else VerifyResult(False, "the window looks exactly the same after the click (it may have had no visible effect)")


def read_window(window: str) -> ToolResult:
    win = target_window(window)
    if win is None:
        return ToolResult.failure(f"I can't find a window called '{window}'" if window else "there is no window in front to read",
                                  retryable=False, not_found=True)
    try:
        names = uia.window_controls(win.hwnd)
        lines = [f"{c.kind}: {c.name[:60]}" for c in names[:40]]
        if len(names) < 6:  # custom-drawn: read the words on screen instead
            words = screen.read_words(_shot_of(win))
            lines.append("text on screen: " + " ".join(w.text for w in words[:80]))
    except (screen.ScreenError, OSError) as e:
        return ToolResult.failure(f"could not look at the window: {e}", retryable=False)
    return ToolResult.success(f"read '{win.title[:50]}'", data="\n".join(lines))

"""Typing into the box that has the keyboard (a search box, a form, Notepad), and pressing Enter.

"click the search and type tamil news live": the first part puts the cursor in the box (uia.click focuses a text box), this
part types. Safety, because a typing tool is a powerful thing:

  * it never types into a password field, and never into Orbit's own terminal;
  * in a browser, it types only when a real text box has the keyboard (so it cannot send letters to a page's hotkeys,
    like YouTube's "k" and "f");
  * the words typed must be words the person actually said (checked by the agent, see Tool.user_text), so text found on a web
    page can never get typed;
  * Enter is pressed only when asked for.
"""
from __future__ import annotations

import re
import time

from pydantic import BaseModel, Field

from ..base import ToolResult, VerifyResult
from . import keyboard, screen, uia, uiclick, windows

BROWSERS = ("brave", "chrome", "msedge", "firefox", "opera", "vivaldi", "chromium")
SETTLE_S = 3.0
CHANGE_PIXELS = 12            # how many sampled pixels must differ for "the window changed" (same bar as clicking)
# Boxes where new words should REPLACE the old ones ("click the search and type cats" on a results page that already shows a query).
# Only these: selecting everything in an ordinary text area could wipe something the person wrote.
_REPLACEABLE = re.compile(r"search|find|query|filter|address|url", re.I)


class TypeParams(BaseModel):
    text: str = Field(default="", max_length=500,
                      description="the words to type, exactly as the user said them (empty = just press Enter)")
    into: str = Field(default="", description="the name of the text box to click first, e.g. search (empty = the box that already has the cursor)")
    enter: bool = Field(default=False, description="press Enter after typing (runs a search, submits)")
    window: str = Field(default="", description="the app window to type in (empty = the window in front)")
    replace: bool = Field(default=False, description="a search box that already holds words: replace them instead of adding to them")


def _snapshot(win: windows.Window) -> tuple[str, str]:
    title = next((w.title for w in windows.list_windows() if w.hwnd == win.hwnd), "")
    return title, uia.address_bar(win.hwnd) if win.exe in BROWSERS else ""


def cannot_type_into(field: uia.Field | None, in_browser: bool) -> str:
    """Why typing would be wrong right now ('' = fine). Pure, so it is unit-tested."""
    if field is None:
        return "I can't tell where the cursor is. Click a text box first (say 'click the search box')"
    if field.password:
        return "the box that has the cursor is a password field, and Orbit never types into those"
    if field.read_only:
        return f"'{field.name or field.kind}' is read-only, so nothing can be typed there"
    if in_browser and field.kind not in ("text field", "combo box") and field.read_only is not False:
        return ("no text box has the cursor on this page (typing now would only trigger the page's keyboard shortcuts). "
                "Click a text box first, for example 'click the search box'")
    return ""


def type_into(p: TypeParams) -> ToolResult:
    text = " ".join(p.text.split())
    if not text and not p.enter:
        return ToolResult.failure("there is nothing to type", retryable=False)
    win = uiclick.target_window(p.window.strip())
    if win is None:
        what = f"a window called '{p.window}'" if p.window.strip() else "a window to type in (Orbit's own terminal is never typed into)"
        return ToolResult.failure(f"I can't find {what}", retryable=False, not_found=True)
    if windows.is_minimized(win.hwnd):
        windows.restore(win.hwnd)
    in_browser = win.exe in BROWSERS
    fg = windows.foreground_window()
    if fg is None or fg.hwnd != win.hwnd:
        windows.focus(win.hwnd)
    if p.into.strip():
        items = uia.controls(win.hwnd) if in_browser else uia.window_controls(win.hwnd)
        boxes = uia.find(items, p.into.strip(), kind="field")
        if not boxes:
            seen = "; ".join(c.name[:40] for c in items if c.kind in uia.FIELD_KINDS and not c.offscreen) or "no text boxes"
            return ToolResult.failure(f"there is no text box called '{p.into}' in '{win.title[:40]}'. Text boxes I can see: {seen}",
                                      retryable=False, not_found=True)
        uia.click(boxes[0])
        time.sleep(0.12)
    field = uia.focused_field()
    problem = cannot_type_into(field, in_browser)
    if problem:
        return ToolResult.failure(problem, retryable=False)
    fg = windows.foreground_window()
    if fg is None or fg.hwnd != win.hwnd:
        return ToolResult.failure(f"'{win.title[:40]}' lost the keyboard focus, so I did not type (the letters would have gone elsewhere)",
                                  retryable=False)
    before = _snapshot(win)
    readable = bool(field and field.value is not None)
    picture = None
    if not readable:
        try:
            picture = uiclick._shot_of(win)                 # a box that cannot be read back is checked by the window changing on screen
        except (screen.ScreenError, OSError):
            picture = None
    replaced = False
    one_line = field is not None and bool(field.value) and "\n" not in field.value
    if text and (p.replace or p.into.strip()) and one_line and _REPLACEABLE.search(field.name):
        keyboard.press_keys("ctrl+a")                       # select what the search box holds; the typing below replaces it
        time.sleep(0.06)
        replaced = True
    if text:
        keyboard.type_naturally(text)
    summary = f"typed '{text}'" if text else ""
    if p.enter:
        time.sleep(0.15)
        windows.press_enter()
        summary = (summary + " and " if summary else "") + "pressed Enter"
    result = ToolResult.success(f"{summary} in '{win.title[:40]}'")
    result.context.update(hwnd=win.hwnd, exe=win.exe, text=text, enter=p.enter, before=before, readable=readable, picture=picture,
                          field_before="" if replaced else (field.value if field else None))
    if replaced:
        result.summary += " (replacing the words that were in the box)"
    return result


def _window_changed(ctx: dict) -> bool:
    """Did the window change on screen since before the typing? (For boxes whose words cannot be read back.)"""
    if ctx["picture"] is None:
        return False
    try:
        return screen.changed_pixels(ctx["picture"], uiclick._shot_of(windows.Window(ctx["hwnd"], "", ""))) >= CHANGE_PIXELS
    except (screen.ScreenError, OSError):
        return False


def verify_type(p: TypeParams, r: ToolResult) -> VerifyResult:
    ctx = r.context
    win = next((w for w in windows.list_windows() if w.hwnd == ctx["hwnd"]), None)
    if win is None:
        return VerifyResult(True, "the window closed after the keys were sent")
    if ctx["enter"]:
        deadline = time.monotonic() + SETTLE_S
        while True:
            if _snapshot(win) != ctx["before"]:
                return VerifyResult(True, "the page or window changed after Enter")
            if ctx["readable"]:
                field = uia.focused_field()
                if field is not None and field.value is not None and field.value != (ctx["field_before"] or "") + ctx["text"]:
                    return VerifyResult(True, "the text box changed after Enter")
            elif _window_changed(ctx):
                return VerifyResult(True, "the window changed on screen after Enter")
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        if not ctx["text"]:
            return VerifyResult(False, "nothing visibly changed after Enter (the box may not do anything with it)")
        return VerifyResult(False, "the page did not change after Enter (a search page can take longer than this: look at the window)")
    if not ctx["readable"]:
        deadline = time.monotonic() + SETTLE_S
        while not _window_changed(ctx):
            if time.monotonic() >= deadline:
                return VerifyResult(False, "nothing changed on screen after typing (this window may not take typed text)")
            time.sleep(0.25)
        return VerifyResult(True, "the window changed on screen after typing (its text cannot be read back)")
    field = uia.focused_field()
    if field is None or field.value is None:
        return VerifyResult(True, "the keys were sent; this box can no longer be read back to double-check them")
    wanted = ctx["text"].lower()
    if wanted in field.value.lower():
        return VerifyResult(True, f"the box now reads '{field.value[:60]}'")
    return VerifyResult(False, f"the box reads '{field.value[:60]}', not what was typed")

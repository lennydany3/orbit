"""Look at the screen the way a person points at it: "what does this mean?", "what's going on here?", "I have a doubt about this".

Orbit has no eyes, so it reads what Windows and OCR can tell it about the spot the mouse pointer is on:
  * the window under the pointer (its title and program),
  * the control under the pointer, as UI Automation names it,
  * any text the person has selected (the best clue when someone says "what does this mean"),
  * the text visible in the window's document (browsers, editors) and, for everything else, the text OCR reads off the window,
  * the words nearest the pointer, which is how "this" gets its meaning.

It reads and sends nothing anywhere by itself: it hands a block of text to the agent, which asks the language model. The tool is
`model_visible=False`, so the language model can never call it (or be tricked into calling it) on its own: only the person's own
question triggers it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from ..base import ToolResult
from . import mouse, screen, textinput, uia, windows

MAX_PAGE_TEXT = 2800
MAX_OCR_TEXT = 2800
MAX_NEAR = 600


class LookParams(BaseModel):
    pass


@dataclass
class ScreenView:
    x: int
    y: int
    title: str
    exe: str
    element: dict | None = None
    selected: str = ""
    page_text: str = ""
    under: str = ""                  # the word the pointer is on (OCR)
    near: str = ""                   # the lines of text around the pointer (OCR)
    ocr_text: str = ""               # all the text OCR read from the window
    notes: list[str] = field(default_factory=list)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def format_view(view: ScreenView, limit: int = 4000) -> str:
    """The view as the block of text the language model reads. Pure: unit-tested. Longer sections are trimmed to fit `limit`."""
    lines = [f'Window: "{view.title}" ({view.exe or "unknown program"})']
    pointing = []
    if view.under:
        pointing.append(f'on the word "{view.under}"')
    if view.element and (view.element.get("name") or view.element.get("text")):
        label = view.element.get("name") or view.element["text"]
        pointing.append(f'over a {view.element["kind"]} called "{_clip(label, 80)}"')
    lines.append("Mouse pointer: " + (", ".join(pointing) if pointing else "over the window (nothing readable right under it)"))
    if view.element and view.element.get("trail"):
        lines.append("Inside: " + "; ".join(view.element["trail"]))
    if view.selected:
        lines.append(f'Text the user has selected: "{_clip(view.selected, 1200)}"')
    if view.near:
        lines.append("Text around the pointer (read from the screen, lines separated by /): " + _clip(view.near, MAX_NEAR))
    used = sum(len(line) for line in lines)
    room = max(400, limit - used - 200)
    if view.page_text:
        lines.append("Text visible in the window: " + _clip(view.page_text, min(MAX_PAGE_TEXT, room)))
    if len(view.page_text) < 150 and view.ocr_text:
        lines.append("Text read from the window (OCR, may contain mistakes): " + _clip(view.ocr_text, min(MAX_OCR_TEXT, room)))
    if not (view.page_text or view.ocr_text or view.near or view.selected):
        lines.append("No text could be read from this window (it may show only pictures, video or a game).")
    lines += [f"Note: {n}" for n in view.notes]
    return "\n".join(lines)[:limit]


def _own_terminal_note(title: str) -> str:
    return f"the pointer was over Orbit's own terminal, so this is the window behind it: '{title[:50]}'"


def gather() -> ScreenView | None:
    """Look at the screen now (None when there is no window to look at). Every source is optional: one that fails is left out."""
    x, y = mouse.position()
    own = windows.console_hwnd()
    notes: list[str] = []
    win = windows.window_at(x, y)
    if win is None or win.hwnd == own:
        fg = windows.foreground_window()
        fallback = fg if fg and fg.hwnd != own else next((w for w in windows.list_windows() if w.hwnd != own), None)
        if fallback is None:
            return None
        notes.append(_own_terminal_note(fallback.title) if win is not None else
                     f"the pointer was not over a window, so this is the window in front: '{fallback.title[:50]}'")
        win = fallback
    view = ScreenView(x, y, win.title, win.exe, notes=notes)
    left, top, right, bottom = windows.visible_rect(win.hwnd)
    inside = left <= x < right and top <= y < bottom
    if not inside:
        view.notes.append("the pointer is outside this window, so nothing is 'under' it")
    if inside:
        try:
            view.element = uia.describe_at(x, y)
        except Exception:                      # noqa: BLE001 - one source failing never stops the others
            view.element = None
    try:
        if win.exe in textinput.BROWSERS:      # a page's own text is exact (and includes small print); other apps' is mostly noise
            visible, selected = uia.window_text(win.hwnd, MAX_PAGE_TEXT)
            view.page_text = visible.replace("￼", " ")
            view.selected = selected
        view.selected = view.selected or uia.focused_selection()
    except Exception:                          # noqa: BLE001
        pass
    if not windows.is_minimized(win.hwnd) and right - left > 8 and bottom - top > 8:
        try:
            words = screen.read_words(screen.capture(left, top, right - left, bottom - top))
            rx, ry = x - left, y - top
            if inside:
                under = screen.word_under(words, rx, ry)
                view.under = under.text if under else ""
                view.near = " / ".join(screen.words_to_lines(screen.words_near(words, rx, ry)))
            view.ocr_text = " ".join(screen.words_to_lines(words))
        except (screen.ScreenError, OSError) as e:
            view.notes.append(f"the screen could not be read with OCR ({e})")
    return view


def look(p: LookParams) -> ToolResult:
    view = gather()
    if view is None:
        return ToolResult.failure("there is no window on screen to look at", retryable=False)
    block = format_view(view)
    result = ToolResult.success(f"looked at '{view.title[:50]}' ({len(block)} characters of text)", data=block)
    result.context["view"] = view
    return result

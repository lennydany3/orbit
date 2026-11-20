"""Windows UI Automation: read and click NAMED controls in another app's window.

Chromium browsers (Brave, Chrome) publish the web page itself through UI Automation, so this works on your real,
logged-in browser window without remote debugging, which Chromium blocks for a profile that is in use. It is the same
interface screen readers use: Orbit sees links, buttons and fields by name, and presses them the way assistive
software does. Everything it reads from a page is untrusted data, never instructions.
"""
import difflib
import re
import time
from dataclasses import dataclass

import comtypes.client

from ... import visual
from . import mouse, symbols

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

_CLSID_UIAUTOMATION = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_automation = None

_APP_KINDS = {  # what to look for in a normal desktop app window (more than in a web page)
    UIA.UIA_ButtonControlTypeId: "button", UIA.UIA_HyperlinkControlTypeId: "link", UIA.UIA_MenuItemControlTypeId: "menu item",
    UIA.UIA_TabItemControlTypeId: "tab", UIA.UIA_CheckBoxControlTypeId: "checkbox", UIA.UIA_RadioButtonControlTypeId: "radio button",
    UIA.UIA_ListItemControlTypeId: "list item", UIA.UIA_TreeItemControlTypeId: "tree item", UIA.UIA_ComboBoxControlTypeId: "combo box",
    UIA.UIA_SplitButtonControlTypeId: "button", UIA.UIA_EditControlTypeId: "text field",
}
_KINDS = {
    UIA.UIA_HyperlinkControlTypeId: "link", UIA.UIA_ButtonControlTypeId: "button", UIA.UIA_TabItemControlTypeId: "tab",
    UIA.UIA_MenuItemControlTypeId: "menu item", UIA.UIA_CheckBoxControlTypeId: "checkbox",
    UIA.UIA_ListItemControlTypeId: "list item", UIA.UIA_RadioButtonControlTypeId: "radio button",
    UIA.UIA_EditControlTypeId: "text field", UIA.UIA_ComboBoxControlTypeId: "combo box",     # the search box, a form field
}
FIELD_KINDS = ("text field", "combo box")
_WARM_UP_ATTEMPTS = 4  # Chromium only builds a page's accessibility tree once something asks for it: the first looks are empty
_COM_RETRIES = 3       # the tree is rebuilt while a page changes or goes fullscreen; a call in that moment fails, so try again


@dataclass
class Control:
    name: str
    kind: str
    offscreen: bool
    element: object  # the COM element; only click() uses it
    enabled: bool = True
    selected: bool = False  # for tabs: is this the current one
    rid: tuple = ()         # a stable id for the element, to recognise it again after other tabs close


def _uia():
    global _automation
    if _automation is None:
        _automation = comtypes.client.CreateObject(_CLSID_UIAUTOMATION, interface=UIA.IUIAutomation)
    return _automation


def _type_condition(type_ids):
    uia = _uia()
    cond = None
    for type_id in type_ids:
        one = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, type_id)
        cond = one if cond is None else uia.CreateOrCondition(cond, one)
    return cond


def _retrying(fn):
    """Run a UI Automation call, trying again if the window's tree is being rebuilt at that moment."""
    last = None
    for attempt in range(_COM_RETRIES):
        try:
            return fn()
        except comtypes.COMError as e:
            last = e
            time.sleep(0.4)
    raise last


def controls(hwnd: int, limit: int = 600) -> list[Control]:
    """Named links, buttons, tabs... of the PAGE in a window (the browser's own toolbar is left out), on-screen order.
    The first looks may need a moment while the browser builds the page's accessibility tree."""
    return _retrying(lambda: _controls(hwnd, limit))


def _controls(hwnd: int, limit: int) -> list[Control]:
    root = _uia().ElementFromHandle(hwnd)
    cond = _type_condition(list(_KINDS))
    found = None
    for attempt in range(_WARM_UP_ATTEMPTS):
        page = root.FindFirst(UIA.TreeScope_Descendants, _type_condition([UIA.UIA_DocumentControlTypeId]))
        found = page.FindAll(UIA.TreeScope_Descendants, cond) if page else None          # (a missing page is a NULL pointer, not None)
        if found is not None and found.Length > 0:
            break
        time.sleep(1.0)
    if found is None:
        return []  # no page yet (still loading): say so, rather than listing the browser's own toolbar as if it were the page
    out: list[Control] = []
    for i in range(min(found.Length, limit)):
        el = found.GetElement(i)
        name = (el.CurrentName or "").strip()
        if name:
            out.append(Control(name, _KINDS.get(el.CurrentControlType, "control"), bool(el.CurrentIsOffscreen), el))
    return out


def address_bar(hwnd: int) -> str:
    """The text of the browser's address bar ('' if none can be read)."""
    try:
        return _retrying(lambda: _address_bar(hwnd))
    except comtypes.COMError:
        return ""


def _address_bar(hwnd: int) -> str:
    root = _uia().ElementFromHandle(hwnd)
    edits = root.FindAll(UIA.TreeScope_Descendants, _type_condition([UIA.UIA_EditControlTypeId]))
    for i in range(edits.Length):
        el = edits.GetElement(i)
        if "address" in (el.CurrentName or "").lower():
            try:
                pattern = el.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)
                return pattern.CurrentValue or ""
            except Exception:
                return ""
    return ""


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


# Words that describe HOW to click rather than WHAT ("the", "video", "button"); typos of them ("viedo", "theh") count too.
_FILLER = frozenset("the a an video videos link button one please click on my that this it song music channel tab".split())
# Words that point at a picture or a box ("the search icon", "the search box"): never part of a control's name. Exact words only
# (no typo matching), because these are short.
_EXACT_FILLER = frozenset("icon icons symbol symbols glyph logo box bar field input textbox".split())


def _is_filler(word: str) -> bool:
    return word in _FILLER or word in _EXACT_FILLER or any(len(f) >= 3 and difflib.SequenceMatcher(None, word, f).ratio() >= 0.8 for f in _FILLER)


def _edit_distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _word_in(word: str, name_words: list[str]) -> bool:
    """Is this query word (exact, part of, or a typo of) one of the control's words? ("traver" ~ "trevor", "walles" ~ "wallace")"""
    for n in name_words:
        if word == n or (len(word) >= 4 and word in n):
            return True
        if len(word) < 4 or abs(len(word) - len(n)) > 3:
            continue
        ratio = difflib.SequenceMatcher(None, word, n).ratio()
        same_start = len(word) >= 4 and word[:4] == n[:4]
        if ratio >= 0.75 or (len(word) >= 5 and _edit_distance(word, n) <= 2) or (same_start and ratio >= 0.7):
            return True
    return False


def match_score(query: str, name: str) -> float:
    """How well a control's name fits what the user said: 0 = not at all, higher = better. Pure, so it is unit-tested.
    Forgives typos and ignores filler words, so "click theh traver walles viedo" finds "Trevor Wallace Making ..."."""
    q, n = _words(query), _words(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 4.0
    joined_q, joined_n = " ".join(q), " ".join(n)
    if joined_n.startswith(joined_q):
        return 3.5
    if joined_q in joined_n:
        return 3.0
    content = [w for w in q if not _is_filler(w)] or q
    covered = sum(1 for w in content if _word_in(w, n))
    share = covered / len(content)
    if share < 0.6:  # most of the words, not just one of them
        return 0.0
    return 2.0 * share + 0.1 * min(1.0, len(content) / len(n))  # tiny bonus for a tighter name


def find(items: list[Control], query: str, kind: str = "any") -> list[Control]:
    """Controls that fit `query`, best first (visible ones before off-screen ones at equal quality).

    `query` may name a control ("sign in") or a PICTURE ("the search icon", "magnifying glass", "three dots"): a picture is
    looked up under the names it usually has (see symbols.py). `kind` "field" keeps only text boxes ("click the search box
    and type ..."); "button" prefers buttons over text boxes and links."""
    symbol = symbols.identify(query)
    picture = symbol is not None or symbols.wants_a_picture(query)
    wants_field = kind == "field" or (kind == "any" and symbols.wants_a_field(query))
    scored = []
    for c in items:
        score = match_score(query, c.name)
        if symbol is not None:
            alias = max((match_score(name, c.name) - 0.05 * rank for rank, name in enumerate(symbol.names)), default=0.0)
            score = max(score, alias * 0.97)
        if score <= 0:
            continue
        is_field = c.kind in FIELD_KINDS
        if kind == "field" and not is_field:
            continue
        if wants_field and is_field:
            score += 0.5
        elif (picture or kind == "button") and c.kind in ("button", "menu item"):
            score += 0.4                                        # a picture on screen is almost always a button
        if kind == "button" and is_field:
            score -= 0.6
        scored.append((score, c))
    scored.sort(key=lambda sc: (-sc[0], sc[1].offscreen))
    return [c for _, c in scored]


def center(control: Control) -> tuple[int, int] | None:
    """The middle of a control on screen, or None when it has no real position (scrolled away, or hidden)."""
    try:
        rect = control.element.CurrentBoundingRectangle
    except Exception:
        return None
    if rect.right - rect.left < 2 or rect.bottom - rect.top < 2:
        return None
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def _has_focus(el) -> bool:
    try:
        return bool(el.CurrentHasKeyboardFocus)
    except Exception:
        return False


def click(control: Control, use_mouse: bool = False) -> str:
    """Press a control, with Orbit's animated pointer gliding to it first. Returns how it was done: "invoke" (the accessible
    way), "focus" (a text box was given the keyboard), or "mouse" (a real click: used when asked to, or when the control
    cannot be invoked)."""
    el = control.element
    try:
        el.GetCurrentPattern(UIA.UIA_ScrollItemPatternId).QueryInterface(UIA.IUIAutomationScrollItemPattern).ScrollIntoView()
    except Exception:
        pass  # not scrollable, or already in view
    point = center(control)
    if point:
        visual.click_at(*point)                                 # the pointer arrives and presses; the real action follows at once
    if control.kind in FIELD_KINDS and not use_mouse:
        try:
            el.SetFocus()
        except Exception:
            pass
        time.sleep(0.08)
        if _has_focus(el):
            return "focus"
        if point:                                               # a page that ignores SetFocus: a real click puts the caret in the box
            mouse.click(*point)
            return "mouse"
        return "focus"
    if not use_mouse:
        try:
            el.GetCurrentPattern(UIA.UIA_InvokePatternId).QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()
            return "invoke"
        except Exception:
            pass
    if point:
        mouse.click(*point)
    return "mouse"


@dataclass
class Field:
    """The control that has the keyboard right now."""
    name: str
    kind: str                # "text field", "combo box", "document" or "other"
    value: str | None        # what is in it; None = this control cannot be read
    password: bool
    read_only: bool | None


_FOCUS_KINDS = {UIA.UIA_EditControlTypeId: "text field", UIA.UIA_ComboBoxControlTypeId: "combo box",
                UIA.UIA_DocumentControlTypeId: "document"}


def read_text(el, limit: int = 4000) -> str | None:
    """The text of a control (a text box, or a document such as Notepad's), or None if it exposes none."""
    try:
        pattern = el.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)
        return pattern.CurrentValue or ""
    except Exception:
        pass
    try:
        text = el.GetCurrentPattern(UIA.UIA_TextPatternId).QueryInterface(UIA.IUIAutomationTextPattern)
        return (text.DocumentRange.GetText(limit) or "").rstrip("\r\n")
    except Exception:
        return None


_TYPE_NAMES = {50000: "button", 50002: "checkbox", 50003: "combo box", 50004: "text field", 50005: "link", 50006: "image", 50007: "list item",
               50008: "list", 50011: "menu item", 50013: "radio button", 50018: "tab bar", 50019: "tab", 50020: "text", 50021: "toolbar",
               50023: "tree", 50024: "tree item", 50026: "group", 50028: "data grid", 50029: "data item", 50030: "document", 50032: "window",
               50033: "pane", 50034: "header", 50036: "table", 50037: "title bar"}


def type_name(el) -> str:
    return _TYPE_NAMES.get(el.CurrentControlType, "element")


def describe_at(x: int, y: int) -> dict | None:
    """What is at a screen position, as UI Automation knows it: {"name", "kind", "text", "trail"} (trail = the names of what
    contains it, nearest first). Apps that publish nothing (many Electron apps) give an unnamed "pane"."""
    try:
        el = _retrying(lambda: _uia().ElementFromPoint(UIA.tagPOINT(int(x), int(y))))
    except comtypes.COMError:
        return None
    if not el:
        return None
    info = {"name": (el.CurrentName or "").strip(), "kind": type_name(el), "text": (read_text(el, 600) or "").strip(), "trail": []}
    walker, node = _uia().ControlViewWalker, el
    for _ in range(6):
        try:
            node = walker.GetParentElement(node)
        except comtypes.COMError:
            break
        if not node:
            break
        name = (node.CurrentName or "").strip()
        if name:
            info["trail"].append(f"{type_name(node)} '{name[:50]}'")
    info["trail"] = info["trail"][:3]
    return info


def _text_pattern(el):
    return el.GetCurrentPattern(UIA.UIA_TextPatternId).QueryInterface(UIA.IUIAutomationTextPattern)


def _ranges_text(ranges, limit: int) -> str:
    pieces, total = [], 0
    for i in range(ranges.Length):
        piece = (ranges.GetElement(i).GetText(min(limit, 500)) or "").replace("￼", " ").strip()      # (￼ marks an embedded object)
        if piece:
            pieces.append(piece)
            total += len(piece) + 1
        if total >= limit:                                                    # a long page has hundreds of ranges: stop once there is enough
            break
    return " ".join(pieces)[:limit]


def window_text(hwnd: int, limit: int = 3000) -> tuple[str, str]:
    """(the text visible in a window's main document, the text selected in it), through UI Automation's text pattern. Browsers
    and editors that publish a document give both; ("", "") for anything else. A covered browser window publishes nothing."""
    def look():
        root = _uia().ElementFromHandle(hwnd)
        doc = root.FindFirst(UIA.TreeScope_Descendants, _type_condition([UIA.UIA_DocumentControlTypeId]))
        if not doc:
            return None
        return _text_pattern(doc)

    try:
        pattern = _retrying(look)
    except (comtypes.COMError, OSError, ValueError):
        return "", ""
    if pattern is None:
        return "", ""
    parts = []
    for read in (pattern.GetVisibleRanges, pattern.GetSelection):          # separately: reading no selection may fail, and must not lose the page
        try:
            parts.append(_ranges_text(read(), limit))
        except (comtypes.COMError, OSError, ValueError):
            parts.append("")
    return parts[0], parts[1]


def focused_selection(limit: int = 1500) -> str:
    """The text selected in whatever has the keyboard focus ("" if nothing is selected or it cannot be read)."""
    try:
        el = _uia().GetFocusedElement()
        return _ranges_text(_text_pattern(el).GetSelection(), limit) if el else ""
    except (comtypes.COMError, OSError, ValueError):
        return ""


def focused_field() -> Field | None:
    """What has the keyboard focus: a text box, a document, or something else. None if Windows cannot say."""
    try:
        el = _retrying(lambda: _uia().GetFocusedElement())
    except comtypes.COMError:
        return None
    if el is None:
        return None
    try:
        read_only = bool(el.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern).CurrentIsReadOnly)
    except Exception:
        read_only = None
    try:
        password = bool(el.CurrentIsPassword)
    except Exception:
        password = False
    return Field((el.CurrentName or "").strip(), _FOCUS_KINDS.get(el.CurrentControlType, "other"), read_text(el), password, read_only)


def page_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Where the web page itself sits on screen (below the browser's toolbar): (left, top, right, bottom)."""
    def look():
        page = _uia().ElementFromHandle(hwnd).FindFirst(UIA.TreeScope_Descendants, _type_condition([UIA.UIA_DocumentControlTypeId]))
        if not page:                                  # a page that is still loading comes back as a NULL pointer, not None
            return None
        r = page.CurrentBoundingRectangle
        return (r.left, r.top, r.right, r.bottom) if r.right - r.left > 20 and r.bottom - r.top > 20 else None

    try:
        return _retrying(look)
    except comtypes.COMError:
        return None


def toolbar_button(hwnd: int, name: str) -> Control | None:
    """A button of the browser's own toolbar by exact name ("Back", "Forward", "Reload")."""
    root = _uia().ElementFromHandle(hwnd)

    def look():
        buttons = root.FindAll(UIA.TreeScope_Descendants, _type_condition([UIA.UIA_ButtonControlTypeId]))
        for i in range(buttons.Length):
            el = buttons.GetElement(i)
            if (el.CurrentName or "").strip().lower() == name.lower():
                return Control(name, "button", False, el, enabled=bool(el.CurrentIsEnabled))
        return None

    return _retrying(look)


def set_address(hwnd: int, url: str) -> bool:
    """Type a web address into the browser's address bar and press Enter: loads it in the CURRENT tab."""
    def type_it():
        root = _uia().ElementFromHandle(hwnd)
        edits = root.FindAll(UIA.TreeScope_Descendants, _type_condition([UIA.UIA_EditControlTypeId]))
        for i in range(edits.Length):
            el = edits.GetElement(i)
            if "address" in (el.CurrentName or "").lower():
                el.SetFocus()
                el.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern).SetValue(url)
                return True
        return False

    if not _retrying(type_it):
        return False
    time.sleep(0.2)
    from . import windows
    windows.press_enter()
    return True


def link_target(control: Control) -> str:
    """Where a link goes (its address), or '' if it has none that can be read."""
    try:
        return control.element.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern).CurrentValue or ""
    except Exception:
        return ""


_TAB_NOISE = re.compile(r" - Memory usage - .*$")
_TAB_CLASSES = ("BraveTab", "Tab")  # Brave / Chrome tab strip items (YouTube's own "chips" are tab items too, but not these)


def tabs(hwnd: int) -> list[Control]:
    """The browser's open tabs, left to right (or top to bottom), with which one is current."""
    return _retrying(lambda: _tabs(hwnd))


def _tabs(hwnd: int) -> list[Control]:
    root = _uia().ElementFromHandle(hwnd)
    found = root.FindAll(UIA.TreeScope_Descendants, _type_condition([UIA.UIA_TabItemControlTypeId]))
    out: list[Control] = []
    for i in range(found.Length):
        el = found.GetElement(i)
        if el.CurrentClassName not in _TAB_CLASSES:
            continue
        try:
            selected = bool(el.GetCurrentPattern(UIA.UIA_SelectionItemPatternId).QueryInterface(UIA.IUIAutomationSelectionItemPattern).CurrentIsSelected)
        except Exception:
            selected = False
        out.append(Control(_TAB_NOISE.sub("", el.CurrentName or "").strip(), "tab", bool(el.CurrentIsOffscreen), el,
                           selected=selected, rid=tuple(el.GetRuntimeId())))
    return out


def select_tab(control: Control) -> None:
    _retrying(lambda: control.element.GetCurrentPattern(UIA.UIA_SelectionItemPatternId).QueryInterface(UIA.IUIAutomationSelectionItemPattern).Select())


def window_controls(hwnd: int, limit: int = 400) -> list[Control]:
    """Named controls anywhere in a desktop app's window (menus, buttons, tabs, fields...). Empty for custom-drawn apps."""
    return _retrying(lambda: _window_controls(hwnd, limit))


def _window_controls(hwnd: int, limit: int) -> list[Control]:
    root = _uia().ElementFromHandle(hwnd)
    found = root.FindAll(UIA.TreeScope_Descendants, _type_condition(list(_APP_KINDS)))
    out: list[Control] = []
    for i in range(min(found.Length, limit)):
        el = found.GetElement(i)
        name = (el.CurrentName or "").strip()
        if name and not el.CurrentIsOffscreen:
            out.append(Control(name, _APP_KINDS.get(el.CurrentControlType, "control"), False, el, enabled=bool(el.CurrentIsEnabled)))
    return out

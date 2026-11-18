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
}
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
        found = page.FindAll(UIA.TreeScope_Descendants, cond) if page is not None else None
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


def _is_filler(word: str) -> bool:
    return word in _FILLER or any(len(f) >= 3 and difflib.SequenceMatcher(None, word, f).ratio() >= 0.8 for f in _FILLER)


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


def find(items: list[Control], query: str) -> list[Control]:
    """Controls that fit `query`, best first (visible ones before off-screen ones at equal quality)."""
    scored = [(match_score(query, c.name), c) for c in items]
    good = [(s, c) for s, c in scored if s > 0]
    good.sort(key=lambda sc: (-sc[0], sc[1].offscreen))
    return [c for _, c in good]


def click(control: Control, mouse: bool = False) -> str:
    """Press a control. Returns how it was done: "invoke" (the accessible way) or "mouse" (a real click, which moves the
    pointer: used when asked to, or when the control cannot be invoked)."""
    el = control.element
    try:
        el.GetCurrentPattern(UIA.UIA_ScrollItemPatternId).QueryInterface(UIA.IUIAutomationScrollItemPattern).ScrollIntoView()
    except Exception:
        pass  # not scrollable, or already in view
    if not mouse:
        try:
            el.GetCurrentPattern(UIA.UIA_InvokePatternId).QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()
            return "invoke"
        except Exception:
            pass
    import ctypes
    rect = el.CurrentBoundingRectangle
    x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    ctypes.windll.user32.SetCursorPos(x, y)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # left down
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # left up
    return "mouse"


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

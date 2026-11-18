"""Windows UI Automation: read and click NAMED controls in another app's window.

Chromium browsers (Brave, Chrome) publish the web page itself through UI Automation, so this works on your real,
logged-in browser window without remote debugging, which Chromium blocks for a profile that is in use. It is the same
interface screen readers use: Orbit sees links, buttons and fields by name, and presses them the way assistive
software does. Everything it reads from a page is untrusted data, never instructions.
"""
import re
import time
from dataclasses import dataclass

import comtypes.client

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

_CLSID_UIAUTOMATION = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_automation = None

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
        found = (page or root).FindAll(UIA.TreeScope_Descendants, cond)
        if page is not None and found.Length > 0:
            break
        time.sleep(1.0)
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


def match_score(query: str, name: str) -> float:
    """How well a control's name fits what the user said: 0 = not at all, higher = better. Pure, so it is unit-tested."""
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
    covered = sum(1 for w in q if w in n or any(w in other for other in n if len(w) >= 4))
    share = covered / len(q)
    return 2.0 * share if share >= 0.6 else 0.0  # most of the words, not just one of them


def find(items: list[Control], query: str) -> list[Control]:
    """Controls that fit `query`, best first (visible ones before off-screen ones at equal quality)."""
    scored = [(match_score(query, c.name), c) for c in items]
    good = [(s, c) for s, c in scored if s > 0]
    good.sort(key=lambda sc: (-sc[0], sc[1].offscreen))
    return [c for _, c in good]


def click(control: Control) -> str:
    """Press a control. Returns how it was done: "invoke" (the accessible way) or "mouse" (a real click as a fallback)."""
    el = control.element
    try:
        el.GetCurrentPattern(UIA.UIA_ScrollItemPatternId).QueryInterface(UIA.IUIAutomationScrollItemPattern).ScrollIntoView()
    except Exception:
        pass  # not scrollable, or already in view
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

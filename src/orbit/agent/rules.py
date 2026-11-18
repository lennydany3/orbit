"""Fast rules: common commands matched by regex, no model call (instant and 100% predictable).

Each rule maps a sentence to (tool name, args). The agent only uses a match if that tool is registered,
otherwise the request goes to the LLM. Later steps add rules like "open <app>", "volume <n>", "pause".
"""
import difflib
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import quote_plus

from .. import config


@dataclass
class RuleMatch:
    rule: str
    tool: str
    args: dict
    then: list = field(default_factory=list)  # more (tool, args) calls to run after this one succeeds
    soft: bool = False  # a guess from context: if it fails, fall back to the normal path instead of reporting failure


Builder = Callable[[re.Match], tuple]  # (tool, args) or (tool, args, then)

_THE = r"(?:the )?"
_MUSIC = r"(?: ?(?:the )?(?:music|song|track|playback|audio|video))?"

SITES = {
    "github": "https://github.com", "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "gmail": "https://mail.google.com", "wikipedia": "https://www.wikipedia.org", "reddit": "https://www.reddit.com",
    "twitter": "https://x.com", "linkedin": "https://www.linkedin.com",
}
_SITE_NAMES = "|".join(SITES)


def _site_url(site: str, mine: bool) -> str:
    """"open github" = the site; "open my github" = your own profile page."""
    if site == "github" and mine and config.GITHUB_USER:
        return f"https://github.com/{config.GITHUB_USER}"
    return SITES[site]
_VERB = r"(?:please )?(?:open|go to|visit|launch)"


# --- Spotify devices: "switch playback to echo with 30% volume" = move playback, then set that device's volume ---
_DEV = r"(?P<dev>(?!(?:again|it|that|this|next|previous|prev|something|music|the music)\b)(?![\w .'-]*\b(?:monitor|screen|display|half|side)\b)[\w .'-]+?)"
_DEV_TAIL = (
    r"(?:\s+(?:with|at|and)\s+(?:the\s+)?(?:volume\s+(?:at|to|of)\s+)?(?P<n>\d{1,3})\s*(?:%|percent)?(?:\s+volume)?(?:\s+on it)?)?"
    r"(?:\s+instead of [\w .'-]+)?"
)


def _transfer(m: re.Match) -> tuple:
    args = {"device": m["dev"].strip()}
    if m["n"]:
        args["volume"] = int(m["n"])
    return ("api_spotify_transfer", args)


# Always on: the sentence names the audio ("playback", "music output", ...).
_TRANSFER_EXPLICIT = re.compile(
    rf"^(?:transfer|move|switch|send|change|cast|play)\s+(?:the\s+)?(?:playback|music|spotify|sound|audio|songs?|output)"
    rf"(?:\s+(?:output|playback))?\s+(?:to|on)\s+(?:the\s+)?{_DEV}{_DEV_TAIL}$", re.I)
# Only right after a Spotify request, because "switch to chrome" normally means a window.
_TRANSFER_CONTEXT = re.compile(
    rf"^(?:switch to|move to|go to|change to|try|use|play on|play it on|send it to|put it on|cast to)\s+(?:the\s+)?{_DEV}{_DEV_TAIL}$", re.I)

# "move brave to my secondary monitor" / "send spotify to the other screen" / "put chrome on monitor 2"
_MON = (r"(?:primary|main|secondary|second|other|another|next|external|first|2nd|1st|\d|(?:left|right)\s+(?:monitor|screen|display)"
        r"|(?:monitor|screen|display)\s+\d)(?:\s+(?:monitor|screen|display))?")
_ACTIONS = {"max": "maximize", "min": "minimize", "res": "restore"}
_MOVE_TO_MONITOR = re.compile(
    rf"^(?:move|put|send|shift|drag|take|bring)\s+(?:the\s+|my\s+)?(?!(?:it|that|this|them)\b)(?P<t>[\w .+&'-]+?)(?:\s+(?:app|window))?\s+"
    rf"(?:to|onto|on|over to)\s+(?:the\s+|my\s+)?(?P<mon>{_MON})$", re.I)
_SNAP = re.compile(
    r"^(?:snap|put|move|send|dock)\s+(?:the\s+|my\s+)?(?!(?:it|that|this|them)\b)(?P<t>[\w .+&'-]+?)(?:\s+(?:app|window))?\s+(?:to|on|onto|at)\s+(?:the\s+)?"
    r"(?P<side>left|right|top|bottom)(?: half| side| of the screen)?$", re.I)

# "click go in speedtest" / "in speedtest click go": the app is named, so it is a window, not a web page (unless it is a browser)
_BROWSER_WORDS = ("brave", "chrome", "browser", "youtube", "github", "firefox", "edge", "page", "site", "tab")
_CLICK_IN_APP = re.compile(
    r"^(?:click|press|tap|hit|push)\s+(?:on\s+)?(?:the\s+)?(?P<t>.+?)(?: button)?\s+(?:in|on|inside|from)\s+(?:the\s+)?(?P<app>[\w .'-]+?)(?: app| window)?$", re.I)
_IN_APP_CLICK = re.compile(
    r"^(?:in|on|inside)\s+(?:the\s+)?(?P<app>[\w .'-]+?)(?: app| window)?,?\s+(?:click|press|tap|hit|push)\s+(?:on\s+)?(?:the\s+)?(?P<t>.+?)(?: button)?$", re.I)


def _click_in(text: str, app: str) -> tuple:
    app, text = app.strip(), text.strip()
    if any(w in app.lower() for w in _BROWSER_WORDS):
        return ("browser_click", {"text": text})
    return ("os_click", {"text": text, "window": app.title()})


# (rule name, pattern, builder). Patterns are matched against the whole sentence, case-insensitively.
# Anything with extra clauses ("open X and play Y") deliberately does not match and goes to the model.
RULES: list[tuple[str, re.Pattern, Builder]] = [
    # --- Spotify (API layer; only used if connected) ---
    ("spotify-liked", re.compile(r"^play (?:my )?(?:favou?rites?|favs?|liked|saved)(?: (?:music|songs?|tracks?|stuff))?(?: on spotify)?$", re.I),
     lambda m: ("api_spotify_play", {"query": "liked songs"})),
    ("spotify-play", re.compile(r"^(?:play|put on)\s+(?P<q>.+?)\s+on spotify$", re.I),
     lambda m: ("api_spotify_play", {"query": m["q"]})),
    ("spotify-transfer", re.compile(r"^(?:move|transfer|switch|send)\s+(?:the )?(?:playback|music|spotify)\s+to\s+(?:this|my)\s+(?:device|pc|computer|laptop)$", re.I),
     lambda m: ("api_spotify_transfer", {"device": "this"})),
    ("spotify-transfer-device", _TRANSFER_EXPLICIT, _transfer),
    # --- browser layer (before open-app: "open github" is a website, not an installed app) ---
    ("open-site", re.compile(rf"^{_VERB}\s+(?:(?P<my>my )|the )?(?P<site>{_SITE_NAMES})(?: (?:website|site|page|account|profile))?$", re.I),
     lambda m: ("browser_open", {"url": _site_url(m["site"].lower(), bool(m["my"]))})),
    ("open-url", re.compile(rf"^{_VERB}\s+(?P<url>(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?)$", re.I),
     lambda m: ("browser_open", {"url": m["url"] if "://" in m["url"] else "https://" + m["url"]})),
    ("click-in-app", _CLICK_IN_APP, lambda m: _click_in(m["t"], m["app"])),
    ("in-app-click", _IN_APP_CLICK, lambda m: _click_in(m["t"], m["app"])),
    ("click-first-video", re.compile(r"^(?:click|press|tap|select|hit|open|play)\s+(?:on\s+)?(?:the\s+)?(?:first|top|1st)\s+(?:video|result|one)$", re.I),
     lambda m: ("browser_youtube_play", {"query": ""})),
    ("click", re.compile(r"^(?:click|press|tap|select|hit)\s+(?:on\s+)?(?:the\s+)?(?!(?:it|that|this)$)(?P<text>.+?)(?: (?:button|link|video))?$", re.I),
     lambda m: ("browser_click", {"text": m["text"].strip()})),
    # --- browser history and home pages: "go back" is the browser's Back button, not the previous song ---
    ("browser-back", re.compile(r"^(?:go |navigate |take me |step )?back(?: (?:a page|one page|to the previous page|to the last page|to the page before))?$", re.I),
     lambda m: ("browser_navigate", {"action": "back"})),
    ("browser-forward", re.compile(r"^(?:go |navigate )?forward(?: (?:a page|one page))?$", re.I),
     lambda m: ("browser_navigate", {"action": "forward"})),
    ("browser-reload", re.compile(r"^(?:reload|refresh)(?: (?:the |this )?(?:page|tab|site))?$", re.I),
     lambda m: ("browser_navigate", {"action": "reload"})),
    ("site-home", re.compile(rf"^(?:go |navigate |take me )?(?:back )?(?:to )?(?:the )?(?:(?P<a>{_SITE_NAMES}) (?:home ?page|home|main page)|home ?page (?:of|on) (?:the )?(?P<b>{_SITE_NAMES})|(?P<c>{_SITE_NAMES}) home)$", re.I),
     lambda m: ("browser_go_to", {"url": SITES[(m["a"] or m["b"] or m["c"]).lower()]})),
    ("home-page", re.compile(r"^(?:go |navigate |take me )?(?:back )?(?:to )?(?:the )?(?:home ?page|home|main page)(?: of (?:this|the) (?:site|page|website))?$", re.I),
     lambda m: ("browser_navigate", {"action": "home"})),
    ("youtube-first-video", re.compile(r"^(?:play|watch|start)\s+(?:the |a |any |some |one )?(?:first |top )?(?:videos?|something)(?: on youtube)?$", re.I),
     lambda m: ("browser_youtube_play", {"query": ""})),
    ("youtube-play", re.compile(r"^(?:play|watch)\s+(?P<q>.+?)\s+on youtube$", re.I),
     lambda m: ("browser_youtube_play", {"query": m["q"]})),
    ("youtube-search", re.compile(r"^search\s+(?:for\s+)?(?P<q>.+?)\s+on youtube$", re.I),
     lambda m: ("browser_open", {"url": "https://www.youtube.com/results?search_query=" + quote_plus(m["q"])})),
    ("fullscreen-off", re.compile(r"^(?:exit|leave|close) full ?screen$", re.I),
     lambda m: ("browser_fullscreen", {"on": False})),
    ("fullscreen-on", re.compile(r"^(?:go |make (?:it|the video) )?full ?screen(?: (?:it|the video))?$", re.I),
     lambda m: ("browser_fullscreen", {"on": True})),
    ("web-search", re.compile(r"^(?:search|google)\s+(?:for\s+)?(?!.*\b(?:and|then)\b)(?P<q>.+)$", re.I),
     lambda m: ("browser_search", {"query": m["q"]})),
    # --- OS layer ---
    ("open-app", re.compile(r"^(?:please )?(?:open|launch|start)\s+(?:the |my )?(?!.*\b(?:and|then)\b)(?!(?:it|that|this|them|again)$)(?P<name>[\w .+&'-]+?)(?: app)?$", re.I),
     lambda m: ("os_open_app", {"name": m["name"].strip().title()})),
    ("volume-set", re.compile(rf"^(?:turn |set |put )?{_THE}volume (?:to |at )?(?P<n>\d{{1,3}})\s*%?$", re.I),
     lambda m: ("os_set_volume", {"level": int(m["n"])})),
    ("volume-step", re.compile(rf"^(?:turn )?{_THE}volume (?P<dir>up|down)$|^turn it (?P<dir2>up|down)$", re.I),
     lambda m: ("os_change_volume", {"delta": 10 if (m["dir"] or m["dir2"]).lower() == "up" else -10})),
    # --- browser tabs (before close-window: "close all other youtube tabs" is not a window called that) ---
    ("close-other-tabs", re.compile(r"^close\s+(?:all\s+)?(?:the\s+)?(?:other|extra)\s+(?:(?P<m>[\w .'-]+?)\s+)?tabs?(?: except (?:this|the current) one)?$|^close\s+(?:all\s+)?tabs\s+except\s+(?:this|the current)(?: one| tab)?$", re.I),
     lambda m: ("browser_close_tabs", {"scope": "others", "match": (m["m"] or "").strip()})),
    ("close-matching-tabs", re.compile(r"^close\s+(?:all\s+)?(?:the\s+)?(?P<m>(?!(?:this|the|current|other|extra|my|all)\b)[\w .'-]+?)\s+tabs$", re.I),
     lambda m: ("browser_close_tabs", {"scope": "matching", "match": m["m"].strip()})),
    ("close-this-tab", re.compile(r"^close\s+(?:this|the current|current|the)\s+tab$|^close\s+tab$", re.I),
     lambda m: ("browser_close_tabs", {"scope": "this"})),
    ("switch-tab", re.compile(r"^(?:switch|go|jump|change)\s+to\s+(?:the\s+)?(?P<t>[\w .'-]+?)\s+tab$", re.I),
     lambda m: ("browser_switch_tab", {"text": m["t"].strip()})),
    ("list-tabs", re.compile(r"^(?:list|show|what are)\s+(?:me\s+)?(?:all\s+)?(?:my\s+|the\s+)?(?:open\s+)?tabs$", re.I),
     lambda m: ("browser_tabs", {})),
    ("move-window", _MOVE_TO_MONITOR, lambda m: ("os_move_window", {"title": m["t"].strip().title(), "monitor": m["mon"].strip().lower()})),
    ("arrange-state", re.compile(r"^(?P<act>maximi[sz]e|minimi[sz]e|restore)\s+(?:the\s+|my\s+)?(?!(?:it|that|this|them|window|app)$)(?P<t>[\w .+&'-]+?)(?: (?:app|window))?$", re.I),
     lambda m: ("os_arrange_window", {"title": m["t"].strip().title(), "action": _ACTIONS[m["act"][:3].lower()]})),
    ("arrange-snap", _SNAP, lambda m: ("os_arrange_window", {"title": m["t"].strip().title(), "action": m["side"].lower()})),
    ("close-window", re.compile(r"^(?:please )?close\s+(?:the )?(?!(?:it|that|this|them|window|app|tab|both|all|everything|everything i opened|them all|all of them|both of them)$)(?P<t>[\w .+&'-]+?)(?: (?:app|window))?$", re.I),
     lambda m: ("os_close_window", {"title": m["t"].strip().title()})),
    ("mute", re.compile(r"^(?P<un>un)?mute(?: the (?:sound|volume|audio))?$", re.I),
     lambda m: ("os_set_mute", {"muted": not m["un"]})),
    ("pause", re.compile(rf"^(?:pause|stop){_MUSIC}$", re.I),
     lambda m: ("os_media_key", {"key": "pause"})),
    ("play", re.compile(rf"^(?:play|resume|continue){_MUSIC}$", re.I),
     lambda m: ("os_media_key", {"key": "play"})),
    ("next", re.compile(r"^(?:play )?(?:next|skip)(?: (?:the )?(?:song|track))?$", re.I),
     lambda m: ("os_media_key", {"key": "next"})),
    ("previous", re.compile(r"^(?:play )?(?:previous|prev)(?: (?:the )?(?:song|track))?$", re.I),
     lambda m: ("os_media_key", {"key": "prev"})),
    # --- test tools (Step 1) ---
    ("echo", re.compile(r"^echo\s+(?P<text>.+)$", re.I),
     lambda m: ("fake_echo", {"text": m["text"]})),
    ("test-retry", re.compile(r"^test retry\s+(?P<text>.+)$", re.I),
     lambda m: ("fake_echo", {"text": m["text"], "verify_mode": "fail_once"})),
    ("test-fallback", re.compile(r"^test fallback\s+(?P<text>.+)$", re.I),
     lambda m: ("fake_echo", {"text": m["text"], "verify_mode": "fail_always"})),
    ("danger", re.compile(r"^danger\s+(?P<text>.+)$", re.I),
     lambda m: ("fake_danger", {"text": m["text"]})),
]


# Polite filler a person puts around a command: "ok can you close the speed test please?"
_FILLER_HEAD = re.compile(r"^(?:(?:ok(?:ay)?|yo+|yeah|hey|hi|bro|so|now|then|just|please|pls|can you|could you|would you|will you|can u)\b,?\s+)+", re.I)
_IN_BROWSER = r"(?:in|on|with|using)\s+(?:the\s+)?(?:brave|chrome|google chrome|my browser|the browser|browser)"
_ON_PAGE = r"(?:in|on)\s+(?:the\s+)?(?:youtube|github|google|this page|the page|page|this site|the site)"
_BROWSER_HEAD = re.compile(rf"^(?:{_IN_BROWSER}|{_ON_PAGE}),?\s+(?=(?:click|press|tap|select)\b)|^{_IN_BROWSER},?\s+", re.I)  # "in brave open my github", "in youtube click X"
_BROWSER_TAIL = re.compile(rf"\s+{_IN_BROWSER}$", re.I)         # "open my github in brave"
_FILLER_TAIL = re.compile(r"(?:,?\s*(?:please|pls|for me|thanks|thank you))?[\s?.!]*$", re.I)
_PRONOUN_MOVE = re.compile(rf"^(?:move|put|send|shift|bring)\s+(?:it|that|this)\s+(?:to|onto|on|over to)\s+(?:the\s+|my\s+)?(?P<mon>{_MON})$", re.I)
_PRONOUN_ARRANGE = re.compile(r"^(?P<act>maximi[sz]e|minimi[sz]e|restore)\s+(?:it|that|this)$", re.I)
_PRONOUN_CLOSE = re.compile(r"^close (?:it|that|this)$", re.I)
_PLURAL_CLOSE = re.compile(r"^close (?:both|them|them all|all|all of them|both of them|everything)(?: i opened)?$", re.I)
_PRONOUN_OPEN = re.compile(r"^(?:(?:re)?open|launch|start) (?:it|that|this|them)(?: again)?$|^(?:re)?open again$", re.I)


# "now brave is in primary monitor move it to secondary monitor": the leading statement is background, the command follows
_STATEMENT_HEAD = re.compile(r"^(?:now|ok|okay|so|and|well)?\s*[\w .'-]+?\s+(?:is|are)\s+(?:now\s+)?(?:in|on|at)\s+[\w .'-]+?,?\s+(?=(?:move|put|send|shift|maximi[sz]e|minimi[sz]e|snap|close|open)\b)", re.I)


_VERBS_FOR_TYPOS = ("click", "open", "close", "play", "pause", "move", "maximize", "minimize", "switch", "search", "press", "restore", "reload", "refresh", "launch")


def _fix_first_word(text: str) -> str:
    """"clcik the ..." -> "click the ...": a mistyped first word is corrected when exactly one known verb is close."""
    words = text.split(None, 1)
    if not words or words[0].lower() in COMMAND_WORDS:
        return text
    close = difflib.get_close_matches(words[0].lower(), _VERBS_FOR_TYPOS, n=2, cutoff=0.8)
    return (close[0] + (" " + words[1] if len(words) > 1 else "")) if len(close) == 1 else text


def normalize(text: str) -> str:
    """Drop polite filler and "in brave": Orbit has one browser (set with --browser), so the words add nothing."""
    text = _FILLER_HEAD.sub("", text.strip())
    text = _STATEMENT_HEAD.sub("", text)
    text = _BROWSER_HEAD.sub("", _FILLER_HEAD.sub("", text))
    text = _FILLER_TAIL.sub("", text).strip()
    return _BROWSER_TAIL.sub("", text).strip()


_SPLIT = re.compile(r"\s*,\s*(?:and\s+|then\s+)?|\s+(?:and\s+then|then|and|&)\s+", re.I)


COMMAND_WORDS = frozenset(
    "open launch start close quit exit play pause resume stop next skip previous prev mute unmute set turn volume "
    "switch move transfer send cast change search google go visit show list tell what whats what's who how "
    "fullscreen full minimize maximize put make increase decrease raise lower find watch look try use say read check bring".split())
_SHARE_VERB = frozenset({"open", "close", "launch", "start", "quit"})  # "close the clock and the calculator"
_NOT_A_NAME = frozenset("do it that this them tell show what how why please also maybe then".split())


def is_command(part: str) -> bool:
    """Does this piece of a sentence start like a request? (Used to decide whether "A and B" is two requests.)"""
    first = part.split(None, 1)[0].lower() if part.split() else ""
    return first in COMMAND_WORDS


def split_commands(text: str) -> list[str]:
    """"open spotify and play my fav music" -> two commands. "close the clock and the calculator" -> two closes."""
    parts = [p for p in (normalize(part) for part in _SPLIT.split(normalize(text))) if p]
    out: list[str] = []
    for part in parts:
        words = part.lower().split()
        looks_like_a_name = words[0] not in _NOT_A_NAME and (words[0] in ("the", "my", "a", "an") or len(words) <= 2)
        if out and not is_command(part) and looks_like_a_name:
            verb = out[-1].split(None, 1)[0].lower()
            if verb in _SHARE_VERB:
                part = f"{verb} {part}"
        out.append(part)
    return out


def match(text: str, subject: str | None = None, closed: str | None = None, spotify_active: bool = False,
          subjects: list[str] | None = None) -> RuleMatch | None:
    """`subject` = the app/window Orbit last opened or focused ("close it"); `closed` = the last one it closed ("open it");
    `spotify_active` = the conversation is about Spotify, so "switch to echo" means a Spotify device;
    `subjects` = everything Orbit opened lately ("close both")."""
    text = normalize(text)
    found = _match(text, subject, closed, spotify_active, subjects)
    if found is None:  # a second chance for a mistyped first word ("clcik the ..."), only when nothing else fits
        fixed = _fix_first_word(text)
        if fixed != text:
            found = _match(fixed, subject, closed, spotify_active, subjects)
    return found


def _match(text: str, subject, closed, spotify_active, subjects) -> RuleMatch | None:
    if subject and _PRONOUN_CLOSE.match(text):
        return RuleMatch(rule="close-it", tool="os_close_window", args={"title": subject})
    if subjects and _PLURAL_CLOSE.match(text):
        first, *rest = subjects
        return RuleMatch(rule="close-them", tool="os_close_window", args={"title": first},
                         then=[("os_close_window", {"title": name}) for name in rest])
    if subject:
        m = _PRONOUN_MOVE.match(text)
        if m:
            return RuleMatch(rule="move-it", tool="os_move_window", args={"title": subject, "monitor": m["mon"].strip().lower()})
        m = _PRONOUN_ARRANGE.match(text)
        if m:
            return RuleMatch(rule="arrange-it", tool="os_arrange_window",
                             args={"title": subject, "action": _ACTIONS[m["act"][:3].lower()]})
    if (subject or closed) and _PRONOUN_OPEN.match(text):
        return RuleMatch(rule="open-it", tool="os_open_app", args={"name": subject or closed})
    if spotify_active:
        m = _TRANSFER_CONTEXT.match(text)
        if m:
            tool, args = _transfer(m)
            return RuleMatch(rule="spotify-transfer-context", tool=tool, args=args, soft=True)
    for name, pattern, build in RULES:
        m = pattern.match(text)
        if m:
            built = build(m)
            return RuleMatch(rule=name, tool=built[0], args=built[1], then=built[2] if len(built) > 2 else [])
    return None

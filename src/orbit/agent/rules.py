"""Fast rules: common commands matched by regex, no model call (instant and 100% predictable).

Each rule maps a sentence to (tool name, args). The agent only uses a match if that tool is registered,
otherwise the request goes to the LLM. Later steps add rules like "open <app>", "volume <n>", "pause".
"""
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
_DEV = r"(?P<dev>(?!(?:again|it|that|this|next|previous|prev|something|music|the music)\b)[\w .'-]+?)"
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
    ("previous", re.compile(r"^(?:play )?(?:previous|prev|go back)(?: (?:the )?(?:song|track))?$", re.I),
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
_BROWSER_HEAD = re.compile(rf"^{_IN_BROWSER},?\s+", re.I)        # "in brave open my github"
_BROWSER_TAIL = re.compile(rf"\s+{_IN_BROWSER}$", re.I)         # "open my github in brave"
_FILLER_TAIL = re.compile(r"(?:,?\s*(?:please|pls|for me|thanks|thank you))?[\s?.!]*$", re.I)
_PRONOUN_CLOSE = re.compile(r"^close (?:it|that|this)$", re.I)
_PLURAL_CLOSE = re.compile(r"^close (?:both|them|them all|all|all of them|both of them|everything)(?: i opened)?$", re.I)
_PRONOUN_OPEN = re.compile(r"^(?:(?:re)?open|launch|start) (?:it|that|this|them)(?: again)?$|^(?:re)?open again$", re.I)


def normalize(text: str) -> str:
    """Drop polite filler and "in brave": Orbit has one browser (set with --browser), so the words add nothing."""
    text = _FILLER_HEAD.sub("", text.strip())
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
    if subject and _PRONOUN_CLOSE.match(text):
        return RuleMatch(rule="close-it", tool="os_close_window", args={"title": subject})
    if subjects and _PLURAL_CLOSE.match(text):
        first, *rest = subjects
        return RuleMatch(rule="close-them", tool="os_close_window", args={"title": first},
                         then=[("os_close_window", {"title": name}) for name in rest])
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

"""Pictures on buttons: "click the search icon", "the magnifying glass", "the hamburger menu", "the three dots", "the X".

A picture has no words, so how does anyone find it? Two ways, and this module knows both:

  1. Almost every icon has a NAME that the app tells Windows (and web pages tell the browser): the magnifying glass is a
     button called "Search", the hamburger is "Menu", the three dots are "More options", the cog is "Settings". So a picture
     word is turned into the names it usually has, and those are looked up among the real controls.
  2. When an app publishes no names at all (a custom-drawn one), the screen is read with OCR, and many icons ARE characters
     that the OCR engine returns as text: x, +, a heart, a star, three dots, a play triangle. Those glyphs are looked up too.

Nothing here guesses about pixels: a symbol that is neither named nor readable is reported as not found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Symbol:
    key: str
    names: tuple[str, ...]     # what the control is usually called, best first
    glyphs: tuple[str, ...]    # what OCR may read it as


# key: (words for the picture itself, words that only count next to "icon"/"symbol"/"button", names, glyphs)
_TABLE: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "search": (("magnifier", "magnifying glass", "magnifying", "magnify"), ("search", "find"),
               ("search", "google search", "search button", "submit search", "find"), ("\U0001F50D", "\U0001F50E")),
    "voice": (("microphone", "mic"), ("voice", "voice search"),
              ("search by voice", "voice search", "microphone", "dictate", "speak", "mic"), ("\U0001F3A4", "\U0001F399")),
    "camera": (("camera", "lens", "google lens"), ("image search",),
               ("search by image", "google lens", "lens", "camera", "take photo", "take a photo"), ("\U0001F4F7",)),
    "menu": (("hamburger", "three lines", "3 lines", "three bars", "burger"), ("menu", "navigation"),
             ("menu", "main menu", "navigation menu", "open menu", "guide", "open navigation", "toggle menu", "sidebar", "navigation drawer"),
             ("≡", "☰", "三")),
    "more": (("three dots", "3 dots", "dots", "ellipsis", "kebab", "meatballs", "overflow"), ("more", "options"),
             ("more options", "more actions", "more", "options", "more settings", "show more", "overflow menu", "menu"),
             ("⋮", "…", "...", "⋯", "︙")),
    "settings": (("gear", "cog", "cogwheel", "cog wheel", "spanner", "wrench"), ("settings", "preferences", "options"),
                 ("settings", "options", "preferences", "configure", "quick settings", "customize"), ("⚙",)),
    "close": (("cross",), ("x", "close", "dismiss", "cancel", "exit"),
              ("close", "dismiss", "cancel", "clear", "exit", "remove", "close tab"), ("x", "×", "✕", "✖", "✗")),
    "add": (("plus sign",), ("plus", "add", "new", "create", "plus"),
            ("add", "new", "create", "plus", "new tab", "add new", "new chat"), ("+", "＋")),
    "minus": (("minus sign",), ("minus", "subtract"), ("minus", "zoom out", "decrease", "reduce", "remove"), ("−",)),   # (not a plain "-": it is everywhere in text)
    "back": (("back arrow", "left arrow", "arrow left", "backwards arrow"), ("back", "previous"),
             ("back", "go back", "previous", "navigate back", "return", "back to"), ("←", "‹", "◀", "⬅")),
    "forward": (("forward arrow", "right arrow", "arrow right"), ("forward", "next"),
                ("forward", "go forward", "next", "continue", "navigate forward"), ("→", "›", "▶", "➡")),
    "home": (("house",), ("home",), ("home", "home page", "go home", "start"), ("⌂", "\U0001F3E0")),
    "like": (("heart", "thumbs up", "thumb up", "thumbs-up"), ("like", "love", "favorite", "favourite"),
             ("like", "favorite", "favourite", "love", "heart", "add to favorites", "thumbs up", "i like this"),
             ("♥", "♡", "❤", "\U0001F44D")),
    "star": (("star",), ("bookmark", "rate"), ("bookmark", "star", "favorite", "rate", "bookmark this tab", "add bookmark"), ("★", "☆", "⭐")),
    "bell": (("bell",), ("notifications", "alerts"), ("notifications", "notification", "alerts", "reminders"), ("\U0001F514",)),
    "user": (("avatar", "profile picture", "person", "silhouette"), ("profile", "account", "user"),
             ("account", "profile", "your profile", "google account", "user", "avatar", "sign in", "me"), ("\U0001F464",)),
    "share": (("share",), ("share",), ("share", "send", "share this"), ()),
    "download": (("download arrow",), ("download", "save"), ("download", "save", "save as"), ("↓", "⬇")),
    "refresh": (("refresh", "reload", "circular arrow", "circle arrow"), ("refresh", "reload"),
                ("reload", "refresh", "reload this page", "sync"), ("↻", "⟳", "⟲", "↺")),
    "play": (("play", "play triangle", "triangle"), ("play",), ("play", "play (k)", "resume", "play video"), ("▶", "►", "⏵")),
    "pause": (("pause",), ("pause",), ("pause", "pause (k)"), ("⏸", "‖", "∥")),
    "trash": (("trash", "trash can", "bin", "dustbin", "recycle bin", "garbage"), ("delete", "trash"),
              ("delete", "remove", "trash", "bin", "discard", "move to trash"), ("\U0001F5D1",)),
    "edit": (("pencil", "pen"), ("edit",), ("edit", "modify", "rename", "compose", "write"), ("✎", "✏")),
    "copy": (("copy",), ("copy", "duplicate"), ("copy", "duplicate", "copy link"), ()),
    "fullscreen": (("fullscreen", "full screen", "expand"), ("fullscreen", "full screen", "expand"),
                   ("full screen", "fullscreen", "enter full screen", "expand", "maximize"), ("⛶",)),
    "sound": (("speaker", "volume", "sound"), ("mute", "volume", "sound"), ("mute", "unmute", "volume", "sound", "speaker"), ("\U0001F50A", "\U0001F507")),
    "info": (("information",), ("info", "about"), ("info", "information", "about", "details", "view site information"), ("ⓘ", "ℹ")),
    "help": (("question mark",), ("help", "?"), ("help", "support", "get help", "help center"), ()),
    "filter": (("funnel",), ("filter", "filters"), ("filter", "filters", "search tools", "tools", "refine"), ("⧖",)),
}

ICON_WORDS = frozenset({"icon", "icons", "symbol", "symbols", "glyph", "glyphs", "logo", "button", "buttons", "picture",
                        "image", "pictogram", "emoji"})
# words that carry no meaning when a person points at a picture
_NOISE = frozenset({"the", "a", "an", "on", "of", "that", "this", "my", "please", "one", "with", "in", "at", "top", "right", "left",
                    "corner", "little", "small", "tiny", "big", "side", "next", "to", "bar", "box", "field", "page", "is"})


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+|[^\w\s]", text.lower())


def _index() -> tuple[dict[str, str], dict[str, str]]:
    picture, plain = {}, {}
    for key, (picture_words, plain_words, _names, _glyphs) in _TABLE.items():
        for word in picture_words:
            picture[word] = key
        for word in plain_words:
            plain.setdefault(word, key)
    return picture, plain


_PICTURE, _PLAIN = _index()


def identify(query: str) -> Symbol | None:
    """The symbol a request is about ("the search icon", "magnifying glass", "three dots", "x button"), or None when the
    request is about a named control ("click sign in"). Picture words always count; ordinary words ("search", "menu", "x")
    count only next to "icon", "symbol", "glyph" or "button", so "click search" still means the control called Search."""
    tokens = _tokens(query)
    has_icon_word = any(t in ICON_WORDS for t in tokens)
    core = [t for t in tokens if t not in ICON_WORDS and t not in _NOISE]
    if not core:
        return None
    phrase = " ".join(core)
    key = _PICTURE.get(phrase) or (_PLAIN.get(phrase) if has_icon_word else None)
    if key is None and len(core) > 1:                              # "search magnifying glass", "round plus"
        for size in range(len(core) - 1, 0, -1):
            for start in range(0, len(core) - size + 1):
                part = " ".join(core[start:start + size])
                key = _PICTURE.get(part) or (_PLAIN.get(part) if has_icon_word else None)
                if key:
                    break
            if key:
                break
    if key is None:
        return None
    _pw, _plw, names, glyphs = _TABLE[key]
    return Symbol(key, names, glyphs)


def wants_a_picture(query: str) -> bool:
    """Did the person say "icon", "symbol" or "button" (so a button is meant, not a text box or a link)?"""
    return any(t in ICON_WORDS for t in _tokens(query))


def wants_a_field(query: str) -> bool:
    """Did the person say "box", "bar", "field" or "input" (so a text field is meant)?"""
    return any(t in ("box", "bar", "field", "input", "textbox") for t in _tokens(query))

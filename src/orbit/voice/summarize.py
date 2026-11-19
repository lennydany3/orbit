"""Spoken replies are short: one or two sentences, no URLs, no code, no lists. The full reply stays in the CLI."""
import re

from . import config

_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:\\[^\s]*")


def spoken_summary(text: str, max_sentences: int = config.SPOKEN_MAX_SENTENCES, max_chars: int = config.SPOKEN_MAX_CHARS) -> str:
    """The reply as it should be SAID. Never empty."""
    original = (text or "").strip()
    if not original:
        return "Done."
    lines = original.splitlines()
    had_list = any(_LIST_ITEM.match(line) for line in lines)
    prose = " ".join(line for line in lines if not _LIST_ITEM.match(line))
    for pattern, replacement in ((re.compile(r"```.*?```", re.S), " "), (re.compile(r"`[^`]*`"), " "),
                                 (_URL, " "), (_WINDOWS_PATH, "a file")):
        prose = pattern.sub(replacement, prose)
    prose = re.sub(r"[*_#>~|]+", " ", prose)                 # markdown marks that would be read out loud
    prose = re.sub(r"\s+", " ", prose).strip()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", prose) if re.search(r"\w", s)]
    spoken = " ".join(sentences[:max_sentences]).strip()
    cut = len(sentences) > max_sentences
    if len(spoken) > max_chars:
        spoken = spoken[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-")
        spoken += "." if not spoken.endswith((".", "!", "?")) else ""
        cut = True
    if not spoken:
        return "I've put the details on screen."
    if had_list:
        spoken += " I've put the full list on screen."
    elif cut:
        spoken += " The rest is on screen."
    return spoken

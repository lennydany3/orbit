"""Facts about the PC right now, given to the model with each request so it can resolve loose words.

A 7B model does not "know" that "echo" means "Lenny's Echo Pop" or that "the notes window" is a Notepad window.
So before it answers we look up the real names (Spotify devices, open windows, what is playing, volume) with the
same read-only tools it could call, and put them in front of it. Only the facts the request could need are fetched,
so this stays small (the context window is tiny) and fast.
"""
import re
from typing import Callable

from ..tools.base import Registry

# (label, read-only tool, request words that make the fact relevant)
SOURCES: list[tuple[str, str, re.Pattern]] = [
    ("Spotify devices", "api_spotify_devices",
     re.compile(r"\b(spotify|echo|alexa|speaker|phone|device|devices|transfer|playback|output|cast|music|song|songs)\b", re.I)),
    ("Open windows", "os_list_windows",
     re.compile(r"\b(window|windows|close|focus|switch|front|minimi[sz]e|show|bring|app|apps|open|notepad|chrome|clock|calculator)\b", re.I)),
    ("Now playing", "os_media_status",
     re.compile(r"\b(playing|pause|resume|next|previous|track|song|music|spotify)\b", re.I)),
    ("Volume", "os_get_volume",
     re.compile(r"\b(volume|louder|quieter|loud|quiet|mute|unmute|sound|down|up)\b", re.I)),
]
_MAX_LINE_CHARS = 420
HEADER = ("Facts about this PC right now. When the user names something loosely (\"echo\", \"the clock\", \"my phone\"), "
          "work out which one they mean and use its exact name from here:")


def wanted_sources(text: str, previous: str = "") -> list[tuple[str, str]]:
    """Which facts this request could need: [(label, tool name)]."""
    both = f"{text} {previous}"
    return [(label, tool) for label, tool, pattern in SOURCES if pattern.search(both)]


def _compact(value: str) -> str:
    value = "; ".join(line.strip() for line in value.splitlines() if line.strip())
    return value if len(value) <= _MAX_LINE_CHARS else value[:_MAX_LINE_CHARS].rstrip("; ") + " ..."


def _windows_text(data) -> str:
    """Window titles only, shortened: the exe names and long titles would push the useful ones out of the budget."""
    titles = [line.rsplit(" (", 1)[0].strip()[:45] for line in str(data or "").splitlines() if line.strip()]
    return "; ".join(titles[:15])


def format_facts(facts: dict[str, str]) -> str:
    """{label: text} -> the block put in front of the model ('' if there is nothing to say)."""
    lines = [f"- {label}: {_compact(text)}" for label, text in facts.items() if text and text.strip()]
    return HEADER + "\n" + "\n".join(lines) if lines else ""


def gather(registry: Registry, text: str, previous: str = "") -> str:
    """Look up the relevant facts with the read-only tools. A tool that fails is simply left out."""
    facts: dict[str, str] = {}
    for label, tool_name in wanted_sources(text, previous):
        tool = registry.get_exposed(tool_name)
        if tool is None:
            continue
        try:
            result = tool.run(tool.params())
        except Exception:  # context is a nice-to-have: never let it break a request
            continue
        if result.ok:
            facts[label] = _windows_text(result.data) if tool_name == "os_list_windows" else (
                f"{result.summary}. {result.data}" if result.data else result.summary)
    return format_facts(facts)


FactsProvider = Callable[[str, str], str]

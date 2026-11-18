"""Fast rules: common commands matched by regex, no model call (instant and 100% predictable).

Each rule maps a sentence to (tool name, args). The agent only uses a match if that tool is registered,
otherwise the request goes to the LLM. Later steps add rules like "open <app>", "volume <n>", "pause".
"""
import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class RuleMatch:
    rule: str
    tool: str
    args: dict


Builder = Callable[[re.Match], tuple[str, dict]]

_THE = r"(?:the )?"
_MUSIC = r"(?: ?(?:the )?(?:music|song|track|playback|audio|video))?"

# (rule name, pattern, builder). Patterns are matched against the whole sentence, case-insensitively.
# Anything with extra clauses ("open X and play Y") deliberately does not match and goes to the model.
RULES: list[tuple[str, re.Pattern, Builder]] = [
    # --- OS layer ---
    ("open-app", re.compile(r"^(?:please )?(?:open|launch|start)\s+(?!.*\b(?:and|then)\b)(?P<name>[\w .+&'-]+?)(?: app)?$", re.I),
     lambda m: ("os_open_app", {"name": m["name"].strip().title()})),
    ("volume-set", re.compile(rf"^(?:turn |set |put )?{_THE}volume (?:to |at )?(?P<n>\d{{1,3}})\s*%?$", re.I),
     lambda m: ("os_set_volume", {"level": int(m["n"])})),
    ("volume-step", re.compile(rf"^(?:turn )?{_THE}volume (?P<dir>up|down)$|^turn it (?P<dir2>up|down)$", re.I),
     lambda m: ("os_change_volume", {"delta": 10 if (m["dir"] or m["dir2"]).lower() == "up" else -10})),
    ("mute", re.compile(r"^(?P<un>un)?mute(?: the (?:sound|volume|audio))?$", re.I),
     lambda m: ("os_set_mute", {"muted": not m["un"]})),
    ("pause", re.compile(rf"^(?:pause|stop){_MUSIC}$", re.I),
     lambda m: ("os_media_key", {"key": "pause"})),
    ("play", re.compile(rf"^(?:play|resume|continue){_MUSIC}$", re.I),
     lambda m: ("os_media_key", {"key": "play"})),
    ("next", re.compile(r"^(?:next|skip)(?: (?:the )?(?:song|track))?$", re.I),
     lambda m: ("os_media_key", {"key": "next"})),
    ("previous", re.compile(r"^(?:previous|prev|go back)(?: (?:the )?(?:song|track))?$", re.I),
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


def match(text: str) -> RuleMatch | None:
    text = text.strip()
    for name, pattern, build in RULES:
        m = pattern.match(text)
        if m:
            tool, args = build(m)
            return RuleMatch(rule=name, tool=tool, args=args)
    return None

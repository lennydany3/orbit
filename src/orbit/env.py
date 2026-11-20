"""Tiny `.env` loader (no extra dependency), so API keys live in a git-ignored file instead of in code or in your shell history.

Format: one `NAME=value` per line, `#` starts a comment, quotes around the value are optional. A variable that is already set
in the real environment always wins over the file, so `set GROQ_API_KEY=...` in a terminal still overrides `.env`.
"""
import os
from pathlib import Path


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:                      # an inline comment after an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        if name:
            values[name] = value
    return values


def load_env(path: Path) -> list[str]:
    """Copy the file's variables into os.environ (never overwriting existing ones). Returns the names that were set."""
    try:
        values = parse_env(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return []                                # no .env file: perfectly fine, the keys may come from the real environment
    set_now = []
    for name, value in values.items():
        if value and not os.environ.get(name):
            os.environ[name] = value
            set_now.append(name)
    return set_now

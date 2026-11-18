"""Small models sometimes WRITE a tool call as text instead of making one: `os_set_volume(level=60)` or a JSON blob.
This turns such text back into a real call, but only for tool names that exist and arguments that parse as plain values.
"""
import ast
import difflib
import json
import re

from ..llm.base import ToolCall

_WRAPPERS = re.compile(r"</?tool_call>|```[a-zA-Z]*", re.I)


def _from_json(text: str, names: set[str]) -> list[ToolCall]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    calls = []
    for item in data if isinstance(data, list) else [data]:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("tool") or item.get("function")
        args = item.get("arguments") or item.get("parameters") or item.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if name in names and isinstance(args, dict):
            calls.append(ToolCall(name, args))
    return calls


def _closest(name: str, names: set[str]) -> str | None:
    """The model sometimes garbles a tool name ("bralogy_page_info"): accept it only if exactly one real name is very close."""
    if name in names:
        return name
    close = difflib.get_close_matches(name, sorted(names), n=2, cutoff=0.75)
    return close[0] if len(close) == 1 else None


def _from_python_calls(text: str, names: set[str]) -> list[ToolCall]:
    calls = []
    for line in (ln.strip().rstrip(";") for ln in text.splitlines()):
        if not line:
            continue
        try:
            node = ast.parse(line, mode="eval").body
        except SyntaxError:
            return []  # prose mixed in: do not guess
        real = _closest(node.func.id, names) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) else None
        if not (real and not node.args):
            return []
        try:
            args = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords if kw.arg}
        except (ValueError, SyntaxError):
            return []
        calls.append(ToolCall(real, args))
    return calls


def parse_text_calls(content: str, tool_names: set[str]) -> list[ToolCall]:
    """Tool calls hidden in a reply's text ([] if the reply is anything but calls)."""
    text = _WRAPPERS.sub("", content or "").strip()
    if not text:
        return []
    return _from_json(text, tool_names) or _from_python_calls(text, tool_names)

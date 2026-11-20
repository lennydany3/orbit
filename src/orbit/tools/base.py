"""Tool format, results, and the registry.

A Tool is one small typed action. Tools that do the same job on different layers (api / os / browser)
share a `capability` name; the registry shows the model only the best layer for each capability,
and the router uses the others as fallbacks. That is how "most reliable layer first" is enforced in code.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel

Layer = Literal["api", "os", "browser", "vision"]
Risk = Literal["low", "confirm"]
LAYER_ORDER: tuple[str, ...] = ("api", "os", "browser", "vision")

_MAX_DATA_CHARS = 1500


@dataclass
class ToolResult:
    ok: bool
    summary: str = ""
    data: Any = None
    error: str = ""
    verified: bool = False
    retryable: bool = True  # False for permanent failures (app not found): retrying the same call is pointless
    not_found: bool = False  # the thing asked for (app, window, device) does not exist, as opposed to "it failed"
    context: dict = field(default_factory=dict)  # private notes from run() to verify(); never shown to the model

    @classmethod
    def success(cls, summary: str, data: Any = None) -> ToolResult:
        return cls(ok=True, summary=summary, data=data)

    @classmethod
    def failure(cls, error: str, retryable: bool = True, not_found: bool = False) -> ToolResult:
        return cls(ok=False, error=error, retryable=retryable, not_found=not_found)

    def for_model(self) -> str:
        """Text sent back to the LLM. Tool data may come from the screen or web, so it is fenced as untrusted."""
        if not self.ok:
            return f"ERROR: {self.error}"
        text = f"OK: {self.summary} [{'verified' if self.verified else 'NOT verified'}]"
        if self.data is not None:
            data = str(self.data)[:_MAX_DATA_CHARS]
            text += f"\n<untrusted_data>\n{data}\n</untrusted_data>"
        return text


@dataclass
class VerifyResult:
    ok: bool
    detail: str = ""


def read_only_verify(params: Any, result: ToolResult) -> VerifyResult:
    """Verifier for tools that only read state: there is no effect to confirm."""
    return VerifyResult(True, "read-only")


@dataclass
class Tool:
    name: str
    description: str                      # one short sentence
    params: type[BaseModel]               # JSON schema + validation
    layer: Layer
    risk: Risk
    run: Callable[[Any], ToolResult]      # receives the validated params model
    verify: Callable[[Any, ToolResult], VerifyResult] | None = None
    capability: str = ""                  # same capability on several layers = fallbacks of each other
    idempotent: bool = True               # False = running it twice could do harm (a click), so never auto-retry
    needs_confirm: Callable[[Any], bool] | None = None  # per-call risk check on top of `risk`
    user_text: str = ""                   # name of a parameter whose words must come from the user's own request (never from a web page)
    model_visible: bool = True            # False = only Orbit's own code may run it (the language model is never offered it or allowed to call it)

    def __post_init__(self) -> None:
        if self.layer not in LAYER_ORDER:
            raise ValueError(f"unknown layer {self.layer!r}")
        self.capability = self.capability or self.name

    def schema(self) -> dict:
        """Compact tool schema in the format Ollama/OpenAI expect."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _compact(self.params.model_json_schema()),
            },
        }

    def signature(self) -> str:
        """Human/model readable form, e.g. os_set_volume(level: integer)."""
        schema = _compact(self.params.model_json_schema())
        required = set(schema.get("required", []))
        parts = []
        for pname, prop in schema.get("properties", {}).items():
            kind = "|".join(map(str, prop["enum"])) if "enum" in prop else prop.get("type", "any")
            parts.append(f"{pname}{'' if pname in required else '?'}: {kind}")
        return f"{self.name}({', '.join(parts)})"


def _compact(node: Any, parent_key: str = "") -> Any:
    """Drop pydantic's auto-generated 'title' noise to save context tokens."""
    if isinstance(node, dict):
        return {
            k: _compact(v, k)
            for k, v in node.items()
            if not (k == "title" and parent_key != "properties" and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_compact(v, parent_key) for v in node]
    return node


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        for other in self._tools.values():
            if other.capability == tool.capability and other.params is not tool.params:
                raise ValueError(f"{tool.name!r} and {other.name!r} share capability but not params")
        self._tools[tool.name] = tool

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def alternatives(self, tool: Tool) -> list[Tool]:
        """Same capability, best layer first. The router tries them in this order."""
        same = [t for t in self._tools.values() if t.capability == tool.capability]
        return sorted(same, key=lambda t: LAYER_ORDER.index(t.layer))

    def exposed(self) -> list[Tool]:
        """What the model may call: the best-layer tool of each capability."""
        best: dict[str, Tool] = {}
        for t in self._tools.values():
            cur = best.get(t.capability)
            if cur is None or LAYER_ORDER.index(t.layer) < LAYER_ORDER.index(cur.layer):
                best[t.capability] = t
        return list(best.values())

    def get_exposed(self, name: str) -> Tool | None:
        return next((t for t in self.exposed() if t.name == name), None)

    def relevant(self, text: str, limit: int) -> list[Tool]:
        """Pick the tools most related to the request (word overlap). Few tools = send them all."""
        tools = [t for t in self.exposed() if t.model_visible]
        if len(tools) <= limit:
            return tools
        words = {w for w in text.lower().replace("_", " ").split() if len(w) > 2}

        def score(t: Tool) -> int:
            hay = f"{t.name.replace('_', ' ')} {t.description}".lower()
            return sum(1 for w in words if w in hay)

        return sorted(tools, key=score, reverse=True)[:limit]

    def closest_names(self, name: str, n: int = 3) -> list[str]:
        return difflib.get_close_matches(name, [t.name for t in self.exposed()], n=n, cutoff=0.5)

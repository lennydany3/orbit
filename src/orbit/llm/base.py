"""The only thing the agent knows about a model. Swap providers by writing another class like OllamaLLM."""
from dataclasses import dataclass, field
from typing import Protocol


class LLMError(Exception):
    """Model unreachable or misbehaving. The message is shown to the user."""


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    elapsed_s: float = 0.0


class LLM(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse: ...

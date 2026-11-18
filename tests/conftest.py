from pathlib import Path

import pytest
import yaml

from orbit.agent.loop import Agent
from orbit.agent.router import Router
from orbit.agent.safety import Safety
from orbit.llm.base import LLMResponse, ToolCall
from orbit.tools.base import Registry
from orbit.tools.fake import register_fake_tools

CURRENT_STEP = 2  # raise this as build steps are finished; later-step cases are skipped until then


def load_cases() -> list[dict]:
    return yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text(encoding="utf-8"))


class ScriptedLLM:
    """Plays back prepared responses, so the agent loop is tested without a real model."""

    def __init__(self, *responses: LLMResponse):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        return self.responses.pop(0) if self.responses else LLMResponse(content="(script ended)")


def call(name: str, **args) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(name, args)])


@pytest.fixture
def logs() -> list[tuple[str, str]]:
    return []


@pytest.fixture
def registry() -> Registry:
    reg = Registry()
    register_fake_tools(reg)
    return reg


@pytest.fixture
def make_agent(registry, logs):
    def _make(llm=None, confirm=lambda q: False, **kw) -> Agent:
        log = lambda kind, msg: logs.append((kind, msg))  # noqa: E731
        return Agent(llm, registry, Router(log), Safety(confirm=confirm), log, **kw)

    return _make

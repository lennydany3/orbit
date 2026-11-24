"""Groq LLM and the .env loader, against a fake HTTP server (no network, no key needed)."""
import json
import os

import httpx
import pytest

from orbit.env import load_env, parse_env
from orbit.llm.base import LLMError
from orbit.llm.groq_llm import GroqLLM, to_openai_messages


def llm_with(handler) -> GroqLLM:
    client = httpx.Client(base_url="https://groq.test/v1", transport=httpx.MockTransport(handler))
    return GroqLLM(api_key="k", client=client)


def reply(message: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": message}]})


def test_plain_answer_and_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return reply({"role": "assistant", "content": "Hello!"})

    resp = llm_with(handler).chat([{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "t"}}])
    assert resp.content == "Hello!" and resp.tool_calls == []
    assert seen["body"]["tool_choice"] == "auto" and seen["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_tool_call_arguments_are_parsed_from_json_text():
    msg = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "set_volume", "arguments": '{"level": 30}'}}]}
    resp = llm_with(lambda r: reply(msg)).chat([{"role": "user", "content": "volume 30"}], [])
    assert [(c.name, c.arguments) for c in resp.tool_calls] == [("set_volume", {"level": 30})]


def test_history_is_translated_to_openai_shape():
    history = [
        {"role": "user", "content": "mute and pause"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "mute", "arguments": {}}},
                                                            {"function": {"name": "pause", "arguments": {"x": 1}}}]},
        {"role": "tool", "tool_name": "pause", "content": "paused"},
        {"role": "tool", "tool_name": "mute", "content": "muted"},
    ]
    out = to_openai_messages(history)
    calls = out[1]["tool_calls"]
    assert out[1]["content"] is None and calls[1]["function"]["arguments"] == '{"x": 1}'
    ids = {c["function"]["name"]: c["id"] for c in calls}
    assert out[2] == {"role": "tool", "tool_call_id": ids["pause"], "content": "paused"}     # matched by tool name, not by order
    assert out[3] == {"role": "tool", "tool_call_id": ids["mute"], "content": "muted"}
    assert all("tool_name" not in m for m in out)


def test_malformed_tool_call_text_is_handed_to_the_rescue_parser():
    body = {"error": {"code": "tool_use_failed", "message": "bad", "failed_generation": "<function=mute>{}</function>"}}
    resp = llm_with(lambda r: httpx.Response(400, json=body)).chat([{"role": "user", "content": "x"}], [{"type": "function"}])
    assert resp.content == "<function=mute>{}</function>" and resp.tool_calls == []


@pytest.mark.parametrize("status,expected", [(401, "API key"), (404, "ORBIT_GROQ_MODEL"), (500, "Groq error 500")])
def test_errors_are_explained(status, expected):
    with pytest.raises(LLMError, match=expected):
        llm_with(lambda r: httpx.Response(status, json={"error": {"message": "nope"}})).chat([{"role": "user", "content": "x"}], [])


def test_rate_limit_waits_once_then_succeeds(monkeypatch):
    monkeypatch.setattr("orbit.llm.groq_llm.time.sleep", lambda s: None)
    answers = iter([httpx.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "slow down"}}),
                    reply({"content": "ok"})])
    assert llm_with(lambda r: next(answers)).chat([{"role": "user", "content": "x"}], []).content == "ok"


def test_network_failure_is_an_llm_error():
    def handler(request):
        raise httpx.ConnectError("offline")

    with pytest.raises(LLMError, match="internet"):
        llm_with(handler).chat([{"role": "user", "content": "x"}], [])


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        GroqLLM()


# ---- .env ----
def test_parse_env_handles_comments_quotes_and_export():
    text = '# keys\nGROQ_API_KEY="gsk_abc"\nexport SPOTIFY_CLIENT_ID = sk_123  # inline\n\nbroken line\nEMPTY=\n'
    assert parse_env(text) == {"GROQ_API_KEY": "gsk_abc", "SPOTIFY_CLIENT_ID": "sk_123", "EMPTY": ""}


def test_load_env_never_overrides_the_real_environment(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("A_KEY=from_file\nB_KEY=from_file\nC_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("A_KEY", "from_shell")
    monkeypatch.delenv("B_KEY", raising=False)
    monkeypatch.delenv("C_KEY", raising=False)
    try:
        assert load_env(f) == ["B_KEY"]
        assert os.environ["A_KEY"] == "from_shell" and os.environ["B_KEY"] == "from_file" and "C_KEY" not in os.environ
        assert load_env(tmp_path / "missing.env") == []
    finally:
        os.environ.pop("B_KEY", None)

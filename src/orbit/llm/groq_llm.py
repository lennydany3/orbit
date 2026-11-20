"""Groq's hosted models through its OpenAI-compatible chat API. Same interface as OllamaLLM, so the agent does not change.

The agent keeps its history in Ollama's message shape (tool results carry `tool_name`, tool calls carry no id and dict arguments).
OpenAI-style APIs want ids and JSON-string arguments, so `to_openai_messages` translates on the way out.
"""
import copy
import json
import os
import time

import httpx

from .. import config
from .base import LLMError, LLMResponse, ToolCall


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """Ollama-shaped history -> OpenAI-shaped messages (tool-call ids, JSON-string arguments, no `tool_name`)."""
    out: list[dict] = []
    pending: list[tuple[str, str]] = []          # (tool name, call id) still waiting for their tool result
    counter = 0
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                counter += 1
                call_id = f"call_{counter}"
                pending.append((fn.get("name", ""), call_id))
                args = fn.get("arguments", {})
                calls.append({"id": call_id, "type": "function",
                              "function": {"name": fn.get("name", ""), "arguments": args if isinstance(args, str) else json.dumps(args)}})
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
        elif role == "tool":
            idx = next((i for i, (name, _) in enumerate(pending) if name == m.get("tool_name")), 0 if pending else None)
            call_id = pending.pop(idx)[1] if idx is not None else "call_0"
            out.append({"role": "tool", "tool_call_id": call_id, "content": m.get("content", "")})
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out


def _error_text(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", {}).get("message") or resp.text[:200]
    except (ValueError, AttributeError):
        return resp.text[:200]


class GroqLLM:
    def __init__(self, model: str = config.GROQ_MODEL, api_key: str | None = None, temperature: float = config.TEMPERATURE,
                 timeout: float = config.GROQ_TIMEOUT_S, base_url: str = config.GROQ_BASE_URL,
                 client: httpx.Client | None = None, fallback_model: str | None = None):
        key = api_key or config_key()
        if not key:
            raise LLMError("GROQ_API_KEY is not set. Put it in the .env file (see .env.example) and start Orbit again.")
        self.model, self.temperature = model, temperature
        self.fallback_model = config.GROQ_FALLBACK_MODEL if fallback_model is None else fallback_model
        # One long-lived client: the TLS connection is reused, so each request skips the handshake.
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {key}"})

    def with_temperature(self, temperature: float) -> "GroqLLM":
        """The same model and connection with a different temperature (chat wants more variety than tool calls)."""
        other = copy.copy(self)
        other.temperature = temperature
        return other

    def warm_up(self) -> float:
        """Open the connection now (there is no model to load in the cloud). Raises LLMError if Groq cannot be reached."""
        start = time.perf_counter()
        try:
            resp = self._client.get("/models")
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach Groq ({e}).") from e
        if resp.status_code in (401, 403):
            raise LLMError("Groq rejected the API key (check GROQ_API_KEY).")
        return time.perf_counter() - start

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        body = {"model": self.model, "messages": to_openai_messages(messages), "temperature": self.temperature}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        start = time.perf_counter()
        can_fall_back = bool(self.fallback_model) and self.fallback_model != self.model
        resp = self._post(body, wait_if_limited=not can_fall_back)
        if resp.status_code == 429 and can_fall_back:
            resp = self._post(dict(body, model=self.fallback_model))     # the big model is out of tokens this minute: ask the small one now
        if resp.status_code == 400 and tools:
            failed = _failed_generation(resp)
            if failed is not None:                # the model wrote a malformed tool call: hand the text to the agent's rescue parser
                return LLMResponse(content=failed, elapsed_s=time.perf_counter() - start)
        if resp.status_code != 200:
            raise LLMError(self._explain(resp))
        try:
            message = resp.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Groq sent a reply Orbit could not read ({e}).") from e
        calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(name=fn.get("name", ""), arguments=dict(args)))
        return LLMResponse(content=message.get("content") or "", tool_calls=calls, elapsed_s=time.perf_counter() - start)

    def _post(self, body: dict, wait_if_limited: bool = True) -> httpx.Response:
        if body["model"].startswith("openai/gpt-oss") and config.GROQ_REASONING:
            body = dict(body, reasoning_effort=config.GROQ_REASONING)   # these models think first; "low" keeps answers fast
        for attempt in (1, 2):
            try:
                resp = self._client.post("/chat/completions", json=body)
            except httpx.HTTPError as e:
                raise LLMError(f"Cannot reach Groq ({e}). Check your internet connection.") from e
            if resp.status_code == 429 and attempt == 1 and wait_if_limited:      # rate limited: wait the (short) time Groq asks for, once
                wait = _retry_after(resp)
                if wait is not None and wait <= 10:
                    time.sleep(wait)
                    continue
            return resp
        return resp

    def _explain(self, resp: httpx.Response) -> str:
        if resp.status_code in (401, 403):
            return "Groq rejected the API key (check GROQ_API_KEY)."
        if resp.status_code == 404:
            return f"Groq has no model '{self.model}'. Set ORBIT_GROQ_MODEL to one from console.groq.com/docs/models."
        if resp.status_code == 429:
            return f"Groq rate limit reached: {_error_text(resp)}"
        return f"Groq error {resp.status_code}: {_error_text(resp)}"


def config_key() -> str | None:
    return os.environ.get("GROQ_API_KEY") or None


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


def _failed_generation(resp: httpx.Response) -> str | None:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        return None
    return err.get("failed_generation") if err.get("code") == "tool_use_failed" else None

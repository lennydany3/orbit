"""Qwen2.5 7B (or any tool-calling model) through a local Ollama server."""
import json
import time

import httpx
import ollama

from .. import config
from .base import LLMError, LLMResponse, ToolCall


class OllamaLLM:
    def __init__(
        self,
        model: str = config.MODEL,
        host: str = config.OLLAMA_HOST,
        num_ctx: int = config.NUM_CTX,
        temperature: float = config.TEMPERATURE,
        timeout: float = config.LLM_TIMEOUT_S,
        keep_alive: str = config.KEEP_ALIVE,
    ):
        self.keep_alive = keep_alive
        self.model = model
        self.host = host
        self._options = {"num_ctx": num_ctx, "temperature": temperature}
        self._client = ollama.Client(host=host, timeout=timeout)

    def warm_up(self) -> float:
        """Load the model into memory now (one token of output), so the first real request is not the one that pays ~20 s.
        Returns the seconds it took. Raises LLMError if Ollama cannot be reached."""
        start = time.perf_counter()
        try:
            self._client.chat(model=self.model, messages=[{"role": "user", "content": "hi"}],
                              options={**self._options, "num_predict": 1}, keep_alive=self.keep_alive)
        except ollama.ResponseError as e:
            raise LLMError(f"Ollama error {e.status_code}: {e.error}") from e
        except (ConnectionError, httpx.HTTPError) as e:
            raise LLMError(f"Cannot reach Ollama at {self.host} ({e}).") from e
        return time.perf_counter() - start

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        start = time.perf_counter()
        try:
            resp = self._client.chat(
                model=self.model,
                messages=messages,
                tools=tools or None,
                options=self._options,
                keep_alive=self.keep_alive,
            )
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise LLMError(f"Model '{self.model}' is not installed. Run: ollama pull {self.model}") from e
            raise LLMError(f"Ollama error {e.status_code}: {e.error}") from e
        except (ConnectionError, httpx.HTTPError) as e:
            raise LLMError(f"Cannot reach Ollama at {self.host} ({e}). Is the Ollama app running?") from e

        calls = []
        for tc in resp.message.tool_calls or []:
            args = tc.function.arguments
            if isinstance(args, str):  # some models return JSON text instead of an object
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append(ToolCall(name=tc.function.name, arguments=dict(args or {})))
        return LLMResponse(
            content=resp.message.content or "",
            tool_calls=calls,
            elapsed_s=time.perf_counter() - start,
        )

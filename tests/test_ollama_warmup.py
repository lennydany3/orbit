"""The model is loaded ahead of time (and kept loaded) so the first real request never pays the ~20 s cold start."""
import httpx
import pytest

from orbit.llm.base import LLMError
from orbit.llm.ollama_llm import OllamaLLM


class FakeClient:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error


def llm_with(client) -> OllamaLLM:
    llm = OllamaLLM(keep_alive="30m")
    llm._client = client
    return llm


def test_warm_up_loads_the_model_with_one_token_and_the_configured_keep_alive():
    client = FakeClient()
    seconds = llm_with(client).warm_up()
    call = client.calls[0]
    assert seconds >= 0 and call["keep_alive"] == "30m" and call["options"]["num_predict"] == 1
    assert call["messages"][0]["content"] == "hi" and "tools" not in call


def test_real_requests_use_the_same_keep_alive():
    client = FakeClient()
    client.chat = lambda **kw: (client.calls.append(kw), type("R", (), {"message": type("M", (), {"tool_calls": None, "content": "ok"})()})())[1]
    llm_with(client).chat([{"role": "user", "content": "hi"}], [])
    assert client.calls[0]["keep_alive"] == "30m"


def test_warm_up_reports_an_unreachable_server_as_an_llm_error():
    with pytest.raises(LLMError, match="Cannot reach Ollama"):
        llm_with(FakeClient(error=httpx.ConnectError("refused"))).warm_up()

"""Friend mode: routing between chat and PC commands, emotion tags, the chat memory, and Groq's rate-limit fallback."""
import json
from datetime import datetime

import httpx
import pytest
from conftest import ScriptedLLM

from orbit import config
from orbit.agent import friend
from orbit.llm.base import LLMResponse
from orbit.llm.groq_llm import GroqLLM


# ---- emotion tags ----
@pytest.mark.parametrize("reply,emotion,text", [
    ("[proud] Nice work today.", "proud", "Nice work today."),
    ("[Teasing]   Procrastination gold medal.", "teasing", "Procrastination gold medal."),
    ("(caring) Hey, I'm here.", "caring", "Hey, I'm here."),
    ("[mysterious] Hmm.", "neutral", "Hmm."),                       # an unknown tag is never spoken and means neutral
    ("No tag at all.", "neutral", "No tag at all."),
    ("", "neutral", ""),
    ("[neutral] Want an example?[neutral] Sure! Say you build a server...", "neutral", "Want an example?"),   # it wrote the user's turn too
])
def test_split_emotion(reply, emotion, text):
    assert friend.split_emotion(reply) == (emotion, text)


def test_persona_mentions_the_time_of_day_and_the_name():
    p = friend.persona(datetime(2026, 11, 20, 23, 0), user_name="Lenny")
    assert "Lenny's close friend" in p and "Friday late evening" in p
    assert "the user's close friend" in friend.persona(datetime(2026, 11, 20, 9, 0), user_name="")


# ---- chat or command? ----
@pytest.mark.parametrize("text,expected", [
    ("hey orbit", "chat"), ("good morning", "chat"), ("how are you", "chat"),
    ("I had such a long day", "chat"), ("I'm so tired today", "chat"), ("I feel really low", "chat"),
    ("roast me", "chat"), ("tell me a joke", "chat"), ("cheer me up", "chat"), ("I have a doubt", "chat"),
    ("what's the difference between a process and a thread", "chat"), ("explain recursion", "chat"),
    ("why is the sky blue", "chat"), ("lol", "chat"), ("thanks", "chat"),
    ("what is playing right now", "action"), ("what windows do I have open", "action"),
    ("what is the volume", "action"), ("explain the spotify volume", None),     # a PC word: never decided on a guess
    ("open spotify", None), ("switch to chrome", None), ("make it a bit louder", None),
])
def test_looks_like(text, expected):
    assert friend.looks_like(text) == expected


def test_classifier_answers():
    assert friend.parse_classification("CHAT") == "chat" and friend.parse_classification(" chat.") == "chat"
    assert friend.parse_classification("ACTION") == "action" and friend.parse_classification("") == "action"


# ---- the agent ----
def chat_reply(tag: str, text: str) -> LLMResponse:
    return LLMResponse(content=f"[{tag}] {text}")


def test_a_chat_is_answered_by_the_persona_without_tools_and_remembered(make_agent):
    llm = ScriptedLLM(chat_reply("caring", "Oof, that sounds rough."), chat_reply("happy", "Oh nice, you fixed it!"))
    agent = make_agent(llm, friend_mode=True)
    assert agent.handle("I had such a long day") == "Oof, that sounds rough."
    assert (agent.last_kind, agent.last_emotion) == ("chat", "caring")
    assert llm.calls[0][0]["role"] == "system" and "close friend" in llm.calls[0][0]["content"]
    assert agent.handle("I had such a long day, but I finally fixed the bug") == "Oh nice, you fixed it!"
    second = llm.calls[1]
    assert [m["role"] for m in second[1:]] == ["user", "assistant", "user"]       # the conversation so far comes along
    assert second[2]["content"].startswith("[caring]")                           # with its feeling, so the tone stays consistent


def test_chat_memory_is_capped_and_forget_clears_it(make_agent, monkeypatch):
    monkeypatch.setattr(config, "MAX_CHAT_TURNS", 3)
    agent = make_agent(ScriptedLLM(*[chat_reply("neutral", f"reply {i}") for i in range(6)]), friend_mode=True)
    for i in range(5):
        agent.handle(f"I'm so tired {i}")
    assert len(agent.chat_turns) == 3 and agent.chat_turns[-1][0]["content"] == "I'm so tired 4"
    agent.forget()
    assert agent.chat_turns == []


def test_an_unclear_request_asks_the_classifier_once_then_a_follow_up_stays_chat(make_agent):
    llm = ScriptedLLM(LLMResponse(content="CHAT"), chat_reply("happy", "Ha, fair."), chat_reply("teasing", "Of course you did."))
    agent = make_agent(llm, friend_mode=True)
    assert agent.handle("my brother borrowed my charger again") == "Ha, fair."
    assert "ACTION or CHAT" in llm.calls[0][0]["content"]                         # the first call was the classifier
    assert agent.handle("and then he lost it") == "Of course you did."            # no classifier this time: we are mid-chat
    assert len(llm.calls) == 3


def test_an_unclear_request_the_classifier_calls_a_command_goes_to_the_tool_loop(make_agent):
    llm = ScriptedLLM(LLMResponse(content="ACTION"), LLMResponse(content="Sorry, I can't do that."))
    agent = make_agent(llm, friend_mode=True)
    agent.handle("make it a bit louder")
    assert agent.last_kind == "action"
    assert "operates the user's Windows PC" in llm.calls[1][0]["content"]           # the command prompt, with tools


def test_friend_mode_off_never_chats(make_agent):
    llm = ScriptedLLM(LLMResponse(content="(command model)"))
    agent = make_agent(llm, friend_mode=False)
    agent.handle("hey orbit")
    assert agent.last_kind == "action" and len(llm.calls) == 1 and "close friend" not in llm.calls[0][0]["content"]


def test_a_friendly_reply_is_never_mistaken_for_a_faked_action(make_agent):
    """"I've got you" would trip the command path's 'you claimed an action' check; chat has no such check."""
    agent = make_agent(ScriptedLLM(chat_reply("caring", "I've got you. You've done enough today.")), friend_mode=True)
    assert agent.handle("I'm so tired") == "I've got you. You've done enough today."


# ---- Groq: when the big model is rate limited, the small one answers at once ----
def test_groq_falls_back_to_the_small_model_on_a_rate_limit_without_waiting(monkeypatch):
    seen = []
    monkeypatch.setattr("orbit.llm.groq_llm.time.sleep", lambda s: pytest.fail("must not wait when a fallback exists"))

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "big":
            return httpx.Response(429, headers={"retry-after": "4"}, json={"error": {"message": "tokens per minute"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "[happy] hi"}}]})

    client = httpx.Client(base_url="https://groq.test/v1", transport=httpx.MockTransport(handler))
    llm = GroqLLM(model="big", api_key="k", client=client, fallback_model="small")
    assert llm.chat([{"role": "user", "content": "x"}], []).content == "[happy] hi"
    assert seen == ["big", "small"]


def test_with_temperature_keeps_the_connection_and_changes_only_the_temperature():
    llm = GroqLLM(model="m", api_key="k", temperature=0.1)
    warm = llm.with_temperature(0.8)
    assert warm.temperature == 0.8 and llm.temperature == 0.1 and warm._client is llm._client

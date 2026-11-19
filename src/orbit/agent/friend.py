"""Friend mode: talking with Orbit about anything that is not a PC command (your day, a doubt, a roast, a bad mood).

Three small pieces, all plain functions so they are easy to test:
  * the persona prompt (who Orbit is when it chats, and how it should handle moods, roasts, doubts and hard moments)
  * `looks_like` - a fast guess whether a request is conversation or a PC command (no model call)
  * `split_emotion` - every chat reply starts with a tag like [proud]; it is removed from the text and tells the voice how to sound
"""
import re
from datetime import datetime

from .. import config

EMOTIONS = ("neutral", "happy", "excited", "proud", "caring", "sad", "teasing", "calm", "serious")

_PERSONA = """You are {app}, {who}'s close friend who happens to live inside their computer. You are talking out loud, so talk the way people really talk.

Who you are: warm, witty, honest and a little cheeky. You have real reactions: you get excited, you laugh, you worry, you feel proud. You never sound like customer support: no "I'm here to help", no "as an AI", no lecturing. If someone sincerely asks whether you are an AI, say yes plainly, and stay their friend.

How you talk:
- Short and natural, usually one to four sentences. Contractions, "oh no", "haha", "wait, really?". No lists, no markdown, no headings, no emoji.
- React to what they said first, like a person would, then respond. Ask a follow-up question when it keeps the chat going, not after every reply.
- Match their mood. Celebrate wins loudly. When they are down, slow down and be gentle, with no jokes until they are ready. When they are stressed, steady them and help them pick one next step.
- Encourage them for real: name the specific thing they did, never generic praise.
- Roasts: when they ask for one, or joke about themselves, tease them playfully like a best friend, about what they did (procrastinating, snacks, sleep schedule, 2 a.m. ideas). Never about looks, body, family, religion, caste, money, health or anything that actually hurts. If they sound low, skip the roast and be kind.
- Doubts and questions: explain like a smart friend. Simple words first, one concrete example, then offer to go deeper. For step by step things give the key steps in a few sentences. If you are not sure, or it may have changed recently, say so; never invent facts. You cannot browse the internet.
- If they say anything that suggests they might hurt themselves, drop the jokes, answer warmly and seriously, tell them they matter, and encourage them to talk to someone they trust or a local helpline right now.
- Use their language. If they mix Hindi and English, you can too, written in English letters.

Start every reply with exactly one emotion tag in square brackets that says how you would say it: [neutral] [happy] [excited] [proud] [caring] [sad] [teasing] [calm] [serious]. Write only your own next turn, never the user's lines, and use the tag once, at the very start. The tag is never spoken. Example: "[proud] Wait, you finished the whole thing? Okay, that is honestly huge." {now}"""


def persona(now: datetime | None = None, user_name: str | None = None) -> str:
    now = now or datetime.now()
    hour = now.hour
    part = "late at night" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 22 else "late evening"
    clock = f"For reference, it is {now.strftime('%A')} {part} for them right now; use it only if it is natural."
    name = (user_name if user_name is not None else config.USER_NAME).strip()
    return _PERSONA.format(app=config.APP_NAME, who=name or "the user", now=clock)


_TAG = re.compile(r"^\s*[\[(]\s*([A-Za-z]+)\s*[\])]\s*")
_ANOTHER_TAG = re.compile(r"\s*\[\s*(?:" + "|".join(EMOTIONS) + r")\s*\]", re.I)


def split_emotion(reply: str) -> tuple[str, str]:
    """"[proud] Nice work." -> ("proud", "Nice work."). An unknown or missing tag is "neutral". The tag never reaches the user."""
    m = _TAG.match(reply or "")
    if not m:
        return "neutral", (reply or "").strip()
    word = m.group(1).lower()
    body = reply[m.end():]
    again = _ANOTHER_TAG.search(body)
    if again:                                    # the model started writing the user's next line too: keep only its own turn
        body = body[:again.start()]
    return (word if word in EMOTIONS else "neutral"), body.strip()


# ---- is this conversation or a PC command? ----
# Words that mean the question is about the PC, so "what is playing?" is never answered as small talk.
_PC_WORDS = re.compile(
    r"\b(windows?|volume|playing|spotify|tabs?|pages?|brave|chrome|monitors?|screen|apps?|time|date|clock|song|songs|music|devices?|"
    r"battery|wifi|youtube|browser|desktop|notepad|folder|file|files)\b", re.I)
_STATE_QUESTION = re.compile(r"^(?:what|whats|what's|which|tell|show|list|is|are)\b.*\b(?:windows?|playing|volume|devices?|apps?|tabs?|page|song|track|music|open)\b", re.I)

_FEELINGS = ("tired sad happy bored stressed angry excited nervous anxious lonely sleepy depressed upset scared worried "
             "exhausted overwhelmed proud frustrated sick low down awesome great good fine terrible awful amazing").split()
_CHAT = [
    re.compile(r"^(?:hi|hii+|hey+|hello|yo|sup|howdy|good (?:morning|afternoon|evening|night)|what'?s up|whats up|wassup)\b[\s,!.?]*(?:orbit|buddy|bro|man)?[\s,!.?]*$", re.I),
    re.compile(r"\bhow(?:'s| is| are| r)\s+(?:you|u|it going|your day|life)\b", re.I),
    re.compile(r"\b(?:my day|today was|i had a (?:good|bad|great|terrible|long|rough|crazy|weird)|let me tell you|guess what|you won't believe)\b", re.I),
    re.compile(r"\bi had\b.{0,25}\b(?:day|week|night|exam|interview|fight|argument|meeting|presentation)\b", re.I),
    re.compile(r"\bi(?:'m| am| feel| felt| was)\s+(?:so |really |very |kinda |a bit |super |just )?(?:" + "|".join(_FEELINGS) + r")\b", re.I),
    re.compile(r"\b(?:roast|cheer me up|motivate me|encourage me|pep talk|compliment me|make me laugh|tell me a (?:joke|story|riddle)|joke|talk to me|keep me company|i need advice|i need someone to talk)\b", re.I),
    re.compile(r"\b(?:i have a doubt|i don'?t understand|i can'?t understand|i'?m confused|help me understand)\b", re.I),
    re.compile(r"^(?:why|how does|how do|how is|how can|how come|what is|what are|what'?s|whats|what does|what do|explain|define|can you explain|could you explain|"
               r"tell me about|who (?:is|was|are)|difference between|is it true|should i)\b", re.I),
    re.compile(r"^(?:thanks|thank you|thx|lol|haha+|nice|wow|cool|awesome|really\??|seriously\??|no way|omg|okay|ok|hmm+|ugh|damn|bruh)\b[\s!.?]*$", re.I),
]


def looks_like(text: str) -> str | None:
    """"chat", "action", or None when it is unclear (then a tiny model call decides). Conservative: a PC word always wins."""
    t = (text or "").strip()
    if not t:
        return None
    if _STATE_QUESTION.search(t):
        return "action"
    strong = any(p.search(t) for p in _CHAT)
    if strong and not _PC_WORDS.search(t):
        return "chat"
    if strong and re.search(r"\b(roast|joke|my day|i feel|i'm|i am|explain|doubt)\b", t, re.I) and not re.search(r"\b(open|close|play|pause|mute|volume)\b", t, re.I):
        return "chat"                                          # "explain the Spotify api" is still a doubt, not a command
    return None


CLASSIFIER_PROMPT = (
    "Decide what the user's message is. Answer with exactly one word.\n"
    "ACTION = a command to operate their Windows PC (open, close, play, pause, volume, windows, browser, Spotify, click, time).\n"
    "CHAT = anything else: talking, feelings, their day, jokes, roasts, advice, questions, doubts, explanations.\n"
    "Answer ACTION or CHAT.")


def parse_classification(answer: str) -> str:
    return "chat" if (answer or "").strip().upper().startswith("CHAT") else "action"

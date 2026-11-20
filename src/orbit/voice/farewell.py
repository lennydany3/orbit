"""Ending a conversation by voice: "nothing", "leave it", "let's talk later", "that's all", "never mind", "bye", "stop listening"...

After "hey orbit" and a reply, Orbit keeps listening for a few seconds so you can just carry on. These phrases close that
window at once: Orbit says a short goodbye and goes back to waiting for "hey orbit" (or the hold-to-talk key). They never reach
the language model, so "nothing" is not mistaken for small talk and "leave it" never becomes a request.

Only whole sentences count ("nothing" yes, "nothing is playing" no, "do nothing" no). Pure functions: unit-tested, no audio.
Hindi and Tamil phrases (romanised, and in their own scripts) are included as a best effort, because speech-to-text writes
either.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass

# Words people put round a goodbye that mean nothing by themselves.
_LEAD = r"(?:ok(?:ay)?|alright|all right|well|so|yeah|yep|yes|yup|hey|hi|hello|um+|uh+|hmm+|please|pls|cool|fine|great|sure|right|now|just|then|orbit|buddy|bro|man|dude|thanks|thank you|thank u|thx)"
_TAIL = r"(?:for now|right now|now|today|tonight|anyway|ok|okay|please|pls|orbit|buddy|bro|man|dude|thanks|thank you|thank u|thx|yaar|ra|da)"
_LEADING = re.compile(rf"^(?:{_LEAD}\s+)+")
_TRAILING = re.compile(rf"(?:\s+{_TAIL})+$")

_PUNCT = re.compile(r"[!\"#$%&()*+,\-./:;<=>?@\[\\\]^_`{|}~।॥…“”‘]")   # (Indian full stops too; letters and vowel signs are kept)

# kind -> patterns that must match the WHOLE sentence
_PATTERNS: dict[str, tuple[str, ...]] = {
    "nothing": (
        r"nothing(?: (?:else|more|now|at all|really|much|thanks|thank you))*",
        r"never ?mind(?: it)?(?: please)?", r"forget (?:it|about it|that)", r"leave it(?: (?:alone|now|there|for now|please))*", r"drop it",
        r"don'?t worry(?: about it)?", r"it'?s (?:nothing|fine|okay|ok|alright|all right)", r"no(?:pe)? (?:thanks|thank you|nothing|i'?m (?:good|fine|okay))",
        r"that'?s (?:all|it|enough|everything|fine|okay|alright)", r"that is (?:all|it|enough|everything)", r"that will be all", r"that'?ll be all",
        r"that'?ll do", r"that will do", r"i'?m (?:good|done|okay|fine|all set|set)", r"i am (?:good|done|fine|okay|all set)", r"all (?:good|done|set)",
        r"we'?re (?:good|done|all set)", r"enough(?: for now| for today)?", r"no more", r"that'?s enough(?: for (?:now|today))?",
        r"kuch (?:nahi|nahin|nai)(?: yaar)?", r"rehne do|rahne do|reh(?:ne)? de|chhod do|chod do|chhodo|chodo|chhod de|chod de", r"bas(?: itna hi| ho gaya)?",
        r"onnum ?illa(?:i)?|onnumilla(?:i)?|onnum illai|vidu|vittudu|vittu ?da|vittuda",
        "कुछ नहीं", "रहने दो", "छोड़ो", "छोड़ दो",
        "बस", "ஒண்ணும் இல்ல", "ஒன்றுமில்லை", "விடு",
    ),
    "later": (
        r"(?:let'?s|lets|we can|we will|we'?ll|i'?ll|i will|can we|can i|shall we) (?:talk|chat|speak|catch up|do this|continue|do it|try) (?:later|tomorrow|again|another time|after(?:wards)?|some other time)(?: (?:today|tonight|then))?",
        r"(?:talk|chat|speak) (?:to you |with you )?(?:later|tomorrow|soon|again)", r"(?:see|catch) you (?:later|tomorrow|soon|around)", r"see ya(?: later)?",
        r"later(?: then)?|laters|ttyl", r"(?:some )?other time|another time", r"not now|not right now|not today|maybe later|later maybe|not at the moment",
        r"baad (?:mein|me) baat (?:karte hain|karenge|karte hai|karte hein)|phir baat karte hain|baad (?:mein|me) (?:milte hain|milenge)",
        r"appuram pesalam|apparam pesalam|appuram pesuvom|apuram pesalam|pesalam later",
        "बाद में बात करते हैं", "அப்புறம் பேசலாம்", "பிறகு பேசலாம்",
    ),
    "bye": (
        r"bye(?: bye)?(?: for now)?(?: orbit)?", r"goodbye|good bye", r"ta ?ta", r"cya|c ya", r"take care", r"good ?night|night night|nighty night",
        r"gotta go|got to go|i (?:have|need|got|gotta) to go|i'?ve got to go|i must go|i should go", r"i'?m (?:leaving|off|going|heading out|out)",
        r"catch you later", r"see you", r"theek hai bye|thik hai bye|okay bye|ok bye|chalo bye|chalta hoon|chalta hu",
        r"poitu varen|poitu varaen|poitu vaaren", "ठीक है बाय",
    ),
    "stop": (
        r"stop listening(?: to me)?(?: now)?", r"stop (?:the )?(?:conversation|chat|call|session)", r"end (?:the )?(?:conversation|chat|call|session)(?: now)?",
        r"go (?:to )?sleep|go back to sleep|go away|sleep mode|time to sleep", r"be quiet|be silent|quiet down|shh+|shut up|shush",
        r"you can (?:go|sleep|rest|leave|stop)(?: now)?", r"stand ?by", r"dismiss(?: orbit)?", r"leave me alone", r"mute yourself", r"don'?t listen(?: anymore| now)?",
        r"that'?s the end|conversation over|we'?re done here|i'?m done talking|i am done talking",
    ),
}
_COMPILED = {kind: [re.compile(rf"(?:{p})") for p in patterns] for kind, patterns in _PATTERNS.items()}

_REPLIES = {
    "nothing": ("Okay, no problem.", "Sure thing.", "No worries.", "Alright, I'm here if you need me.", "Okay, leaving it."),
    "later": ("Sure, talk to you later.", "Okay, catch you later.", "Sounds good. Just say hey Orbit when you want me.", "Alright, talk soon."),
    "bye": ("Bye for now!", "See you later.", "Take care.", "Okay, bye!"),
    "night": ("Good night!", "Sleep well. Good night!"),
    "stop": ("Okay, I'll stop listening. Say hey Orbit when you need me.", "Going quiet. Just call me when you need me.",
             "Okay. I'm here when you say hey Orbit."),
}


@dataclass(frozen=True)
class Goodbye:
    kind: str          # nothing | later | bye | stop
    heard: str         # the cleaned-up sentence that matched


def clean(text: str) -> str:
    """Lower case, no punctuation (letters of every script stay), single spaces."""
    t = (text or "").lower().replace("’", "'")
    return " ".join(_PUNCT.sub(" ", t).split())


def detect(text: str) -> Goodbye | None:
    """Is this sentence the user ending the conversation? Whole-sentence matches only, after dropping fillers like "okay" and
    "thanks" at the edges. ("no thanks" is matched before "thanks" is dropped, so it still counts.)"""
    base = clean(text)
    if not base or len(base.split()) > 8:
        return None
    lead_free = _LEADING.sub("", base)
    stripped = _TRAILING.sub("", lead_free)
    for candidate in dict.fromkeys((base, lead_free, stripped)):
        if not candidate:
            continue
        for kind, patterns in _COMPILED.items():
            if any(p.fullmatch(candidate) for p in patterns):
                return Goodbye(kind, candidate)
    return None


def reply(goodbye: Goodbye, rng: random.Random | None = None) -> str:
    """A short, spoken goodbye that fits what was said."""
    rng = rng or random.Random()
    kind = "night" if re.search(r"night", goodbye.heard) else goodbye.kind
    return rng.choice(_REPLIES[kind])

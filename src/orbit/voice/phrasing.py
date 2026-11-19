"""Make Orbit sound like a person: tool reports in, natural speech out.

The agent's replies are terse reports ("asked 'Spotify Premium' to close", "Failed: could not complete and verify the action on
any layer (os:os_open_app). Last error: app 'Your Door' not found"). They are fine on screen, but nobody talks like that. This
module rewrites them for the SPOKEN version only; the full original reply is still printed, and the agent is unchanged.
Natural-language replies from the model ("It seems nothing is playing. Would you like to play something?") pass through as they are.
Pure functions plus a small class for variety: no audio, no model.
"""
import random
import re
from urllib.parse import urlparse

OKAYS = ("Okay, ", "Sure, ", "Done. ", "Got it. ")
FILLERS_FIRST = ("One moment.", "Let me check.", "Working on it.", "Just a second.")
FILLERS_LONG = ("Still working on it.", "Almost there.", "This one is taking a little longer.")

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]+")
_SITES = {"youtube.com": "YouTube", "github.com": "GitHub", "google.com": "Google", "spotify.com": "Spotify", "reddit.com": "Reddit",
          "twitter.com": "Twitter", "x.com": "X", "netflix.com": "Netflix", "amazon.com": "Amazon", "wikipedia.org": "Wikipedia",
          "gmail.com": "Gmail", "twitch.tv": "Twitch"}
_BROWSER_SUFFIX = re.compile(r"\s+[-–|]\s+(Brave|Google Chrome|Chrome|Microsoft Edge|Mozilla Firefox)$", re.I)


def clean_title(title: str, limit: int = 60) -> str:
    """A window or page title as you would say it: no emoji, no "(204)" notification count, no browser name, not too long."""
    t = _EMOJI.sub(" ", title or "")
    t = re.sub(r"^\(\d+\)\s*", "", t)
    t = _BROWSER_SUFFIX.sub("", t.strip())
    t = re.sub(r"\bSpotify (Premium|Free)\b", "Spotify", t)
    t = re.sub(r"\s+", " ", t).strip(" '\"")
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:- ")
    return t


def site_name(url: str) -> str:
    host = urlparse(url if "://" in url else "//" + url).netloc.lower().removeprefix("www.")
    return _SITES.get(host, host or "the page")


def _capitalize_sentences(text: str) -> str:
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text.strip())


def _plain_error(text: str) -> str:
    text = re.sub(r"\b(?:os|api|browser):\w+", "", text)           # internal tool names mean nothing out loud
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"\s+", " ", text).strip(" .;:")[:120]


class Phraser:
    def __init__(self, rng: random.Random | None = None, openers: tuple[str, ...] = OKAYS):
        self.rng, self.openers, self._last = rng or random.Random(), openers, ""
        self._first = True

    # ---- variety ----
    def _ok(self) -> str:
        """An opener ("Okay, ", "Done. " ...) for the first action of a reply, never the same one twice in a row."""
        if not self._first:
            return ""
        self._first = False
        choices = [o for o in self.openers if o != self._last] or list(self.openers)
        self._last = self.rng.choice(choices)
        return self._last

    def filler(self, number: int) -> str:
        """Said while the agent is still working: 0 = first (after ~2.5 s), 1 = a second one much later."""
        return self.rng.choice(FILLERS_LONG if number else FILLERS_FIRST)

    # ---- the rewrite ----
    def say(self, reply: str, request: str = "") -> str:
        text = _EMOJI.sub(" ", (reply or "").strip())
        text = re.sub(r"[ \t]+", " ", text)
        if not text:
            return ""
        self._first = True
        if text.startswith("Nothing was done"):
            heard = clean_title(request, 70).rstrip(".!?,;: ")          # no doubled punctuation inside the quotes
            return (f'Sorry, I didn\'t catch what to do with "{heard}". Could you say that another way?' if heard
                    else "Sorry, I didn't understand that. Could you say it another way?")
        if text.startswith("Failed:"):
            return self._failure(text)
        text = text.replace("was already open; brought it to the front", "was already open, brought it to the front")   # its own ";" is not a split
        parts = [p.strip() for p in re.split(r";\s+", text) if p.strip()]
        return _capitalize_sentences(" ".join(self._part(p) for p in parts))

    def _failure(self, text: str) -> str:
        m = re.search(r"app '(.+?)' not found", text)
        if m:
            return f"Sorry, I couldn't find an app called {clean_title(m.group(1))}."
        if "declined" in text:
            return "Okay, I won't do that."
        m = re.search(r"Last error:\s*(.+)$", text) or re.match(r"Failed:\s*(.+)$", text)
        detail = _plain_error(m.group(1)) if m else ""
        return f"Sorry, that didn't work. {detail}." if detail else "Sorry, that didn't work."

    def _part(self, s: str) -> str:
        ok = self._ok
        m = re.match(r"(.+?) was already open, brought it to the front$", s, re.I)
        if m:
            return f"{clean_title(m.group(1))} was already open, so I brought it to the front."
        m = re.match(r"(?:opened|went to) (https?://\S+)(?: \('(.*)'\))?(?: in (?:your Brave|the current tab).*)?$", s, re.I)
        if m:
            return f"{ok()}opening {site_name(m.group(1))}."
        m = re.match(r"asked '(.+?)' to close$", s, re.I)
        if m:
            return f"{ok()}closing {clean_title(m.group(1))}."
        m = re.match(r"moved '(.+?)' to monitor (\d+)(?: \((\w+)\))?", s, re.I)
        if m:
            where = f"{m.group(3)} monitor" if m.group(3) else f"monitor {m.group(2)}"
            return f"{ok()}I moved {clean_title(m.group(1))} to your {where}."
        m = re.match(r"(?:focused|switched to the tab) '(.+?)'", s, re.I)
        if m:
            return f"{ok()}switching to {clean_title(m.group(1))}."
        m = re.match(r"clicked the first video: '(.+?)'", s, re.I)
        if m:
            return f"{ok()}playing {clean_title(m.group(1))}."
        m = re.match(r"clicked '(.+?)'", s, re.I)
        if m:
            return f"{ok()}I clicked {clean_title(m.group(1))}."
        m = re.match(r"set volume to (\d+)%", s, re.I) or re.match(r"volume \d+% -> (\d+)%", s, re.I)
        if m:
            return f"{ok()}volume is {m.group(1)} percent."
        m = re.match(r"volume is (\d+)%, (muted|not muted)", s, re.I)
        if m:
            return f"The volume is {m.group(1)} percent" + (", and it's muted." if m.group(2) == "muted" else ".")
        if s.lower() in ("muted", "unmuted"):
            return f"{ok()}{s.lower()}."
        if re.match(r"resumed Spotify playback", s, re.I):
            return f"{ok()}resuming Spotify."
        m = re.match(r"(playing|paused): '(.+?)' by (.+?) on .+$", s, re.I)
        if m:
            return (f"Right now it's playing {clean_title(m.group(2))} by {m.group(3)}." if m.group(1).lower() == "playing"
                    else f"{clean_title(m.group(2))} by {m.group(3)} is paused.")
        if s.lower() == "nothing is playing on spotify":
            return "Nothing is playing on Spotify right now."
        m = re.match(r"moved playback to ([^(]+?)(?: \(|$| at )", s, re.I)
        if m:
            return f"{ok()}switching the music to {clean_title(m.group(1))}."
        m = re.match(r"already (playing|paused|not playing)", s, re.I)
        if m:
            return f"It's already {'paused' if 'not' in m.group(1).lower() else m.group(1).lower()}."
        m = re.match(r"Spotify volume on (.+?): (\d+)%", s, re.I)
        if m:
            return f"{ok()}{clean_title(m.group(1))} volume is {m.group(2)} percent."
        m = re.match(r"playing '?(.+?)'?(?: on YouTube)?(?: on .+)?(?: \(started Spotify first\))?$", s, re.I)
        if m:
            return f"{ok()}playing {clean_title(m.group(1))}."
        m = re.match(r"(\d+) tab\(s\) open", s, re.I)
        if m:
            return f"You have {m.group(1)} tabs open."
        m = re.match(r"closed (\d+) tab", s, re.I)
        if m:
            return f"{ok()}closed {m.group(1)} tab{'s' if m.group(1) != '1' else ''}."
        m = re.match(r"(\d+) windows? open", s, re.I)
        if m:
            return f"You have {m.group(1)} windows open."
        if s.lower().startswith("entered fullscreen"):
            return f"{ok()}fullscreen."
        if s.lower().startswith("left fullscreen"):
            return f"{ok()}back to normal size."
        m = re.match(r"pressed (back|forward|reload|home)", s, re.I)
        if m:
            return f"{ok()}going {m.group(1).lower()}." if m.group(1).lower() in ("back", "forward", "home") else f"{ok()}reloading."
        m = re.match(r"opened ([^.!?]{1,40})$", s, re.I)               # an app: "opened Spotify"
        if m:
            return f"{ok()}I opened {clean_title(m.group(1))}."
        return s if s[-1:] in ".!?" else s + "."

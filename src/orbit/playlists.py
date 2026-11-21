"""Your playlists: the Spotify links you saved in the window's Music page, so "play my gym playlist" knows which one you mean.

`music.json` (see `userdata`):

    {"version": 1,
     "playlists": [{"id": "p-3f9a1c2b", "kind": "playlist", "spotify_id": "37i9dQZF1DXcBWIGoYBM5M", "name": "Gym Mix",
                    "added": "2026-11-22T10:15"}]}

The window saves a link (it only has to understand the link); `name` may be empty at first, and Orbit fills it in by asking
Spotify's public page for its title (`resolve_names`), so nothing here needs your Spotify login. Playing one goes through the
Spotify tool, which does need it.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import userdata

KINDS = ("playlist", "album", "artist", "track", "show", "episode")
CONTEXT_KINDS = ("playlist", "album", "artist", "show")        # these play as a whole; a track or an episode plays by itself
MAX_PLAYLISTS = 200
MAX_NAME = 100

_ID = r"[A-Za-z0-9]{22}"
_URL = re.compile(
    rf"^https?://(?:open|play)\.spotify\.com/(?:intl-[a-z]{{2,3}}(?:-[a-z]{{2,4}})?/)?(?:embed/)?(?:user/[^/\s?#]+/)?(?P<kind>{'|'.join(KINDS)})/(?P<id>{_ID})(?:[/?#]\S*)?$",
    re.I)
_URI = re.compile(rf"^spotify:(?:user:[^:\s]+:)?(?P<kind>{'|'.join(KINDS)}):(?P<id>{_ID})$", re.I)


@dataclass(frozen=True)
class Link:
    kind: str
    spotify_id: str

    @property
    def uri(self) -> str:
        return f"spotify:{self.kind}:{self.spotify_id}"

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/{self.kind}/{self.spotify_id}"


def parse_link(text: object) -> Link | str:
    """A Spotify link or `spotify:` address -> what it is, or a sentence saying why it is not usable."""
    raw = text.strip() if isinstance(text, str) else ""
    if not raw:
        return "Paste a Spotify link first."
    raw = raw.strip("<>\"' ")
    for pattern in (_URL, _URI):
        m = pattern.match(raw)
        if m:
            return Link(m.group("kind").lower(), m.group("id"))
    low = raw.lower()
    if "spotify.link" in low or "spotify.app.link" in low:
        return "That is one of Spotify's short links. Open it in your browser and copy the full link from the address bar."
    if any(host in low for host in ("music.apple.com", "youtube.com", "youtu.be", "deezer.com", "soundcloud.com", "tidal.com", "music.amazon")):
        return "Orbit plays your music on Spotify, so it needs a Spotify link (open.spotify.com/playlist/...)."
    return "That is not a Spotify link. In Spotify, open the playlist, choose Share, then Copy link, and paste it here."


@dataclass
class Playlist:
    id: str
    kind: str
    spotify_id: str
    name: str = ""
    added: datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def link(self) -> Link:
        return Link(self.kind, self.spotify_id)

    @property
    def title(self) -> str:
        """What to call it out loud: its name, or its kind while the name is not known yet."""
        return self.name or f"your Spotify {self.kind}"

    @classmethod
    def parse(cls, raw: object) -> Playlist | None:
        if not isinstance(raw, dict):
            return None
        kind, sid = raw.get("kind"), raw.get("spotify_id")
        if kind not in KINDS or not isinstance(sid, str) or not re.fullmatch(_ID, sid):
            return None
        known = {"id", "kind", "spotify_id", "name", "added"}
        return cls(userdata.clean(raw.get("id"), 40) or userdata.new_id("p"), kind, sid, userdata.clean(raw.get("name"), MAX_NAME),
                   userdata.from_text(raw.get("added")), {k: v for k, v in raw.items() if k not in known})

    def to_dict(self) -> dict:
        out = dict(self.extra)
        out.update(id=self.id, kind=self.kind, spotify_id=self.spotify_id, name=self.name)
        if self.added is not None:
            out["added"] = userdata.to_text(self.added)
        return out


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))


_QUERY_FILLER = frozenset({"my", "the", "a", "an", "playlist", "album", "artist", "music", "songs", "song", "list", "mix", "saved", "on", "spotify"})


class MusicStore:
    def __init__(self, file: Path | None = None):
        self.file = file
        self.playlists: list[Playlist] = []
        self._stamp: tuple[int, int] | None = None
        self._rest: dict = {}
        self.reload()

    @property
    def path(self) -> Path:
        return self.file or userdata.path_of(userdata.MUSIC_FILE)

    def changed(self) -> bool:
        return userdata.stamp(self.path) != self._stamp

    def reload(self) -> None:
        self._stamp = userdata.stamp(self.path)
        data = userdata.read(self.path)
        raw = data.get("playlists")
        self.playlists = [p for p in (Playlist.parse(x) for x in raw[:MAX_PLAYLISTS]) if p] if isinstance(raw, list) else []
        self._rest = {k: v for k, v in data.items() if k not in ("version", "playlists")}

    def _change(self, apply):
        self.reload()
        result = apply()
        data = dict(self._rest)
        data["playlists"] = [p.to_dict() for p in self.playlists]
        userdata.write(self.path, data)
        self._stamp = userdata.stamp(self.path)
        return result

    def add(self, link: Link, name: str = "", now: datetime | None = None) -> Playlist:
        def apply() -> Playlist:
            for p in self.playlists:
                if p.link == link:
                    return p
            if len(self.playlists) >= MAX_PLAYLISTS:
                raise ValueError(f"that is as many playlists as Orbit keeps ({MAX_PLAYLISTS}); remove one first")
            p = Playlist(userdata.new_id("p"), link.kind, link.spotify_id, userdata.clean(name, MAX_NAME),
                         (now or datetime.now()).replace(second=0, microsecond=0))
            self.playlists.append(p)
            return p

        return self._change(apply)

    def unnamed(self) -> list[Playlist]:
        return [p for p in self.playlists if not p.name]

    def set_name(self, playlist_id: str, name: str, only_if_unnamed: bool = False) -> bool:
        def apply() -> bool:
            for p in self.playlists:
                if p.id == playlist_id and (not only_if_unnamed or not p.name):
                    p.name = userdata.clean(name, MAX_NAME)
                    return True
            return False

        return self._change(apply)

    def get(self, playlist_id: str) -> Playlist | None:
        return next((p for p in self.playlists if p.id == playlist_id), None)

    def find(self, query: str) -> list[Playlist]:
        """The saved playlists a spoken name means, best first: the same words, then the same start, then all its words in it."""
        wanted = [w for w in _words(query) if w not in _QUERY_FILLER]
        if not wanted:
            return []
        scored: list[tuple[int, Playlist]] = []
        for p in self.playlists:
            have = _words(p.name)
            if not have:
                continue
            if have == wanted:
                scored.append((0, p))
            elif have[: len(wanted)] == wanted:
                scored.append((1, p))
            elif all(w in have for w in wanted):
                scored.append((2, p))
        return [p for _, p in sorted(scored, key=lambda s: s[0])]


# ---- names, from Spotify's public page --------------------------------------------------------------------------------------

OEMBED = "https://open.spotify.com/oembed"


def fetch_title(link: Link, http=None, timeout: float = 6.0) -> str | None:
    """The name Spotify shows for this link (its public oEmbed answer: no login, nothing about you is sent), or None."""
    try:
        import httpx

        client = http or httpx
        resp = client.get(OEMBED, params={"url": link.url}, timeout=timeout)
        resp.raise_for_status()
        title = resp.json().get("title")
    except Exception:  # noqa: BLE001 - offline, or Spotify said no: the name stays unknown for now
        return None
    return userdata.clean(title, MAX_NAME) or None


def resolve_names(store: MusicStore, fetch=fetch_title) -> int:
    """Names every saved playlist that has none. Returns how many it named."""
    named = 0
    for p in store.unnamed():
        title = fetch(p.link)
        if title and store.set_name(p.id, title, only_if_unnamed=True):
            named += 1
    return named


class NameResolver(threading.Thread):
    """Looks for playlists with no name every few seconds and names them (a daemon thread; the first look is at once)."""

    def __init__(self, store: MusicStore | None = None, every_s: float = 4.0, fetch=fetch_title):
        super().__init__(name="playlist-names", daemon=True)
        self.store, self.every_s, self.fetch = store or MusicStore(), every_s, fetch
        self._stop_now = threading.Event()
        self._tried: dict[str, int] = {}

    def stop(self) -> None:
        self._stop_now.set()

    def run(self) -> None:
        while not self._stop_now.is_set():
            try:
                self.store.reload()
                for p in self.store.unnamed():
                    if self._tried.get(p.id, 0) >= 3:
                        continue                                    # three tries: then it keeps its kind as its name until you rename it
                    self._tried[p.id] = self._tried.get(p.id, 0) + 1
                    title = self.fetch(p.link)
                    if title:
                        self.store.set_name(p.id, title, only_if_unnamed=True)
            except Exception:  # noqa: BLE001 - a naming problem must never reach the voice loop
                pass
            self._stop_now.wait(self.every_s)

"""Spotify Web API connector (API layer): search, play, list devices, transfer playback, now playing.

Most reliable layer, so the router prefers it. Needs Spotify Premium for play/transfer, and the Orbit app's
user allow-list (development mode). Errors are phrased so the model can act on them.
"""
import re
import socket
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from ..base import Registry, Tool, ToolResult, VerifyResult, read_only_verify
from . import credstore, spotify_auth

API = "https://api.spotify.com/v1"
SETTLE_TIMEOUT_S = 8.0
DEVICE_WAIT_S = 15.0
NOT_LOGGED_IN = "Spotify is not connected yet. Ask the user to run: python -m orbit auth spotify"
_LIKED = re.compile(r"\b(favou?rites?|liked|saved|my (?:music|songs|library))\b", re.I)


class SpotifyError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# ---------------------------------------------------------------- HTTP client with token handling
class SpotifyClient:
    def __init__(self, client_id: str, http: httpx.Client | None = None):
        self.client_id = client_id
        self.http = http or httpx.Client(timeout=15)
        self._access: str | None = None
        self._expires_at = 0.0

    def _refresh(self) -> None:
        refresh = credstore.load_secret(spotify_auth.CREDENTIAL_TARGET)
        if not refresh:
            raise SpotifyError(NOT_LOGGED_IN)
        resp = self.http.post(spotify_auth.TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh, "client_id": self.client_id,
        })
        if resp.status_code != 200:
            raise SpotifyError("Spotify login expired or was revoked. Ask the user to run: python -m orbit auth spotify")
        body = resp.json()
        self._access = body["access_token"]
        self._expires_at = time.monotonic() + body.get("expires_in", 3600) - 60
        if body.get("refresh_token"):  # Spotify may rotate it
            credstore.save_secret(spotify_auth.CREDENTIAL_TARGET, body["refresh_token"])

    def call(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> dict | None:
        """Returns the JSON body, or None for empty answers (204). Raises SpotifyError with a useful message."""
        for attempt in range(3):
            if self._access is None or time.monotonic() >= self._expires_at:
                self._refresh()
            try:
                resp = self.http.request(method, API + path, params=params, json=json,
                                         headers={"Authorization": f"Bearer {self._access}"})
            except httpx.HTTPError as e:
                raise SpotifyError(f"could not reach Spotify ({type(e).__name__})", retryable=True) from e
            if resp.status_code == 401 and attempt == 0:
                self._access = None  # token went stale early: refresh once and retry
                continue
            if resp.status_code == 429 and attempt < 2:
                time.sleep(min(float(resp.headers.get("Retry-After", 1)), 5))
                continue
            if resp.status_code in (200, 201, 202, 204):
                return resp.json() if resp.content else None
            raise self._error(resp)
        raise SpotifyError("Spotify kept rejecting the request", retryable=True)

    @staticmethod
    def _error(resp: httpx.Response) -> SpotifyError:
        try:
            err = resp.json().get("error", {})
            message, reason = err.get("message", ""), err.get("reason", "")
        except ValueError:
            message, reason = resp.text[:100], ""
        if reason == "NO_ACTIVE_DEVICE" or (resp.status_code == 404 and "device" in message.lower()):
            return SpotifyError("no Spotify device is available. Open the Spotify app first (os_open_app) and try again")
        if reason == "PREMIUM_REQUIRED" or (resp.status_code == 403 and "premium" in message.lower()):
            return SpotifyError("Spotify Premium is required to control playback")
        if resp.status_code == 403:
            return SpotifyError(f"Spotify refused (403): {message or 'the account may not be on the app allow-list'}")
        if resp.status_code >= 500:
            return SpotifyError(f"Spotify had a server problem (HTTP {resp.status_code})", retryable=True)
        return SpotifyError(f"Spotify error HTTP {resp.status_code}: {message}")


_client: SpotifyClient | None = None


def get_client() -> SpotifyClient:
    global _client
    if _client is None:
        client_id = spotify_auth.client_id_from_env()
        if not client_id:
            raise SpotifyError(f"{spotify_auth.CLIENT_ID_ENV} is not set")
        _client = SpotifyClient(client_id)
    return _client


# ---------------------------------------------------------------- pure helpers (unit-tested)
@dataclass
class Target:
    body: dict       # request body for PUT /me/player/play
    label: str       # what we are about to play, for humans
    uri: str         # the thing to look for when verifying
    kind: str        # track | artist | album | playlist | liked


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def choose_target(query: str, results: dict) -> Target | None:
    """Pick what to play from a search answer: exact artist/album name first, else the top track."""
    q = _norm(query)
    items = {kind: [i for i in (results.get(kind + "s") or {}).get("items", []) if i] for kind in ("track", "artist", "album", "playlist")}
    for artist in items["artist"]:
        if _norm(artist["name"]) == q:
            return Target({"context_uri": artist["uri"]}, f"{artist['name']} (artist)", artist["uri"], "artist")
    for album in items["album"]:
        if _norm(album["name"]) == q:
            by = (album.get("artists") or [{}])[0].get("name", "")
            return Target({"context_uri": album["uri"]}, f"{album['name']} by {by}", album["uri"], "album")
    if "playlist" in q and items["playlist"]:
        pl = items["playlist"][0]
        return Target({"context_uri": pl["uri"]}, f"playlist {pl['name']}", pl["uri"], "playlist")
    if items["track"]:
        t = items["track"][0]
        by = ", ".join(a["name"] for a in t.get("artists", []))
        return Target({"uris": [t["uri"]]}, f"{t['name']} by {by}", t["uri"], "track")
    return None


def pick_device(devices: list[dict], wanted: str = "this", hostname: str | None = None) -> dict | None:
    """'this'/'here'/'pc' = the Spotify app on this computer. Anything else = match by device name."""
    wanted = (wanted or "this").strip().lower()
    host = (hostname or socket.gethostname()).lower()
    if wanted in ("this", "this device", "here", "this pc", "this computer", "this laptop", "my pc", "my computer", "computer", "pc", "laptop"):
        computers = [d for d in devices if d.get("type") == "Computer"]
        named = [d for d in computers if d.get("name", "").lower() == host or host in d.get("name", "").lower()]
        if named:
            return named[0]
        return computers[0] if len(computers) == 1 else None
    for d in devices:
        if wanted in d.get("name", "").lower():
            return d
    return None


# ---------------------------------------------------------------- tools
class PlayParams(BaseModel):
    query: str = Field(default="", description="song, artist, album or playlist; 'my favourite music' plays liked songs; empty resumes")


class TransferParams(BaseModel):
    device: str = Field(default="this", description="device name, or 'this' for this computer")


class NoParams(BaseModel):
    pass


def _fail(e: SpotifyError) -> ToolResult:
    return ToolResult.failure(str(e), retryable=e.retryable)


def _find_device(client: SpotifyClient, wanted: str, wait_s: float = 0) -> dict | None:
    deadline = time.monotonic() + wait_s
    while True:
        devices = (client.call("GET", "/me/player/devices") or {}).get("devices", [])
        found = pick_device(devices, wanted)
        if found or time.monotonic() >= deadline:
            return found
        time.sleep(1)


def play(p: PlayParams) -> ToolResult:
    try:
        client = get_client()
        device = _find_device(client, "this", wait_s=DEVICE_WAIT_S)
        params = {"device_id": device["id"]} if device else None
        query = p.query.strip()
        if not query:
            client.call("PUT", "/me/player/play", params=params)
            result = ToolResult.success("resumed Spotify playback")
            result.context.update(kind="resume")
            return result
        if _LIKED.search(query):
            liked = [i["track"]["uri"] for i in (client.call("GET", "/me/tracks", params={"limit": 50}) or {}).get("items", []) if i.get("track")]
            if not liked:
                return ToolResult.failure("there are no liked songs in this Spotify account", retryable=False)
            target = Target({"uris": liked}, f"your liked songs ({len(liked)} tracks)", liked[0], "liked")
        else:
            found = client.call("GET", "/search", params={"q": query, "type": "track,artist,album,playlist", "limit": 5})
            target = choose_target(query, found or {})
            if target is None:
                return ToolResult.failure(f"nothing found on Spotify for '{query}'", retryable=False)
        client.call("PUT", "/me/player/play", params=params, json=target.body)
    except SpotifyError as e:
        return _fail(e)
    where = f" on {device['name']}" if device else ""
    result = ToolResult.success(f"playing {target.label}{where}")
    result.context.update(kind=target.kind, uri=target.uri, uris=target.body.get("uris"))
    return result


def _player(client: SpotifyClient) -> dict | None:
    return client.call("GET", "/me/player")


def verify_play(p: PlayParams, r: ToolResult) -> VerifyResult:
    ctx = r.context
    client = get_client()

    def matches(state: dict | None) -> bool:
        if not state or not state.get("is_playing"):
            return False
        if ctx["kind"] == "resume":
            return True
        item = state.get("item") or {}
        if ctx["kind"] in ("artist", "album", "playlist"):
            if (state.get("context") or {}).get("uri") == ctx["uri"]:
                return True
            # Spotify does not always report the context, so check the playing track itself.
            if ctx["kind"] == "artist":
                return any(a.get("uri") == ctx["uri"] for a in item.get("artists", []))
            if ctx["kind"] == "album":
                return (item.get("album") or {}).get("uri") == ctx["uri"]
            return bool(item)  # playlist: tracks can't be tied back to it, so "something is playing" is all we can confirm
        return item.get("uri") in (ctx.get("uris") or [ctx["uri"]])

    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    state = None
    while True:
        try:
            state = _player(client)
        except SpotifyError as e:
            return VerifyResult(False, str(e))
        if matches(state):
            item = (state.get("item") or {}).get("name", "?")
            return VerifyResult(True, f"Spotify reports playing '{item}' on {state['device']['name']}")
        if time.monotonic() >= deadline:
            now = (state or {}).get("item") or {}
            return VerifyResult(False, f"Spotify is not playing the requested item (state: {'playing' if (state or {}).get('is_playing') else 'paused or idle'}, track '{now.get('name', 'none')}')")
        time.sleep(0.7)


def devices(p: NoParams) -> ToolResult:
    try:
        found = (get_client().call("GET", "/me/player/devices") or {}).get("devices", [])
    except SpotifyError as e:
        return _fail(e)
    lines = [f"{d['name']} ({d['type']}){' [active]' if d.get('is_active') else ''}" for d in found]
    return ToolResult.success(f"{len(found)} Spotify device(s)", data="\n".join(lines) or "none")


def now_playing(p: NoParams) -> ToolResult:
    try:
        state = _player(get_client())
    except SpotifyError as e:
        return _fail(e)
    if not state or not state.get("item"):
        return ToolResult.success("nothing is playing on Spotify")
    item = state["item"]
    by = ", ".join(a["name"] for a in item.get("artists", []))
    return ToolResult.success(f"{'playing' if state['is_playing'] else 'paused'}: '{item['name']}' by {by} on {state['device']['name']}")


def transfer(p: TransferParams) -> ToolResult:
    try:
        client = get_client()
        device = _find_device(client, p.device, wait_s=DEVICE_WAIT_S)
        if device is None:
            have = ", ".join(d["name"] for d in (client.call("GET", "/me/player/devices") or {}).get("devices", [])) or "none"
            return ToolResult.failure(f"no Spotify device matches '{p.device}'. Available: {have}. If Spotify is closed, open it first", retryable=False)
        state = _player(client)
        client.call("PUT", "/me/player", json={"device_ids": [device["id"]], "play": bool(state and state.get("is_playing"))})
    except SpotifyError as e:
        return _fail(e)
    result = ToolResult.success(f"moved playback to {device['name']}")
    result.context["device_id"] = device["id"]
    return result


def verify_transfer(p: TransferParams, r: ToolResult) -> VerifyResult:
    client = get_client()
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while True:
        try:
            state = _player(client)
        except SpotifyError as e:
            return VerifyResult(False, str(e))
        if state and state["device"]["id"] == r.context["device_id"]:
            return VerifyResult(True, f"Spotify reports the active device is {state['device']['name']}")
        if time.monotonic() >= deadline:
            return VerifyResult(False, "Spotify still reports a different active device")
        time.sleep(0.7)


# ---------------------------------------------------------------- registration
def register_spotify_tools(registry: Registry) -> None:
    def add(name, description, params, run, verify, **kw):
        registry.register(Tool(name=name, description=description, params=params, layer="api", risk="low", run=run, verify=verify, **kw))

    add("api_spotify_play", "Play a song, artist, album or playlist on Spotify; 'my favourite music' plays liked songs.", PlayParams, play, verify_play)
    add("api_spotify_transfer", "Move Spotify playback to a device, 'this' meaning this computer.", TransferParams, transfer, verify_transfer)
    add("api_spotify_devices", "List the devices Spotify can play on.", NoParams, devices, read_only_verify)
    add("api_spotify_now_playing", "Say what is playing on Spotify right now.", NoParams, now_playing, read_only_verify)

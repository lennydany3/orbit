"""Spotify Web API connector (API layer): search, play, list devices, transfer playback, now playing.

Most reliable layer, so the router prefers it. Needs Spotify Premium for play/transfer, and the Orbit app's
user allow-list (development mode). Errors are phrased so the model can act on them.
"""
import re
import socket
import time
from typing import Literal
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from ..base import Registry, Tool, ToolResult, VerifyResult, read_only_verify
from . import credstore, spotify_auth

API = "https://api.spotify.com/v1"
SETTLE_TIMEOUT_S = 8.0
FRESH_START_RETRIES = 3   # a just-started Spotify app can refuse commands for a moment even though it is listed
RETRY_DELAY_S = 2.0
TRANSFER_ATTEMPTS = 3
DEVICE_WAIT_S = 25.0  # after starting the Spotify app, how long to wait for it to show up as a device
NOT_LOGGED_IN = "Spotify is not connected yet. Ask the user to run: python -m orbit auth spotify"
_LIKED = re.compile(r"\b(favou?rites?|liked|saved|my (?:music|songs|library))\b", re.I)


class SpotifyError(Exception):
    def __init__(self, message: str, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


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
                try:
                    return resp.json() if resp.content else None
                except ValueError:  # some player commands answer 200 with a non-JSON body
                    return None
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
            return SpotifyError("no Spotify device is available. Open the Spotify app first (os_open_app) and try again", status=resp.status_code)
        if reason == "PREMIUM_REQUIRED" or (resp.status_code == 403 and "premium" in message.lower()):
            return SpotifyError("Spotify Premium is required to control playback", status=resp.status_code)
        if resp.status_code == 403:
            return SpotifyError(f"Spotify refused (403): {message or 'the account may not be on the app allow-list'}", status=resp.status_code)
        if resp.status_code >= 500:
            return SpotifyError(f"Spotify had a server problem (HTTP {resp.status_code})", retryable=True, status=resp.status_code)
        return SpotifyError(f"Spotify error HTTP {resp.status_code}: {message}", status=resp.status_code)


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


# "switch to the speaker" / "play it on my phone": a kind of device, not a name
DEVICE_TYPE_WORDS = {
    "speaker": ("Speaker", "CastAudio", "AVR", "AudioDongle"), "speakers": ("Speaker", "CastAudio", "AVR", "AudioDongle"),
    "phone": ("Smartphone",), "mobile": ("Smartphone",), "smartphone": ("Smartphone",),
    "tv": ("TV", "CastVideo"), "television": ("TV", "CastVideo"), "tablet": ("Tablet",), "car": ("Automobile",),
}
THIS_DEVICE_WORDS = {"this", "this device", "here", "this pc", "this computer", "this laptop", "my pc", "my computer", "computer", "pc", "laptop"}


_TRACK_URI = re.compile(r"spotify:track:[A-Za-z0-9]+")


def valid_track_uris(items: list[dict]) -> list[str]:
    """Playable track URIs from a saved-tracks answer. Spotify can return removed tracks (null uri), local files
    (spotify:local:...) or duplicates, and a single bad entry makes the whole play request fail with HTTP 400."""
    seen: set[str] = set()
    uris: list[str] = []
    for item in items:
        track = (item or {}).get("track") or {}
        uri = track.get("uri")
        if track.get("is_local") or not isinstance(uri, str) or not _TRACK_URI.fullmatch(uri) or uri in seen:
            continue
        seen.add(uri)
        uris.append(uri)
    return uris


def pick_device(devices: list[dict], wanted: str = "this", hostname: str | None = None) -> dict | None:
    """'this'/'here'/'pc' = the Spotify app on this computer. Anything else = match by device name."""
    wanted = (wanted or "this").strip().lower()
    host = (hostname or socket.gethostname()).lower()
    if wanted in THIS_DEVICE_WORDS:
        computers = [d for d in devices if d.get("type") == "Computer"]
        named = [d for d in computers if d.get("name", "").lower() == host or host in d.get("name", "").lower()]
        if named:
            return named[0]
        return computers[0] if len(computers) == 1 else None
    kinds = DEVICE_TYPE_WORDS.get(re.sub(r"\b(?:my|the|a)\b", " ", wanted).strip())
    if kinds:
        of_kind = [d for d in devices if d.get("type") in kinds]
        if len(of_kind) == 1:
            return of_kind[0]
        active = [d for d in of_kind if d.get("is_active")]
        return active[0] if len(active) == 1 else None  # two speakers and no way to tell which: do not guess
    squashed = re.sub(r"[^a-z0-9]", "", wanted)  # "lennys echo pop" must match "Lenny's Echo Pop"
    for d in devices:
        if squashed and squashed in re.sub(r"[^a-z0-9]", "", d.get("name", "").lower()):
            return d
    return None


# ---------------------------------------------------------------- tools
class PlayParams(BaseModel):
    query: str = Field(default="", description="song, artist, album or playlist; 'my favourite music' plays liked songs; empty resumes")


class TransferParams(BaseModel):
    device: str = Field(default="this", description="device name only (e.g. Echo), or 'this' for this computer")
    volume: int | None = Field(default=None, ge=0, le=100, description="optional volume percent to set on that device")


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


def launch_spotify() -> bool:
    """Start the Spotify desktop app (an OS-layer helper). True if a launch was started."""
    from ..os_layer import apps
    app, _ = apps.resolve_app("Spotify", apps.installed_apps())
    if app is None:
        return False
    apps.launch(app)
    return True


def _ensure_device(client: SpotifyClient, wanted: str) -> tuple[dict | None, bool]:
    """The device to use, starting the Spotify app on this PC first if it is not running.
    Returns (device or None, whether we had to start Spotify). Other devices (phone, speaker) are never launched."""
    device = _find_device(client, wanted)
    if device or (wanted or "this").strip().lower() not in THIS_DEVICE_WORDS:
        return device, False
    if not launch_spotify():
        return None, False
    return _find_device(client, wanted, wait_s=DEVICE_WAIT_S), True


def _start_playback(client: SpotifyClient, params: dict | None, body: dict | None, fresh: bool) -> None:
    """PUT /me/player/play. Right after we started the Spotify app, wait and retry a few times before giving up."""
    attempts = FRESH_START_RETRIES if fresh else 1
    for attempt in range(1, attempts + 1):
        try:
            client.call("PUT", "/me/player/play", params=params, json=body)
            return
        except SpotifyError as e:
            if attempt == attempts or e.status not in (400, 404, 502, 503):
                raise
            time.sleep(RETRY_DELAY_S)


def play(p: PlayParams) -> ToolResult:
    try:
        client = get_client()
        device, started = _ensure_device(client, "this")
        params = {"device_id": device["id"]} if device else None
        query = p.query.strip()
        if not query:
            _start_playback(client, params, None, started)
            result = ToolResult.success("resumed Spotify playback")
            result.context.update(kind="resume")
            return result
        if _LIKED.search(query):
            items = (client.call("GET", "/me/tracks", params={"limit": 50}) or {}).get("items", [])
            liked = valid_track_uris(items)
            if not liked:
                return ToolResult.failure("there are no playable liked songs in this Spotify account", retryable=False)
            skipped = len(items) - len(liked)
            note = f", skipped {skipped} unplayable" if skipped else ""
            target = Target({"uris": liked}, f"your liked songs ({len(liked)} tracks{note})", liked[0], "liked")
        else:
            found = client.call("GET", "/search", params={"q": query, "type": "track,artist,album,playlist", "limit": 5})
            target = choose_target(query, found or {})
            if target is None:
                return ToolResult.failure(f"nothing found on Spotify for '{query}'", retryable=False, not_found=True)
        try:
            _start_playback(client, params, target.body, started)
        except SpotifyError as e:
            if e.status != 400 or target.kind != "liked":
                raise
            # Spotify rejected the list (one bad entry spoils all of them): still play something, and say so.
            first = target.body["uris"][0]
            _start_playback(client, params, {"uris": [first]}, started)
            target = Target({"uris": [first]}, "your most recent liked song (Spotify rejected the full list)", first, "track")
    except SpotifyError as e:
        return _fail(e)
    where = f" on {device['name']}" if device else ""
    result = ToolResult.success(f"playing {target.label}{where}" + (" (started Spotify first)" if started else ""))
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
    lines = [f"{d['name']} ({d['type']}){' [active]' if d.get('is_active') else ''}"
             + (f" volume {d['volume_percent']}%" if d.get("volume_percent") is not None else "") for d in found]
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
        device, started = _ensure_device(client, p.device)
        if device is None:
            have = ", ".join(d["name"] for d in (client.call("GET", "/me/player/devices") or {}).get("devices", [])) or "none"
            return ToolResult.failure(
                f"no Spotify device matches '{p.device}'. Available right now: {have}. "
                "A phone or speaker only appears while its Spotify app is open or recently used, so open Spotify on it and try again",
                retryable=False, not_found=True)
        state = _player(client)
        was_playing = bool(state and state.get("is_playing"))
        # With a volume: move the device paused, set the volume, then resume, so a speaker never blasts at its old level.
        body = {"device_ids": [device["id"]], "play": was_playing and p.volume is None}
        replayed = False
        try:
            _put_with_retries(client, "/me/player", {"json": body})
        except SpotifyError as e:
            # Spotify's transfer call sometimes answers 500 for a device that plays fine (seen when moving from a speaker
            # back to this PC). Starting the current song on that device is the same thing from the user's side.
            if e.status not in (404, 500, 502, 503) or not (state and state.get("item")):
                raise
            _replay_on(client, device["id"], state)
            if not (was_playing and p.volume is None):
                client.call("PUT", "/me/player/pause", params={"device_id": device["id"]})
            replayed = True
        if p.volume is not None:
            _put_with_retries(client, "/me/player/volume", {"params": {"volume_percent": p.volume, "device_id": device["id"]}})
            if was_playing:
                _put_with_retries(client, "/me/player/play", {"params": {"device_id": device["id"]}})
    except SpotifyError as e:
        return _fail(e)
    extra = f" at {p.volume}% volume" if p.volume is not None else ""
    note = " (Spotify's transfer call failed, so I restarted the current song there)" if replayed else ""
    result = ToolResult.success(f"moved playback to {device['name']}{extra}{note}" + (" (started Spotify first)" if started else ""))
    result.context.update(device_id=device["id"], volume=p.volume)
    return result


def _replay_on(client: SpotifyClient, device_id: str, state: dict) -> None:
    """Start what is playing now (same song, same position) on another device."""
    item_uri = state["item"]["uri"]
    context_uri = (state.get("context") or {}).get("uri")
    body = {"position_ms": state.get("progress_ms") or 0}
    body.update({"context_uri": context_uri, "offset": {"uri": item_uri}} if context_uri else {"uris": [item_uri]})
    _put_with_retries(client, "/me/player/play", {"params": {"device_id": device_id}, "json": body})


def _put_with_retries(client: SpotifyClient, path: str, kwargs: dict) -> None:
    """PUT that retries the transient errors speakers and just-woken apps give (404 'Not found', 5xx)."""
    for attempt in range(1, TRANSFER_ATTEMPTS + 1):
        try:
            client.call("PUT", path, **kwargs)
            return
        except SpotifyError as e:
            if attempt == TRANSFER_ATTEMPTS or e.status not in (404, 500, 502, 503):
                raise
            time.sleep(RETRY_DELAY_S)


class ControlParams(BaseModel):
    action: Literal["play", "pause", "next", "previous"] = Field(description="what to do with Spotify playback")


_CONTROL_CALLS = {
    "pause": ("PUT", "/me/player/pause"), "play": ("PUT", "/me/player/play"),
    "next": ("POST", "/me/player/next"), "previous": ("POST", "/me/player/previous"),
}


def control(p: ControlParams) -> ToolResult:
    try:
        client = get_client()
        state = _player(client)
        if not state or not state.get("device"):
            return ToolResult.failure("nothing is active on Spotify; start playback first", retryable=False)
        if p.action == "play" and state["is_playing"]:
            return ToolResult.success(f"already playing on {state['device']['name']}")
        if p.action == "pause" and not state["is_playing"]:
            return ToolResult.success(f"already paused on {state['device']['name']}")
        method, path = _CONTROL_CALLS[p.action]
        client.call(method, path, params={"device_id": state["device"]["id"]})
    except SpotifyError as e:
        return _fail(e)
    result = ToolResult.success(f"{p.action} on {state['device']['name']}")
    result.context.update(item=(state.get("item") or {}).get("uri"), progress=state.get("progress_ms") or 0)
    return result


def verify_control(p: ControlParams, r: ToolResult) -> VerifyResult:
    if "item" not in r.context:  # nothing was sent ("already playing")
        return VerifyResult(True, r.summary)
    client = get_client()

    def done(state: dict | None) -> bool:
        if not state:
            return False
        if p.action == "pause":
            return not state["is_playing"]
        if p.action == "play":
            return state["is_playing"]
        changed = (state.get("item") or {}).get("uri") != r.context["item"]
        if p.action == "next":
            return changed
        return changed or (state.get("progress_ms") or 0) < r.context["progress"] - 500  # previous may restart the song

    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while True:
        try:
            state = _player(client)
        except SpotifyError as e:
            return VerifyResult(False, str(e))
        if done(state):
            item = (state.get("item") or {}).get("name", "?")
            return VerifyResult(True, f"Spotify reports {'playing' if state['is_playing'] else 'paused'}: '{item}' on {state['device']['name']}")
        if time.monotonic() >= deadline:
            return VerifyResult(False, "Spotify's state did not change as expected")
        time.sleep(0.7)


class VolumeParams(BaseModel):
    level: int | None = Field(default=None, ge=0, le=100, description="volume percent to set, e.g. 50")
    change: int | None = Field(default=None, ge=-100, le=100, description="or percent to add (up) or take away (down), e.g. -10; use this for 'reduce' / 'raise'")
    device: str = Field(default="", description="device name, e.g. Echo; empty = the device playing now")


def set_device_volume(p: VolumeParams) -> ToolResult:
    if p.level is None and p.change is None:
        return ToolResult.failure("say what volume to set (level, e.g. 50) or how much to change it (change, e.g. -10)", retryable=False)
    try:
        client = get_client()
        if p.device:
            device, _ = _ensure_device(client, p.device)
        else:
            state = _player(client)
            device = state["device"] if state else None
        if device is None:
            return ToolResult.failure(f"no Spotify device matches '{p.device or 'the playing one'}'", retryable=False, not_found=True)
        if p.level is not None:
            before, target = None, p.level
        else:
            fresh = next((d for d in (client.call("GET", "/me/player/devices") or {}).get("devices", []) if d["id"] == device["id"]), device)
            before = fresh.get("volume_percent")
            if before is None:
                return ToolResult.failure(f"I can't read {device['name']}'s current volume, so I can't raise or lower it; give me a number instead", retryable=False)
            target = max(0, min(100, before + p.change))
        client.call("PUT", "/me/player/volume", params={"volume_percent": target, "device_id": device["id"]})
    except SpotifyError as e:
        return _fail(e)
    was = f" (was {before}%)" if before is not None else ""
    result = ToolResult.success(f"Spotify volume on {device['name']}: {target}%{was}")
    result.context.update(device_id=device["id"], level=target)
    return result


def verify_device_volume(p: VolumeParams, r: ToolResult) -> VerifyResult:
    client = get_client()
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while True:
        try:
            devices_now = (client.call("GET", "/me/player/devices") or {}).get("devices", [])
        except SpotifyError as e:
            return VerifyResult(False, str(e))
        d = next((x for x in devices_now if x["id"] == r.context["device_id"]), None)
        if d and d.get("volume_percent") is not None and abs(d["volume_percent"] - r.context["level"]) <= 2:
            return VerifyResult(True, f"Spotify reports {d['name']} at {d['volume_percent']}%")
        if time.monotonic() >= deadline:
            now = d.get("volume_percent") if d else None
            return VerifyResult(False, f"Spotify reports the device volume as {now}%, not {r.context['level']}%")
        time.sleep(0.7)


def verify_transfer(p: TransferParams, r: ToolResult) -> VerifyResult:
    client = get_client()
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while True:
        try:
            state = _player(client)
        except SpotifyError as e:
            return VerifyResult(False, str(e))
        if state and state["device"]["id"] == r.context["device_id"]:
            wanted = r.context.get("volume")
            actual = state["device"].get("volume_percent")
            if wanted is None or (actual is not None and abs(actual - wanted) <= 2):
                extra = f" at {actual}%" if wanted is not None else ""
                return VerifyResult(True, f"Spotify reports the active device is {state['device']['name']}{extra}")
            if time.monotonic() >= deadline:
                return VerifyResult(False, f"the device is active but its volume reads {actual}%, not {wanted}%")
        elif time.monotonic() >= deadline:
            return VerifyResult(False, "Spotify still reports a different active device")
        time.sleep(0.7)


# ---------------------------------------------------------------- registration
def register_spotify_tools(registry: Registry) -> None:
    def add(name, description, params, run, verify, **kw):
        registry.register(Tool(name=name, description=description, params=params, layer="api", risk="low", run=run, verify=verify, **kw))

    add("api_spotify_play", "Play a song, artist, album or playlist on Spotify; 'my favourite music' plays liked songs.", PlayParams, play, verify_play)
    add("api_spotify_transfer", "Move Spotify playback to a device (\"put the music on the speaker\", \"play it on my phone\"), optionally setting its volume; 'this' = this computer.", TransferParams, transfer, verify_transfer)
    add("api_spotify_volume", "ONLY change the volume of a Spotify device; it does not move playback (use api_spotify_transfer for that).", VolumeParams, set_device_volume, verify_device_volume)
    add("api_spotify_control", "Play, pause, next or previous on Spotify, on whichever device is playing.", ControlParams, control, verify_control)
    add("api_spotify_devices", "List the devices Spotify can play on.", NoParams, devices, read_only_verify)
    add("api_spotify_now_playing", "Say what is playing on Spotify right now.", NoParams, now_playing, read_only_verify)

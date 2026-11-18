"""Spotify connector tested against a fake Spotify (httpx.MockTransport); the real API is checked by hand."""
import json

import httpx
import pytest

from orbit.agent.router import Router
from orbit.tools.api_layer import credstore, spotify, spotify_auth
from orbit.tools.api_layer.spotify import (
    PlayParams, SpotifyClient, SpotifyError, TransferParams, choose_target, pick_device,
)
from orbit.tools.base import Registry

DEVICES = [
    {"id": "p1", "name": "CMF by Nothing Phone 1", "type": "Smartphone", "is_active": False},
    {"id": "pc1", "name": "ATLAS", "type": "Computer", "is_active": False},
    {"id": "e1", "name": "Kitchen Speaker", "type": "Speaker", "is_active": False},
]


# ---------- PKCE / auth helpers ----------
def test_pkce_challenge_matches_rfc7636_example():
    import base64
    import hashlib
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"  # RFC 7636 appendix B
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_make_pkce_is_valid_and_random():
    v1, c1 = spotify_auth.make_pkce()
    v2, _ = spotify_auth.make_pkce()
    assert 43 <= len(v1) <= 128 and v1 != v2 and "=" not in c1


def test_auth_url_has_pkce_and_exact_redirect():
    url = spotify_auth.build_auth_url("CID", "CHAL", "STATE")
    assert "code_challenge_method=S256" in url and "code_challenge=CHAL" in url and "state=STATE" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8888%2Fcallback" in url
    assert "client_secret" not in url


def test_credential_store_round_trip_and_delete():
    target = "Orbit/test-secret-delete-me"
    try:
        credstore.save_secret(target, "tok-ünï-123")
        assert credstore.load_secret(target) == "tok-ünï-123"
        credstore.save_secret(target, "second")  # overwrite
        assert credstore.load_secret(target) == "second"
    finally:
        credstore.delete_secret(target)
    assert credstore.load_secret(target) is None


# ---------- pure choices ----------
def search(**kw):
    return {k + "s": {"items": v} for k, v in kw.items()}


def test_exact_artist_name_plays_the_artist():
    res = search(artist=[{"name": "Daft Punk", "uri": "spotify:artist:1"}], track=[{"name": "T", "uri": "spotify:track:9", "artists": []}])
    t = choose_target("daft punk", res)
    assert t.kind == "artist" and t.body == {"context_uri": "spotify:artist:1"}


def test_otherwise_top_track():
    res = search(artist=[{"name": "Daft Punk", "uri": "a"}], track=[{"name": "Get Lucky", "uri": "spotify:track:2", "artists": [{"name": "Daft Punk"}]}])
    t = choose_target("get lucky", res)
    assert t.kind == "track" and t.body == {"uris": ["spotify:track:2"]} and "Daft Punk" in t.label


def test_nothing_found_and_null_items_are_safe():
    assert choose_target("zzz", {"tracks": {"items": [None]}, "artists": None}) is None


def test_pick_device_this_computer_by_hostname():
    assert pick_device(DEVICES, "this", hostname="Atlas")["id"] == "pc1"


def test_pick_device_this_with_single_computer_even_if_name_differs():
    assert pick_device(DEVICES, "this", hostname="other-pc")["id"] == "pc1"


def test_pick_device_never_guesses_between_two_computers():
    two = DEVICES + [{"id": "pc2", "name": "WORK", "type": "Computer", "is_active": False}]
    assert pick_device(two, "this", hostname="neither") is None


def test_pick_device_by_name_and_unknown():
    assert pick_device(DEVICES, "kitchen")["id"] == "e1"
    assert pick_device(DEVICES, "tv") is None


# ---------- client with fake Spotify ----------
class FakeSpotify:
    """Tiny stateful fake of the endpoints we use."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.token_requests = 0
        self.playing = False
        self.active = None
        self.item = None
        self.context = None
        self.devices = DEVICES
        self.fail_once_401 = False
        self.play_error = None
        self.ignore_play = False
        self.report_context = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/api/token":
            self.token_requests += 1
            return httpx.Response(200, json={"access_token": f"AT{self.token_requests}", "expires_in": 3600})
        self.calls.append((method, path))
        if self.fail_once_401:
            self.fail_once_401 = False
            return httpx.Response(401, json={"error": {"message": "expired"}})
        if path == "/v1/me/player/devices":
            return httpx.Response(200, json={"devices": self.devices})
        if path == "/v1/search":
            return httpx.Response(200, json=search(
                artist=[{"name": "Daft Punk", "uri": "spotify:artist:1"}],
                track=[{"name": "One More Time", "uri": "spotify:track:1", "artists": [{"name": "Daft Punk"}]}]))
        if path == "/v1/me/tracks":
            return httpx.Response(200, json={"items": [{"track": {"uri": "spotify:track:L1"}}, {"track": {"uri": "spotify:track:L2"}}]})
        if path == "/v1/me/player/play":
            if self.play_error:
                return httpx.Response(self.play_error[0], json=self.play_error[1])
            body = json.loads(request.content) if request.content else {}
            if not self.ignore_play:
                self.playing, self.active = True, request.url.params.get("device_id")
                self.context = body.get("context_uri")
                self.item = (body.get("uris") or ["spotify:track:1"])[0]
            return httpx.Response(204)
        if path == "/v1/me/player" and method == "PUT":
            body = json.loads(request.content)
            self.active = body["device_ids"][0]
            return httpx.Response(204)
        if path == "/v1/me/player" and method == "GET":
            if not self.active:
                return httpx.Response(204)
            dev = next(d for d in self.devices if d["id"] == self.active)
            return httpx.Response(200, json={
                "is_playing": self.playing, "device": dev, "context": {"uri": self.context} if (self.context and self.report_context) else None,
                "item": {"uri": self.item, "name": "One More Time", "artists": [{"name": "Daft Punk", "uri": "spotify:artist:1"}],
                         "album": {"uri": "spotify:album:1"}} if self.item else None})
        return httpx.Response(404, json={"error": {"message": "nope"}})


@pytest.fixture
def fake(monkeypatch):
    fs = FakeSpotify()
    client = SpotifyClient("CID", httpx.Client(transport=httpx.MockTransport(fs.handler)))
    monkeypatch.setattr(spotify, "get_client", lambda: client)
    monkeypatch.setattr(credstore, "load_secret", lambda target: "REFRESH")
    monkeypatch.setattr(credstore, "save_secret", lambda *a, **k: None)
    monkeypatch.setattr(spotify, "SETTLE_TIMEOUT_S", 0.5)
    monkeypatch.setattr(spotify, "DEVICE_WAIT_S", 0.0)
    monkeypatch.setattr(spotify.socket, "gethostname", lambda: "Atlas")
    return fs, client


@pytest.fixture
def run(logs):
    reg = Registry()
    spotify.register_spotify_tools(reg)
    router = Router(lambda k, m: logs.append((k, m)))
    return lambda name, params: router.run(reg.alternatives(reg.get_exposed(name)), params)


def test_play_artist_targets_this_computer_and_verifies(fake, run):
    fs, _ = fake
    r = run("api_spotify_play", PlayParams(query="Daft Punk"))
    assert r.ok and r.verified and "ATLAS" in r.summary and fs.active == "pc1"


def test_play_artist_verified_from_track_when_spotify_reports_no_context(fake, run):
    fs, _ = fake
    fs.report_context = False  # what the real API did in practice
    r = run("api_spotify_play", PlayParams(query="Daft Punk"))
    assert r.ok and r.verified


def test_play_liked_songs(fake, run):
    r = run("api_spotify_play", PlayParams(query="my favourite music"))
    assert r.ok and r.verified and "liked songs (2 tracks)" in r.summary


def test_play_that_spotify_ignores_is_reported_failed(fake, run):
    fs, _ = fake
    fs.ignore_play = True
    r = run("api_spotify_play", PlayParams(query="Daft Punk"))
    assert not r.ok and "not playing the requested item" in r.error


def test_premium_required_message(fake, run):
    fs, _ = fake
    fs.play_error = (403, {"error": {"message": "Player command failed: Premium required", "reason": "PREMIUM_REQUIRED"}})
    r = run("api_spotify_play", PlayParams(query="Daft Punk"))
    assert not r.ok and "Premium is required" in r.error


def test_no_device_message_tells_model_to_open_spotify(fake, run):
    fs, _ = fake
    fs.devices = []
    fs.play_error = (404, {"error": {"message": "Player command failed: No active device found", "reason": "NO_ACTIVE_DEVICE"}})
    r = run("api_spotify_play", PlayParams(query="Daft Punk"))
    assert not r.ok and "os_open_app" in r.error


def test_transfer_verified(fake, run):
    fs, _ = fake
    r = run("api_spotify_transfer", TransferParams(device="this"))
    assert r.ok and r.verified and fs.active == "pc1"


def test_transfer_unknown_device_lists_available(fake, run):
    r = run("api_spotify_transfer", TransferParams(device="tv"))
    assert not r.ok and "ATLAS" in r.error


def test_expired_token_401_refreshes_once_and_retries(fake):
    fs, client = fake
    fs.fail_once_401 = True
    assert client.call("GET", "/me/player/devices")["devices"]
    assert fs.token_requests == 2


def test_not_logged_in_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(credstore, "load_secret", lambda target: None)
    client = SpotifyClient("CID", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(SpotifyError, match="auth spotify"):
        client.call("GET", "/me/player")


def test_connector_only_registered_when_configured(monkeypatch):
    from orbit.tools.api_layer import register_api_tools
    reg = Registry()
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    register_api_tools(reg)
    assert reg.all() == []
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "x")
    register_api_tools(reg)
    assert {t.layer for t in reg.all()} == {"api"}

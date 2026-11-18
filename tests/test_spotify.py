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


def test_pick_device_ignores_apostrophes_and_punctuation():
    devs = DEVICES + [{"id": "e2", "name": "Lenny's Echo Pop", "type": "Speaker", "is_active": False}]
    assert pick_device(devs, "lennys echo pop")["id"] == "e2"
    assert pick_device(devs, "echo")["id"] == "e2"
    assert pick_device(devs, "cmf by nothing")["id"] == "p1"


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
        self.volumes: dict[str, int] = {}
        self.volume_ignored = False
        self.control_ignored = False

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
            return httpx.Response(200, json={"devices": [dict(d, volume_percent=self.volumes.get(d["id"], 50)) for d in self.devices]})
        if path in ("/v1/me/player/pause", "/v1/me/player/next", "/v1/me/player/previous"):
            if self.control_ignored:
                return httpx.Response(204)
            if path.endswith("pause"):
                self.playing = False
            elif path.endswith("next"):
                self.item = "spotify:track:NEXT"
            else:
                self.item = "spotify:track:PREV"
            return httpx.Response(200, content=b"\n")  # a 200 with a body that is not JSON, as Spotify sometimes sends
        if path == "/v1/me/player/volume":
            if self.volume_ignored:
                return httpx.Response(204)
            self.volumes[request.url.params["device_id"]] = int(request.url.params["volume_percent"])
            return httpx.Response(204)
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
                "is_playing": self.playing, "device": dict(dev, volume_percent=self.volumes.get(dev["id"], 50)), "context": {"uri": self.context} if (self.context and self.report_context) else None,
                "progress_ms": 60000, "item": {"uri": self.item, "name": "One More Time", "artists": [{"name": "Daft Punk", "uri": "spotify:artist:1"}],
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
    monkeypatch.setattr(spotify, "RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(spotify, "launch_spotify", lambda: pytest.fail("must not launch Spotify unless it is needed"))
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


def test_no_device_message_tells_model_to_open_spotify(fake, run, monkeypatch):
    fs, _ = fake
    fs.devices = []
    fs.play_error = (404, {"error": {"message": "Player command failed: No active device found", "reason": "NO_ACTIVE_DEVICE"}})
    monkeypatch.setattr(spotify, "launch_spotify", lambda: False)  # and Spotify cannot be started either
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
    monkeypatch.setattr(spotify_auth, "_read_user_env", lambda name: None)
    register_api_tools(reg)
    assert reg.all() == []
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "x")
    register_api_tools(reg)
    assert {t.layer for t in reg.all()} == {"api"}


def test_client_id_falls_back_to_the_windows_user_environment(monkeypatch):
    """A terminal opened before `setx` doesn't have the variable, but the Windows user environment does."""
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.setattr(spotify_auth, "_read_user_env", lambda name: "from-registry")
    assert spotify_auth.client_id_from_env() == "from-registry"
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "from-process")
    assert spotify_auth.client_id_from_env() == "from-process"


def test_spotify_closed_it_is_started_then_used(fake, run, monkeypatch):
    fs, _ = fake
    all_devices = fs.devices
    fs.devices = []  # the app is closed: no devices at all
    launched = []

    def start():
        launched.append(1)
        fs.devices = all_devices  # the app starts and registers itself
        return True

    monkeypatch.setattr(spotify, "launch_spotify", start)
    monkeypatch.setattr(spotify, "DEVICE_WAIT_S", 1.5)
    r = run("api_spotify_play", PlayParams(query="my favourite music"))
    assert r.ok and r.verified and launched == [1] and "started Spotify first" in r.summary


def test_spotify_not_installed_gives_a_clear_error(fake, run, monkeypatch):
    fs, _ = fake
    fs.devices = []
    fs.play_error = (404, {"error": {"message": "No active device found", "reason": "NO_ACTIVE_DEVICE"}})
    monkeypatch.setattr(spotify, "launch_spotify", lambda: False)
    r = run("api_spotify_play", PlayParams(query="Daft Punk"))
    assert not r.ok and "os_open_app" in r.error


def test_transfer_also_starts_spotify_when_closed(fake, run, monkeypatch):
    fs, _ = fake
    all_devices = fs.devices
    fs.devices = []
    monkeypatch.setattr(spotify, "launch_spotify", lambda: setattr(fs, "devices", all_devices) or True)
    monkeypatch.setattr(spotify, "DEVICE_WAIT_S", 1.5)
    r = run("api_spotify_transfer", TransferParams(device="this"))
    assert r.ok and r.verified and "started Spotify first" in r.summary


def test_other_devices_are_never_launched(fake, run):
    fs, _ = fake
    fs.devices = [d for d in DEVICES if d["type"] != "Speaker"]
    r = run("api_spotify_transfer", TransferParams(device="kitchen"))  # launch_spotify would fail the test
    assert not r.ok and "no Spotify device matches" in r.error


def test_valid_track_uris_drops_everything_spotify_would_reject():
    items = [
        {"track": {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}},
        {"track": None},                                              # removed from the catalogue
        {"track": {"uri": None, "name": "gone"}},                     # null uri: this is what made Spotify answer 400
        {"track": {"uri": "spotify:local:Artist:Album:Song:200", "is_local": True}},
        {"track": {"uri": "spotify:episode:bbbbbbbbbbbbbbbbbbbbbb"}},  # not a track
        {"track": {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}},    # duplicate
        {"track": {"uri": "spotify:track:cccccccccccccccccccccc"}},
        None,
    ]
    assert spotify.valid_track_uris(items) == ["spotify:track:aaaaaaaaaaaaaaaaaaaaaa", "spotify:track:cccccccccccccccccccccc"]


def test_play_liked_songs_sends_only_valid_uris_and_says_what_it_skipped(fake, run, monkeypatch):
    fs, client = fake
    sent = {}
    real_call = client.call

    def spy(method, path, **kw):
        if path == "/me/player/play" and kw.get("json"):
            sent.update(kw["json"])
        return real_call(method, path, **kw)

    monkeypatch.setattr(client, "call", spy)
    r = run("api_spotify_play", PlayParams(query="my favourite music"))
    assert r.ok and sent["uris"] == ["spotify:track:L1", "spotify:track:L2"]


def test_if_spotify_still_rejects_the_liked_list_one_song_is_played_and_reported(fake, run):
    fs, client = fake
    real_handler = fs.handler

    def picky(request):  # rejects any multi-track list, like Spotify did with a bad entry in it
        if request.url.path == "/v1/me/player/play" and request.content and len(json.loads(request.content).get("uris", [])) > 1:
            return httpx.Response(400, json={"error": {"message": "Invalid track uri in payload"}})
        return real_handler(request)

    client.http = httpx.Client(transport=httpx.MockTransport(picky))
    r = run("api_spotify_play", PlayParams(query="my favourite music"))
    assert r.ok and r.verified and "rejected the full list" in r.summary


def test_freshly_started_spotify_that_refuses_at_first_is_retried(fake, run, monkeypatch):
    fs, client = fake
    all_devices = fs.devices
    fs.devices = []
    monkeypatch.setattr(spotify, "launch_spotify", lambda: setattr(fs, "devices", all_devices) or True)
    monkeypatch.setattr(spotify, "DEVICE_WAIT_S", 1.5)
    real_handler = fs.handler
    refusals = {"left": 2}

    def flaky(request):  # the new app is listed as a device but not ready for commands yet
        if request.url.path == "/v1/me/player/play" and refusals["left"] > 0:
            refusals["left"] -= 1
            return httpx.Response(400, json={"error": {"message": "Invalid track uri in payload"}})
        return real_handler(request)

    client.http = httpx.Client(transport=httpx.MockTransport(flaky))
    r = run("api_spotify_play", PlayParams(query="my favourite music"))
    assert r.ok and r.verified and "liked songs (2 tracks)" in r.summary and refusals["left"] == 0


def test_no_waiting_and_retrying_when_spotify_was_already_running(fake, run):
    fs, client = fake
    real_handler = fs.handler
    seen = []

    def always_400(request):
        if request.url.path == "/v1/me/player/play":
            seen.append(1)
            return httpx.Response(400, json={"error": {"message": "Invalid track uri in payload"}})
        return real_handler(request)

    client.http = httpx.Client(transport=httpx.MockTransport(always_400))
    r = run("api_spotify_play", PlayParams(query="Daft Punk"))
    assert not r.ok and len(seen) == 1


def test_spotify_device_volume_verified(fake, run):
    from orbit.tools.api_layer.spotify import VolumeParams
    fs, _ = fake
    r = run("api_spotify_volume", VolumeParams(level=30, device="kitchen"))
    assert r.ok and r.verified and fs.volumes["e1"] == 30 and "Kitchen Speaker" in r.summary


def test_spotify_device_volume_that_does_not_stick_is_a_failure(fake, run):
    from orbit.tools.api_layer.spotify import VolumeParams
    fs, _ = fake
    fs.volume_ignored = True
    r = run("api_spotify_volume", VolumeParams(level=30, device="kitchen"))
    assert not r.ok and "not 30%" in r.error


def test_offline_device_error_explains_how_to_make_it_appear(fake, run):
    r = run("api_spotify_transfer", TransferParams(device="phone that is off"))
    assert not r.ok and "open Spotify on it" in r.error


def _playing_fake(fs):
    fs.active, fs.playing, fs.item = "e1", True, "spotify:track:1"  # playing on the speaker


@pytest.mark.parametrize("action", ["pause", "next", "previous"])
def test_spotify_control_acts_on_the_playing_device_and_verifies(fake, run, action):
    from orbit.tools.api_layer.spotify import ControlParams
    fs, _ = fake
    _playing_fake(fs)
    r = run("api_spotify_control", ControlParams(action=action))
    assert r.ok and r.verified and "Kitchen Speaker" in r.summary


def test_spotify_pause_is_idempotent(fake, run):
    from orbit.tools.api_layer.spotify import ControlParams
    fs, _ = fake
    _playing_fake(fs)
    fs.playing = False
    r = run("api_spotify_control", ControlParams(action="pause"))
    assert r.ok and "already paused" in r.summary


def test_spotify_control_that_has_no_effect_is_a_failure(fake, run):
    from orbit.tools.api_layer.spotify import ControlParams
    fs, _ = fake
    _playing_fake(fs)
    fs.control_ignored = True
    r = run("api_spotify_control", ControlParams(action="pause"))
    assert not r.ok and "did not change" in r.error


def test_spotify_control_with_nothing_active(fake, run):
    from orbit.tools.api_layer.spotify import ControlParams
    r = run("api_spotify_control", ControlParams(action="pause"))
    assert not r.ok and "start playback first" in r.error


def test_transfer_retries_transient_spotify_errors(fake, run):
    fs, client = fake
    real_handler = fs.handler
    fails = {"left": 2}

    def flaky(request):
        if request.method == "PUT" and request.url.path == "/v1/me/player" and fails["left"] > 0:
            fails["left"] -= 1
            return httpx.Response(404, json={"error": {"message": "Not found."}})
        return real_handler(request)

    client.http = httpx.Client(transport=httpx.MockTransport(flaky))
    r = run("api_spotify_transfer", TransferParams(device="kitchen"))
    assert r.ok and r.verified and fails["left"] == 0


def test_transfer_with_volume_moves_paused_sets_volume_then_resumes(fake, run):
    fs, client = fake
    _playing_fake(fs)
    fs.active = "pc1"  # playing on the PC now
    real_handler = fs.handler
    order = []

    def spy(request):
        if request.method == "PUT" and request.url.path in ("/v1/me/player", "/v1/me/player/volume", "/v1/me/player/play"):
            body = json.loads(request.content) if request.content else {}
            order.append((request.url.path.rsplit("/", 1)[-1], body.get("play"), request.url.params.get("volume_percent")))
        return real_handler(request)

    client.http = httpx.Client(transport=httpx.MockTransport(spy))
    r = run("api_spotify_transfer", TransferParams(device="kitchen", volume=30))
    assert r.ok and r.verified and "at 30% volume" in r.summary
    assert order == [("player", False, None), ("volume", None, "30"), ("play", None, None)]  # volume is set BEFORE it plays


def _transfer_always_500(fs, client):
    real_handler = fs.handler

    def handler(request):
        if request.method == "PUT" and request.url.path == "/v1/me/player":
            return httpx.Response(500)
        return real_handler(request)

    client.http = httpx.Client(transport=httpx.MockTransport(handler))


def test_transfer_that_spotify_answers_500_falls_back_to_playing_the_same_song_there(fake, run):
    fs, client = fake
    _playing_fake(fs)  # playing on the speaker
    _transfer_always_500(fs, client)
    r = run("api_spotify_transfer", TransferParams(device="this"))
    assert r.ok and r.verified and fs.active == "pc1" and fs.playing and "restarted the current song" in r.summary


def test_fallback_keeps_a_paused_player_paused(fake, run):
    fs, client = fake
    _playing_fake(fs)
    fs.playing = False
    _transfer_always_500(fs, client)
    r = run("api_spotify_transfer", TransferParams(device="this"))
    assert r.ok and fs.active == "pc1" and not fs.playing


def test_transfer_500_with_nothing_to_replay_is_still_an_error(fake, run):
    fs, client = fake
    _transfer_always_500(fs, client)
    r = run("api_spotify_transfer", TransferParams(device="this"))
    assert not r.ok and "server problem" in r.error


# ---------- device volume: absolute and relative, device kinds ----------
def test_volume_change_is_relative_to_the_devices_current_volume(fake, run):
    from orbit.tools.api_layer.spotify import VolumeParams
    fs, _ = fake
    fs.volumes["e1"] = 50
    r = run("api_spotify_volume", VolumeParams(change=-20, device="kitchen"))
    assert r.ok and r.verified and fs.volumes["e1"] == 30 and "(was 50%)" in r.summary


def test_volume_change_is_clamped(fake, run):
    from orbit.tools.api_layer.spotify import VolumeParams
    fs, _ = fake
    fs.volumes["e1"] = 5
    run("api_spotify_volume", VolumeParams(change=-20, device="kitchen"))
    assert fs.volumes["e1"] == 0


def test_volume_needs_a_level_or_a_change(fake, run):
    from orbit.tools.api_layer.spotify import VolumeParams
    r = run("api_spotify_volume", VolumeParams(device="kitchen"))
    assert not r.ok and "level" in r.error and "change" in r.error


def test_volume_on_the_playing_device_when_no_device_is_named(fake, run):
    from orbit.tools.api_layer.spotify import VolumeParams
    fs, _ = fake
    fs.active, fs.playing, fs.item = "e1", True, "spotify:track:1"
    fs.volumes["e1"] = 40
    r = run("api_spotify_volume", VolumeParams(change=10))
    assert r.ok and fs.volumes["e1"] == 50 and "Kitchen Speaker" in r.summary


def test_devices_list_shows_each_volume(fake, run):
    fs, _ = fake
    fs.volumes["e1"] = 35
    r = run("api_spotify_devices", spotify.NoParams())
    assert "Kitchen Speaker (Speaker) volume 35%" in r.data


@pytest.mark.parametrize("word, expected", [("speaker", "e1"), ("my speaker", "e1"), ("phone", "p1"), ("the phone", "p1")])
def test_a_kind_of_device_finds_the_one_device_of_that_kind(word, expected):
    assert pick_device(DEVICES, word)["id"] == expected


def test_two_speakers_are_never_guessed_between():
    two = DEVICES + [{"id": "e2", "name": "Bedroom", "type": "Speaker", "is_active": False}]
    assert pick_device(two, "speaker") is None
    two[3]["is_active"] = True
    assert pick_device(two, "speaker")["id"] == "e2"  # unless exactly one is the active one

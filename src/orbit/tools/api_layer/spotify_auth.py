"""Spotify login with the PKCE flow: no client secret exists anywhere, only a public Client ID.

You approve once in your browser; Spotify redirects to http://127.0.0.1:8888/callback where Orbit is
listening; the long-lived refresh token is saved in the Windows Credential Manager.
"""
import base64
import hashlib
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import credstore

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing "
    "user-library-read user-read-private"
)
CREDENTIAL_TARGET = "Orbit/spotify-refresh-token"
CLIENT_ID_ENV = "SPOTIFY_CLIENT_ID"


class AuthError(Exception):
    """Login failed. The message is safe to show."""


def _read_user_env(name: str) -> str | None:
    """The Windows *user* environment (what `setx` writes). A terminal or editor opened before `setx`
    does not have it in its own environment, so we also look here."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, name)[0] or None
    except OSError:
        return None


def client_id_from_env() -> str | None:
    return os.environ.get(CLIENT_ID_ENV) or _read_user_env(CLIENT_ID_ENV)


def is_logged_in() -> bool:
    return credstore.load_secret(CREDENTIAL_TARGET) is not None


def make_pkce() -> tuple[str, str]:
    """(verifier, challenge) per RFC 7636, method S256."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(client_id: str, challenge: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES, "state": state,
    })


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path != "/callback":
            self.send_error(404)
            return
        self.server.params = {k: v[0] for k, v in parse_qs(url.query).items()}
        ok = "code" in self.server.params
        body = ("Orbit is connected to Spotify. You can close this tab." if ok
                else "Spotify login was not completed. You can close this tab and try again.")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):  # keep the console and logs quiet (the URL contains the one-time code)
        pass


def wait_for_callback(state: str, timeout_s: float = 180) -> str:
    """Listen on 127.0.0.1:8888 until Spotify redirects back. Returns the one-time authorization code."""
    try:
        server = HTTPServer(("127.0.0.1", 8888), _CallbackHandler)
    except OSError as e:
        raise AuthError(f"could not listen on 127.0.0.1:8888 ({e}). Is another program using that port?") from e
    server.params = None
    server.timeout = 1
    deadline = time.monotonic() + timeout_s
    try:
        while server.params is None:
            if time.monotonic() > deadline:
                raise AuthError("timed out waiting for you to approve in the browser")
            server.handle_request()
    finally:
        server.server_close()
    params = server.params
    if params.get("state") != state:
        raise AuthError("login response did not match this request (state mismatch); ignored for safety")
    if "error" in params:
        raise AuthError(f"Spotify login was refused: {params['error']}")
    return params["code"]


def authorize(
    client_id: str,
    open_browser: Callable[[str], object] = webbrowser.open,
    http: httpx.Client | None = None,
    timeout_s: float = 180,
) -> None:
    """Run the whole login and store the refresh token. Raises AuthError on any problem."""
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    open_browser(build_auth_url(client_id, challenge, state))
    code = wait_for_callback(state, timeout_s)
    http = http or httpx.Client(timeout=15)
    resp = http.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
        "client_id": client_id, "code_verifier": verifier,
    })
    if resp.status_code != 200:
        raise AuthError(f"Spotify refused the login code (HTTP {resp.status_code}): {resp.text[:200]}")
    refresh = resp.json().get("refresh_token")
    if not refresh:
        raise AuthError("Spotify did not return a refresh token")
    credstore.save_secret(CREDENTIAL_TARGET, refresh)

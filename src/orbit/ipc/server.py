"""A WebSocket server on this PC for Orbit's face to connect to.

Who may connect: only programs on this machine (it binds 127.0.0.1), that know the token, and that are not a web
page. A browser always sends an Origin header with a WebSocket request, so any page you happen to have open is turned
away; the Rust window is not a browser and sends none. Without those checks any website could listen in on what you say
and answer Orbit's "are you sure?" for you.

Each connected face has its own small send queue and thread, so one that stops reading can never slow Orbit down: its queue
fills and it is disconnected.
"""
from __future__ import annotations

import hmac
import json
import logging
import queue
import threading
from collections.abc import Callable
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from . import protocol

log = logging.getLogger("orbit.ipc")

_QUEUE_LIMIT = 256          # messages waiting for one face: about 12 s of levels, far more than any real hiccup


class _Face:
    """One connected face: an outgoing queue drained by its own thread."""

    def __init__(self, connection):
        self.connection = connection
        self.outbox: queue.Queue[str | None] = queue.Queue(maxsize=_QUEUE_LIMIT)

    def offer(self, text: str) -> bool:
        try:
            self.outbox.put_nowait(text)
            return True
        except queue.Full:
            return False

    def pump(self) -> None:
        try:
            while True:
                text = self.outbox.get()
                if text is None:
                    return
                self.connection.send(text)
        except Exception:  # noqa: BLE001 - the face went away; the receive loop notices and cleans up
            return


class IpcServer:
    def __init__(self, *, host: str = "127.0.0.1", port: int = 8765, token: str,
                 allowed_origins: tuple[str, ...] = (), on_command: Callable[[protocol.ConfirmAnswer], str | None],
                 snapshot: Callable[[], dict]):
        """`on_command(answer)` returns None when it took the answer, or a reason it could not (sent back as an `error`).
        `snapshot()` returns the `hello` message a new face gets first."""
        self.host, self._port, self.token = host, port, token
        self.allowed_origins = allowed_origins
        self._on_command, self._snapshot = on_command, snapshot
        self._faces: list[_Face] = []
        self._lock = threading.Lock()
        self._server = None
        self._thread: threading.Thread | None = None

    # ---- lifecycle ----
    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self._port}/?token={self.token}"

    def start(self) -> None:
        from websockets.sync.server import serve

        self._server = serve(
            self._handle, self.host, self._port,
            origins=[None, *self.allowed_origins],      # None: a client that sends no Origin (not a browser)
            process_request=self._check_token,
            compression=None, max_size=protocol.MAX_COMMAND_BYTES, open_timeout=5, logger=log,
        )
        self._port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name="ipc-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            faces, self._faces = self._faces, []
        for face in faces:
            face.offer(None)
            try:
                face.connection.close()
            except Exception:  # noqa: BLE001
                pass
        if self._server is not None:
            self._server.shutdown()
            self._server = None

    @property
    def faces(self) -> int:
        with self._lock:
            return len(self._faces)

    # ---- sending ----
    def publish(self, message: dict) -> None:
        """Send to every connected face. Never blocks and never raises."""
        text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            faces = list(self._faces)
        for face in faces:
            if not face.offer(text):                   # not reading: drop it rather than slow Orbit down
                self._drop(face, "too far behind")

    def _drop(self, face: _Face, why: str) -> None:
        with self._lock:
            if face not in self._faces:
                return
            self._faces.remove(face)
        log.warning("face disconnected: %s", why)
        try:
            face.connection.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- connections ----
    def _check_token(self, connection, request):
        sent = parse_qs(urlsplit(request.path).query).get("token", [""])[0]
        if not hmac.compare_digest(sent.encode(), self.token.encode()):
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden\n")
        return None

    def _handle(self, connection) -> None:
        face = _Face(connection)
        with self._lock:                               # the hello and registration are one step: no event can slip between them
            face.offer(json.dumps(self._snapshot(), ensure_ascii=False, separators=(",", ":")))
            self._faces.append(face)
        threading.Thread(target=face.pump, name="ipc-face", daemon=True).start()
        try:
            for raw in connection:
                self._receive(face, raw)
        except Exception:  # noqa: BLE001 - closed by the other side
            pass
        finally:
            face.offer(None)
            with self._lock:
                if face in self._faces:
                    self._faces.remove(face)

    def _receive(self, face: _Face, raw) -> None:
        try:
            answer = protocol.parse_command(raw)
            problem = self._on_command(answer)
        except protocol.ProtocolError as e:
            problem = str(e)
        if problem:
            face.offer(json.dumps(protocol.error(problem), separators=(",", ":")))

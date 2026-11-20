"""The server and the bridge, wired together."""
from __future__ import annotations

import secrets

from .. import config
from .bridge import UiBridge
from .server import IpcServer


class Link:
    """`start()` opens the port; `bridge` is what the voice loop talks to; `url` is what a face connects to."""

    def __init__(self, port: int = config.IPC_PORT, token: str = config.IPC_TOKEN, allowed_origins: tuple[str, ...] = config.IPC_ORIGINS,
                 host: str = "127.0.0.1"):
        self.token = token or secrets.token_urlsafe(16)
        self.bridge = UiBridge(lambda message: self.server.publish(message))
        self.server = IpcServer(host=host, port=port, token=self.token, allowed_origins=allowed_origins,
                                on_command=self.bridge.on_command, snapshot=self.bridge.snapshot)

    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.server.stop()

    @property
    def url(self) -> str:
        return self.server.url

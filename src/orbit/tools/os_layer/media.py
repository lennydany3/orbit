"""Media keys (play/pause/next/previous) plus a read-back of what the system says is playing.

Reading uses Windows' Global System Media Transport Controls (the same thing the volume flyout shows),
so it works for Spotify, browsers, etc. Pressing uses real media keys, so it goes wherever Windows routes them.
"""
import asyncio
import ctypes
from dataclasses import dataclass

from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SessionManager

_user32 = ctypes.WinDLL("user32")
_user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_void_p]

_VK = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}
_STATUS = {4: "playing", 5: "paused", 3: "stopped"}
_EXTENDED, _KEYUP = 1, 2


@dataclass(frozen=True)
class MediaState:
    status: str       # playing | paused | stopped | none (no media session at all)
    title: str = ""
    artist: str = ""
    app: str = ""
    position_s: float = 0.0

    def describe(self) -> str:
        if self.status == "none":
            return "no media session"
        who = f"'{self.title}' by {self.artist}" if self.artist else f"'{self.title}'"
        return f"{self.status}: {who} ({self.app})"


async def _read() -> MediaState:
    session = (await SessionManager.request_async()).get_current_session()
    if session is None:
        return MediaState("none")
    props = await session.try_get_media_properties_async()
    status = _STATUS.get(int(session.get_playback_info().playback_status), "stopped")
    position = session.get_timeline_properties().position.total_seconds()
    return MediaState(status, props.title or "", props.artist or "", session.source_app_user_model_id, position)


def state() -> MediaState:
    return asyncio.run(_read())


def press(key: str) -> None:
    vk = _VK[key]
    _user32.keybd_event(vk, 0, _EXTENDED, None)
    _user32.keybd_event(vk, 0, _EXTENDED | _KEYUP, None)

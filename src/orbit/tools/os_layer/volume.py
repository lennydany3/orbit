"""System volume and mute through the Windows Core Audio API (pycaw)."""
from pycaw.pycaw import AudioUtilities


def _endpoint():
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume() -> int:
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(level: int) -> None:
    _endpoint().SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100, None)


def is_muted() -> bool:
    return bool(_endpoint().GetMute())


def set_muted(muted: bool) -> None:
    _endpoint().SetMute(1 if muted else 0, None)

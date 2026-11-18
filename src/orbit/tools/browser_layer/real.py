"""Open links in YOUR OWN Brave profile (e.g. "Personal"), where you are already logged in.

This is deliberately not automation: Orbit just asks Brave to open the link, exactly like clicking one. That is why it
works with your real logins, and why Google does not block it (Google rejects sign-in in a browser that is remote
controlled, as Orbit's own automated window is). The price: Orbit cannot read or click inside pages opened this way.
"""
import json
import subprocess
import time
from pathlib import Path

from ... import config
from ..os_layer import windows

_BRAVE_USER_DATA = Path(config.BRAVE_PATHS[-1]).parents[2] / "User Data"  # ...\Brave-Browser\User Data
LOCAL_STATE = _BRAVE_USER_DATA / "Local State"


class ProfileNotFound(Exception):
    """No Brave profile with that name. The message lists the real ones."""


def brave_profiles(local_state: Path = LOCAL_STATE) -> dict[str, str]:
    """{profile name as shown in Brave: folder name}, e.g. {"Personal": "Default", "Research": "Profile 1"}."""
    try:
        cache = json.loads(local_state.read_text(encoding="utf-8"))["profile"]["info_cache"]
    except (OSError, ValueError, KeyError):
        return {}
    return {info.get("name", folder): folder for folder, info in cache.items()}


def profile_folder(name: str, local_state: Path = LOCAL_STATE) -> str:
    """Folder for a profile name (case-insensitive). With no profile list at all, Brave's first profile, "Default"."""
    profiles = brave_profiles(local_state)
    if not profiles:
        return "Default"
    for shown, folder in profiles.items():
        if shown.lower() == name.lower() or folder.lower() == name.lower():
            return folder
    raise ProfileNotFound(f"no Brave profile called '{name}'. Your profiles: {', '.join(profiles)}")


def brave_titles() -> set[str]:
    return {w.title for w in windows.list_windows() if w.exe == "brave"}


def open_link(url: str, exe: Path, folder: str) -> None:
    """Ask Brave to open the link in that profile (a new tab if Brave is already running)."""
    subprocess.Popen([str(exe), f"--profile-directory={folder}", url])


def brave_in_front(wait_s: float = 8.0) -> windows.Window | None:
    """Wait for a Brave window to be in the foreground (bringing one forward if Windows did not)."""
    deadline = time.monotonic() + wait_s
    while True:
        fg = windows.foreground_window()
        if fg and fg.exe == "brave":
            return fg
        candidates = [w for w in windows.list_windows() if w.exe == "brave"]
        if candidates:
            windows.focus(candidates[0].hwnd)
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.3)

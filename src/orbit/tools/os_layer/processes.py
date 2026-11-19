"""Processes behind windows: is an app still running after its window closed, and quitting an app completely.

Why this exists: closing a window is not quitting an app. Many apps (ChatGPT, Spotify, Discord, ...) keep running in the
system tray after you close their window, and "close" must not pretend otherwise. And a window that runs as administrator
cannot be controlled by a normal program at all (Windows blocks it), so Orbit says that clearly instead of failing silently.
"""
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from . import windows

# Never quit these, whatever the request says: the desktop itself, system services, and what Orbit and the model run in.
PROTECTED = frozenset({
    "explorer", "dwm", "winlogon", "csrss", "services", "svchost", "lsass", "wininit", "system", "smss", "registry", "sihost",
    "fontdrvhost", "ctfmon", "taskhostw", "audiodg", "spoolsv", "runtimebroker", "searchhost", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost", "applicationframehost", "systemsettings", "securityhealthservice",
    "ollama", "ollama app", "python", "pythonw", "conhost", "openconsole", "windowsterminal", "cmd", "powershell", "pwsh",
})
# Processes that legitimately keep running after a window closes: not a "stuck in the tray" situation.
HOST_PROCESSES = frozenset({"explorer", "applicationframehost", "systemsettings", "textinputhost", "searchhost",
                            "startmenuexperiencehost", "shellexperiencehost", "runtimebroker", "dwm"})

ADMIN_HINT = "Start Orbit from a terminal opened with 'Run as administrator' if you want it to control that window."


def _stem(name: str) -> str:
    return Path(name).stem.lower()


def exe_of_pid(pid: int) -> str:
    try:
        return psutil.Process(pid).exe()
    except (psutil.Error, OSError, ValueError):
        return ""


def window_exe_path(hwnd: int) -> str:
    return exe_of_pid(windows.pid_of(hwnd)) if hwnd else ""


def running_with_exe(exe_path: str) -> list[psutil.Process]:
    """Every running process started from this exact program file (Electron apps have a dozen)."""
    if not exe_path:
        return []
    want = exe_path.lower()
    found = []
    for proc in psutil.process_iter(["exe"]):
        try:
            if (proc.info["exe"] or "").lower() == want:
                found.append(proc)
        except (psutil.Error, OSError):
            continue
    return found


def wait_until_gone(exe_path: str, seconds: float) -> list[psutil.Process]:
    """Processes of this program still running after waiting up to `seconds` for them to exit on their own."""
    deadline = time.monotonic() + seconds
    while True:
        left = running_with_exe(exe_path)
        if not left or time.monotonic() >= deadline:
            return left
        time.sleep(0.15)


def background_note(target: windows.Window, exe_path: str, wait_s: float = 1.5) -> str:
    """After a window was closed: if the app has no window left but is still running, say so (it lives in the tray)."""
    if not exe_path or target.exe in HOST_PROCESSES:
        return ""
    if any(w.exe == target.exe for w in windows.list_windows()):
        return ""                                   # another window of the same app is still open: nothing is hiding
    if not wait_until_gone(exe_path, wait_s):
        return ""
    return (f", but {target.exe} is still running in the background (the system tray). "
            f"Say 'quit {target.title}' to exit it completely")


# ---------------------------------------------------------------- quitting an app completely
@dataclass
class AppProcesses:
    name: str            # what the user called it
    exe_path: str
    exe_stem: str
    hwnds: list[int]     # its visible windows (may be empty: tray-only)


def find_app(query: str, wins: list[windows.Window]) -> "AppProcesses | str":
    """The running app the user means, or an explanation of why not. Windows first, then process names (tray-only apps)."""
    query = query.strip()
    matched = windows.match_windows(wins, [query])
    path = window_exe_path(matched[0].hwnd) if matched else ""
    if not path:
        squashed = query.lower().replace(" ", "")
        names: dict[str, list[str]] = {}
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                stem, exe = _stem(proc.info["name"] or ""), proc.info["exe"] or ""
            except (psutil.Error, OSError):
                continue
            if exe and (stem == squashed or stem == query.lower() or (len(squashed) >= 4 and squashed in stem)):
                names.setdefault(exe, []).append(stem)
        if not names:
            return f"nothing running matches '{query}'"
        exact = [e for e, stems in names.items() if squashed in stems or query.lower() in stems]
        pool = exact or list(names)
        if len(pool) > 1:
            return f"'{query}' could mean several programs ({', '.join(sorted({_stem(Path(e).name) for e in pool}))}); say which one"
        path = pool[0]
    stem = _stem(Path(path).name)
    hwnds = [w.hwnd for w in wins if w.exe == stem]
    return AppProcesses(query, path, stem, hwnds)


def refusal_for(app: AppProcesses) -> str:
    """A reason Orbit must not quit this, or "" if it may. Protects the desktop, system services and Orbit's own surroundings."""
    if app.exe_stem in PROTECTED:
        return f"I won't quit {app.exe_stem}: it is part of Windows or of what Orbit itself runs on"
    me = psutil.Process()
    ours = {me.pid} | {p.pid for p in me.parents()}
    if any(p.pid in ours for p in running_with_exe(app.exe_path)):
        return f"I won't quit {app.exe_stem}: Orbit is running inside it"
    return ""


def quit_all(app: AppProcesses, grace_s: float = 2.0) -> tuple[int, str]:
    """Close its windows politely, give it a moment, then end whatever is left. Returns (processes ended, error text or "")."""
    for hwnd in app.hwnds:
        windows.close(hwnd)
    procs = wait_until_gone(app.exe_path, grace_s)
    started = len(running_with_exe(app.exe_path)) if procs else 0
    for proc in procs:
        try:
            proc.terminate()
        except psutil.AccessDenied:
            return 0, "access denied"
        except (psutil.NoSuchProcess, OSError):
            continue
    _, alive = psutil.wait_procs(procs, timeout=3.0)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.Error, OSError):
            pass
    _, alive = psutil.wait_procs(alive, timeout=2.0)
    return max(started, len(procs)) - len(alive), ("" if not alive else f"{len(alive)} process(es) would not end")

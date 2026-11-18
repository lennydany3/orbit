"""Find installed apps by name (everything in the Start menu, classic and Store apps) and launch them."""
import difflib
import json
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_GENERIC_HINTS = {"app", "main"}


@dataclass(frozen=True)
class App:
    name: str     # "Spotify"
    app_id: str   # Start-menu AppID: an exe path, a Store "Package!App" id, or a short id

    def hints(self) -> list[str]:
        """Strings that identify this app's windows: its name plus the exe/package name inside its id."""
        found = [self.name.lower()]
        aid = self.app_id
        if aid.lower().endswith(".exe"):
            found.append(Path(aid.replace("\\", "/")).stem.lower())
        elif "!" in aid:
            found.append(aid.split("!")[-1].lower())
        elif not any(c in aid for c in "\\/{}"):
            found.append(aid.lower())
        return [h for h in dict.fromkeys(found) if len(h) >= 3 and h not in _GENERIC_HINTS]


@lru_cache(maxsize=1)
def installed_apps() -> tuple[App, ...]:
    """Ask Windows for the Start menu app list (about 1 s, cached for the session)."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
    ).stdout
    rows = json.loads(out) if out.strip() else []
    if isinstance(rows, dict):
        rows = [rows]
    return tuple(App(r["Name"], r["AppID"]) for r in rows if r.get("Name") and r.get("AppID"))


def resolve_app(query: str, apps) -> tuple[App | None, list[str]]:
    """Best app for a spoken name. Returns (app, []) or (None, suggestions).

    Tiers: exact name > name starts with query > query is a whole word in name > substring > fuzzy.
    If a tier has several different apps we refuse to guess and return them as suggestions.
    """
    q = query.strip().lower()
    if not q:
        return None, []
    tiers = [
        [a for a in apps if a.name.lower() == q],
        [a for a in apps if a.name.lower().startswith(q)],
        [a for a in apps if q in a.name.lower().split()],
        [a for a in apps if q in a.name.lower()],
    ]
    for tier in tiers:
        names = sorted({a.name for a in tier})
        if len(names) == 1:
            return next(a for a in tier if a.name == names[0]), []
        if len(names) > 1:
            return None, names[:5]
    lowered = {a.name.lower(): a for a in apps}
    close = difflib.get_close_matches(q, list(lowered), n=3, cutoff=0.6)
    if close and difflib.SequenceMatcher(None, q, close[0]).ratio() >= 0.8:
        return lowered[close[0]], []
    return None, [lowered[c].name for c in close]


def launch(app: App) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.app_id}"])

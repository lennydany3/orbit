"""Safety gate: risky tools need the user's explicit yes before they run. Also the file allow-list."""
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from .. import config
from ..tools.base import Tool


def cli_confirm(question: str) -> bool:
    """Ask on the console. Anything other than y/yes (including EOF) means no."""
    try:
        return input(f"  [confirm] {question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


class Safety:
    def __init__(
        self,
        confirm: Callable[[str], bool] = cli_confirm,
        allowed_dirs: list[Path] | None = None,
    ):
        self.confirm = confirm
        self.allowed_dirs = [Path(p).resolve() for p in (allowed_dirs if allowed_dirs is not None else config.ALLOWED_DIRS)]

    def approve(self, candidates: list[Tool], params: BaseModel) -> tuple[bool, str]:
        """True if the action may run. If any layer of the capability is risky, ask first."""
        if not any(t.risk == "confirm" for t in candidates):
            return True, ""
        name = candidates[0].name
        if self.confirm(f"{name}({params.model_dump()}) is a risky action. Allow?"):
            return True, ""
        return False, f"the user declined '{name}', so it was not run. Do not retry it."

    def is_path_allowed(self, path: str | Path) -> bool:
        """For file tools: only paths inside the allow-listed folders may be touched."""
        resolved = Path(path).expanduser().resolve()
        return any(resolved == d or d in resolved.parents for d in self.allowed_dirs)

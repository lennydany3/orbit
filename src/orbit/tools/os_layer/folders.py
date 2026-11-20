"""Opening a folder in File Explorer, for the user's own custom commands. Only folders: never a file or a program, so a
command cannot be turned into "run this".
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..base import ToolResult


def resolve(target: str) -> Path:
    """"%USERPROFILE%\\Downloads" or "~/Documents" -> a real path."""
    return Path(os.path.expandvars(os.path.expanduser(target.strip().strip('"')))).resolve()


def open_folder(target: str, opener: Callable[[str], None] | None = None) -> ToolResult:
    path = resolve(target)
    if not path.exists():
        return ToolResult.failure(f"the folder '{path}' does not exist", retryable=False, not_found=True)
    if not path.is_dir():
        return ToolResult.failure(f"'{path}' is a file, not a folder: commands only open folders", retryable=False)
    try:
        (opener or os.startfile)(str(path))      # type: ignore[attr-defined]  # Windows only
    except OSError as e:
        return ToolResult.failure(f"Windows could not open '{path}': {e}", retryable=False)
    return ToolResult.success(f"opened the folder {path.name or path}")

"""Exit when the window that started Orbit goes away.

The sphere window starts Orbit as a child and keeps the child's standard input open. Nothing is ever written to it: when
the window closes, or crashes, the pipe reaches end-of-file and Orbit stops the way Ctrl+C would (so it closes its browser
window and stops the microphone) instead of being killed halfway through, or left running with nobody to show it.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import BinaryIO


def watch_parent(stream: BinaryIO, on_gone: Callable[[], None]) -> threading.Thread:
    """Calls `on_gone()` once, from a background thread, when `stream` ends. Anything sent on it is ignored."""

    def run() -> None:
        try:
            while stream.read(4096):
                pass
        except (OSError, ValueError):                # closed under us: the same as ending
            pass
        on_gone()

    thread = threading.Thread(target=run, name="parent-watch", daemon=True)
    thread.start()
    return thread

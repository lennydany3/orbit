"""Exit when the window that started Orbit goes away.

The sphere window starts Orbit as a child and keeps the child's standard input open. Nothing is ever written to it: when
the window closes, or crashes, the pipe loses its other end and Orbit stops the way Ctrl+C would (so it closes its browser
window and stops the microphone) instead of being killed halfway through, or left running with nobody to show it.

The pipe is *polled*, never read with a blocking call. On Windows a thread blocked reading a handle holds it, and any other
thread that so much as asks about that handle waits for the read to finish. Native libraries do exactly that when they load
(the Fortran runtime inside scipy checks standard input), so a blocking watcher froze Orbit's voice loading for good.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable

_ERROR_BROKEN_PIPE = 109            # Windows: the other end of the pipe is closed


def _ended(fd: int) -> bool:
    """Has the other end of the pipe on `fd` gone away? Never blocks. False for anything that is not a pipe."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        try:
            handle = msvcrt.get_osfhandle(fd)
        except OSError:                              # closed under us: the same as ending
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.PeekNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD]
        available = wintypes.DWORD(0)
        if kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            if available.value:                      # nothing is ever sent, but never let it pile up
                os.read(fd, min(available.value, 65536))
            return False
        return ctypes.get_last_error() == _ERROR_BROKEN_PIPE     # any other error: a console or a file, which never "ends"
    import select

    try:
        if not select.select([fd], [], [], 0)[0]:
            return False
        return os.read(fd, 4096) == b""
    except (OSError, ValueError):
        return True


def watch_parent(fd: int, on_gone: Callable[[], None], poll_s: float = 0.2) -> threading.Thread:
    """Calls `on_gone()` once, from a background thread, when the pipe on `fd` (standard input) loses its other end."""

    def run() -> None:
        while not _ended(fd):
            time.sleep(poll_s)
        on_gone()

    thread = threading.Thread(target=run, name="parent-watch", daemon=True)
    thread.start()
    return thread

"""The Settings page's choices that apply while Orbit is running: how loud it speaks, which microphone and speaker, whether the wake word
is listened for and whether it stays ready after a reply. The voice loop asks `poll()` between turns; the settings file is looked at
about once a second, and only read again when it changed. (The hold-to-talk key follows the same file by itself: see `hotkey`.)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .. import settings

CHECK_S = 1.0


class LivePrefs:
    def __init__(self, file: Path | None = None, check_s: float = CHECK_S, clock: Callable[[], float] = time.monotonic):
        self._file, self._check_s, self._clock = file, check_s, clock
        self._at = clock()
        self._stamp = self._stamp_now()
        self.current = settings.load(file)

    def _stamp_now(self):
        try:
            st = (self._file or settings.path()).stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def poll(self) -> settings.Settings | None:
        """The new settings if the file changed since the last look (and it is time to look again), else None."""
        now = self._clock()
        if now - self._at < self._check_s:
            return None
        self._at = now
        stamp = self._stamp_now()
        if stamp == self._stamp:
            return None
        self._stamp = stamp
        self.current = settings.load(self._file)
        return self.current

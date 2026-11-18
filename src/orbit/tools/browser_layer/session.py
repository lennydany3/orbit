"""One long-lived Chrome window driven by Playwright, using an Orbit-only profile folder.

The browser starts the first time a browser tool needs it and stays open between requests.
If you close the window, the next request starts a fresh one.
"""
import atexit
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from ... import config

DEFAULT_TIMEOUT_MS = 15_000


class BrowserUnavailable(Exception):
    """Chrome could not be started. The message is safe to show to the user and the model."""


class BrowserSession:
    def __init__(
        self,
        profile_dir: Path = config.BROWSER_PROFILE_DIR,
        channel: str = config.BROWSER_CHANNEL,
        headless: bool = config.BROWSER_HEADLESS,
    ):
        self.profile_dir = profile_dir
        self.channel = channel
        self.headless = headless
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None

    def page(self) -> Page:
        """The tab to work in (opens the browser if needed)."""
        if self._ctx is None:
            self._launch()
        pages = [p for p in self._ctx.pages if not p.is_closed()]
        page = pages[-1] if pages else self._ctx.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        return page

    def _launch(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._pw = self._pw or sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                channel=self.channel,
                headless=self.headless,
                no_viewport=True,
                args=["--start-maximized"],
                ignore_default_args=["--enable-automation"],
            )
        except PlaywrightError as e:
            self._ctx = None
            first_line = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
            raise BrowserUnavailable(
                f"could not start the browser ('{self.channel}'): {first_line}. "
                "Is Google Chrome installed, and is another Orbit window already using the profile?"
            ) from e
        self._ctx.on("close", lambda *_: setattr(self, "_ctx", None))  # user closed the window

    def close(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
            if self._pw is not None:
                self._pw.stop()
        except PlaywrightError:
            pass
        finally:
            self._ctx = self._pw = None


session = BrowserSession()
atexit.register(session.close)

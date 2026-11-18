"""One long-lived browser window (Brave or Chrome) driven by Playwright, using an Orbit-only profile folder.

The browser starts the first time a browser tool needs it and stays open between requests.
If you close the window, the next request starts a fresh one. Your everyday browser window and profile are
never touched: Chromium does not allow remote control of a profile that is in use, so Orbit has its own.
"""
import atexit
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from ... import config

DEFAULT_TIMEOUT_MS = 15_000


class BrowserUnavailable(Exception):
    """The browser could not be started. The message is safe to show to the user and the model."""


def looks_like_profile_in_use(message: str) -> bool:
    """Chromium refuses to start a second copy on the same profile; its message says so in a few different ways."""
    text = message.lower()
    return "existing browser session" in text or "profile is already in use" in text or "profile appears to be in use" in text


def find_brave(candidates=None) -> Path | None:
    return next((p for p in (candidates if candidates is not None else config.BRAVE_PATHS) if p.is_file()), None)


class BrowserSession:
    def __init__(
        self,
        browser: str = config.BROWSER,
        profile_root: Path = config.BROWSER_PROFILE_DIR,
        headless: bool = config.BROWSER_HEADLESS,
    ):
        self.profile_root = profile_root
        self.headless = headless
        self._pw: Playwright | None = None
        self._ctx: BrowserContext | None = None
        self.browser = "brave"
        self.use(browser)

    def use(self, browser: str) -> None:
        """Choose "brave" or "chrome". Takes effect the next time the browser starts."""
        if browser not in ("brave", "chrome"):
            raise ValueError(f"unknown browser '{browser}' (use brave or chrome)")
        if browser != self.browser and self._ctx is not None:
            self.close()
        self.browser = browser

    @property
    def running(self) -> bool:
        return self._ctx is not None

    @property
    def profile_dir(self) -> Path:
        # Chrome keeps the folder it always used; Brave gets its own, so the two never share a profile.
        return self.profile_root if self.browser == "chrome" else self.profile_root / self.browser

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
        where: dict = {"channel": "chrome"}
        if self.browser == "brave":
            exe = find_brave()
            if exe is None:
                raise BrowserUnavailable("Brave was not found. Install it, or switch with --browser chrome (or ORBIT_BROWSER=chrome).")
            where = {"executable_path": str(exe)}
        try:
            self._pw = self._pw or sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                **where,
                headless=self.headless,
                no_viewport=True,
                args=["--start-maximized"],
                ignore_default_args=["--enable-automation"],
            )
        except PlaywrightError as e:
            self._ctx = None
            first_line = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
            if looks_like_profile_in_use(str(e)):
                raise BrowserUnavailable(
                    f"Orbit's {self.browser.title()} window is already open in another Orbit session (another terminal?). "
                    "Use that session, or quit it (or close its browser window) and try again."
                ) from e
            raise BrowserUnavailable(
                f"could not start {self.browser}: {first_line}. "
                f"Is {self.browser.title()} installed, and is another Orbit window already using the profile?"
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

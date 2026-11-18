"""Browser layer logic tested with a fake Playwright page (real Chrome is checked by hand via the CLI)."""
import pytest

from orbit.agent.router import Router
from orbit.agent.safety import Safety
from orbit.tools.base import Registry
from orbit.tools.browser_layer import (
    ClickParams, FullscreenParams, OpenParams, _RISKY_CLICK, normalize_url, register_browser_tools, same_site, session,
)
from orbit.tools.browser_layer.session import BrowserUnavailable


# ---------- pure helpers ----------
@pytest.mark.parametrize("raw, expected", [
    ("github.com", "https://github.com"),
    ("https://github.com/x?y=1", "https://github.com/x?y=1"),
    ("  http://example.org ", "http://example.org"),
])
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize("bad", ["", "file:///C:/Users/x/secret.txt", "javascript:alert(1)", "chrome://settings", "ftp://x.org"])
def test_normalize_url_blocks_dangerous_schemes(bad):
    with pytest.raises(ValueError):
        normalize_url(bad)


@pytest.mark.parametrize("a, b, same", [
    ("https://github.com/login", "https://github.com", True),
    ("https://www.youtube.com/watch?v=1", "https://youtube.com", True),
    ("https://www.google.co.in/", "https://www.google.com", True),
    ("https://evil.example/github.com", "https://github.com", False),
    ("chrome-error://chromewebdata/", "https://github.com", False),
])
def test_same_site(a, b, same):
    assert same_site(a, b) is same


@pytest.mark.parametrize("text, risky", [
    ("Buy now", True), ("Delete account", True), ("Send message", True), ("Place order", True),
    ("Sign in", False), ("Next", False), ("Show more", False),
])
def test_risky_click_words(text, risky):
    assert bool(_RISKY_CLICK.search(text)) is risky


# ---------- tools with a fake page ----------
class FakeResponse:
    def __init__(self, status):
        self.status = status


class FakeLocator:
    def __init__(self, page, found=True):
        self.page, self.found = page, found
        self.first = self

    def count(self):
        return 1 if self.found else 0

    def click(self):
        self.page.clicked.append(1)
        if self.page.click_changes_page:
            self.page.text_len += 50


class FakePage:
    def __init__(self):
        self.url, self._title, self.status = "about:blank", "", 200
        self.text_len, self.clicked, self.click_changes_page, self.has_text = 10, [], True, True
        self.destination = None  # where goto ends up (to simulate redirects/errors)

    def goto(self, url, wait_until=None):
        self.url = self.destination or url
        self._title = "Some title"
        return FakeResponse(self.status)

    def bring_to_front(self):
        pass

    def title(self):
        return self._title

    def evaluate(self, js, *a):
        if "innerText" in js and "length" in js:
            return self.text_len
        return None

    def get_by_role(self, role, name=None):
        return FakeLocator(self, found=self.has_text and role == "button")

    def get_by_text(self, text):
        return FakeLocator(self, found=self.has_text)


@pytest.fixture
def env(monkeypatch, logs):
    page = FakePage()
    monkeypatch.setattr(session, "page", lambda: page)
    monkeypatch.setattr("orbit.tools.browser_layer.SETTLE_TIMEOUT_S", 0.3)
    reg = Registry()
    register_browser_tools(reg)
    router = Router(lambda k, m: logs.append((k, m)))

    def run(name, params):
        return router.run(reg.alternatives(reg.get_exposed(name)), params)

    return page, run, reg


def test_open_verified(env):
    page, run, _ = env
    r = run("browser_open", OpenParams(url="github.com"))
    assert r.ok and r.verified and page.url == "https://github.com"


def test_open_http_error_is_a_failure(env):
    page, run, _ = env
    page.status = 404
    r = run("browser_open", OpenParams(url="github.com/nope"))
    assert not r.ok and "HTTP 404" in r.error


def test_open_that_lands_elsewhere_is_a_failure(env):
    page, run, _ = env
    page.destination = "chrome-error://chromewebdata/"
    r = run("browser_open", OpenParams(url="github.com"))
    assert not r.ok and "error page" in r.error


def test_open_blocked_scheme_is_not_retried(env, logs):
    _, run, _ = env
    r = run("browser_open", OpenParams(url="file:///C:/x"))
    assert not r.ok and "only http" in r.error and "retry" not in [k for k, _ in logs]


def test_browser_unavailable_is_a_clean_failure(env, monkeypatch):
    def boom():
        raise BrowserUnavailable("could not start the browser")

    monkeypatch.setattr(session, "page", boom)
    r = env[1]("browser_open", OpenParams(url="github.com"))
    assert not r.ok and "could not start the browser" in r.error


def test_click_verified_by_page_change(env):
    page, run, _ = env
    r = run("browser_click", ClickParams(text="Next"))
    assert r.ok and r.verified


def test_click_with_no_visible_effect_fails_and_is_never_clicked_twice(env):
    page, run, _ = env
    page.click_changes_page = False
    r = run("browser_click", ClickParams(text="Next"))
    assert not r.ok and "looks the same" in r.error
    assert len(page.clicked) == 1  # a click is not idempotent: no automatic retry


def test_click_missing_text(env):
    page, run, _ = env
    page.has_text = False
    r = run("browser_click", ClickParams(text="Nope"))
    assert not r.ok and "nothing on the page" in r.error


def test_risky_click_needs_confirmation(env):
    _, _, reg = env
    tool = reg.get_exposed("browser_click")
    asked = []
    safety = Safety(confirm=lambda q: asked.append(q) or False)
    assert safety.approve([tool], ClickParams(text="Buy now"))[0] is False and asked
    asked.clear()
    assert safety.approve([tool], ClickParams(text="Sign in"))[0] is True and not asked


def test_fullscreen_params_default_enters():
    assert FullscreenParams().on is True


# ---------- which browser ----------
def test_brave_and_chrome_never_share_a_profile(tmp_path):
    from orbit.tools.browser_layer.session import BrowserSession
    s = BrowserSession(browser="brave", profile_root=tmp_path)
    assert s.profile_dir == tmp_path / "brave"
    s.use("chrome")
    assert s.profile_dir == tmp_path  # Chrome keeps the folder it always used


def test_unknown_browser_is_rejected(tmp_path):
    from orbit.tools.browser_layer.session import BrowserSession
    with pytest.raises(ValueError):
        BrowserSession(browser="firefox", profile_root=tmp_path)


def test_find_brave_picks_the_first_that_exists(tmp_path):
    from orbit.tools.browser_layer.session import find_brave
    real = tmp_path / "brave.exe"
    real.write_text("x")
    assert find_brave([tmp_path / "missing.exe", real]) == real
    assert find_brave([tmp_path / "missing.exe"]) is None


def test_missing_brave_gives_a_clear_message(tmp_path, monkeypatch):
    import importlib
    session_module = importlib.import_module("orbit.tools.browser_layer.session")  # (the package re-exports an object named session)
    monkeypatch.setattr(session_module, "find_brave", lambda candidates=None: None)
    s = session_module.BrowserSession(browser="brave", profile_root=tmp_path)
    with pytest.raises(BrowserUnavailable, match="Brave was not found"):
        s.page()


def test_profile_in_use_is_recognised_and_explained():
    import importlib
    m = importlib.import_module("orbit.tools.browser_layer.session")
    assert m.looks_like_profile_in_use("BrowserType.launch_persistent_context: Opening in existing browser session. This usually means that the profile is already in use")
    assert not m.looks_like_profile_in_use("executable doesn't exist at C:/x/brave.exe")

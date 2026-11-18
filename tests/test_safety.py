from orbit.agent.safety import Safety
from orbit.logging_setup import redact


def test_path_allow_list(tmp_path):
    allowed = tmp_path / "ok"
    allowed.mkdir()
    s = Safety(allowed_dirs=[allowed])
    assert s.is_path_allowed(allowed / "a.txt")
    assert not s.is_path_allowed(tmp_path / "other.txt")
    assert not s.is_path_allowed(allowed / ".." / "other.txt")  # no escaping with ..


def test_secrets_are_redacted_from_logs(monkeypatch):
    monkeypatch.setenv("SPOTIFY_TOKEN", "super-secret-value-123")
    assert "super-secret-value-123" not in redact("token is super-secret-value-123")

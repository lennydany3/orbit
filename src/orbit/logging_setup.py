"""Action log: every line goes to the console AND to logs/orbit.log, with secrets redacted."""
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Callable

from . import config

_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def redact(text: str) -> str:
    """Replace the value of any secret-looking environment variable with ***."""
    for name, value in os.environ.items():
        if len(value) >= 8 and any(p in name.upper() for p in _SECRET_NAME_PARTS):
            text = text.replace(value, "***")
    return text


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("orbit")
    if logger.handlers:
        return logger
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        config.LOG_DIR / "orbit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


class ActionLog:
    """Callable `log(kind, message)`. kinds: rule, llm, act, verify, retry, fallback, safety, time, error."""

    def __init__(self, echo: Callable[[str], None] = print, logger: logging.Logger | None = None):
        self._echo = echo
        self._logger = logger or logging.getLogger("orbit")

    def __call__(self, kind: str, message: str) -> None:
        line = redact(f"[{kind}] {message}")
        self._logger.info(line)
        self._echo("  " + line)

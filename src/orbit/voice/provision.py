"""Getting the voice models onto a fresh install.

The installed app ships the small models (the voice-activity detector and the wake word, a few MB) and fetches the big ones the
first time it runs, into the models folder in your Orbit data folder:

  * Whisper `tiny.en`   (75 MB)  checks that a wake word really was "hey orbit"
  * Parakeet TDT 0.6B   (640 MB) turns your speech into text, fast (see stt_fast.py); Whisper `small.en` (480 MB) instead
                                 when ORBIT_STT=whisper
  * the Piper voice     (65 MB)  speaks

It is a one-time download, shown on the face as a notice, and never runs from source: there `python -m orbit.voice.fetch_models`
is still the way, so nothing is fetched behind your back. Anything that fails is reported and skipped: Orbit starts anyway and
the voice that cannot load says so, and the next start tries again.
"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from .. import config as app_config
from . import config

Say = Callable[[str], None]

# What ships inside the program (relative to the models folder), copied out once so they can be replaced by hand.
BUNDLED = ("silero_vad.onnx", "openwakeword/embedding_model.onnx", "openwakeword/melspectrogram.onnx", "openwakeword/hey_orbit.npz")


def copy_bundled(source: Path | None = None, target: Path | None = None) -> list[str]:
    """Puts the shipped models in the models folder when they are not there yet. Returns the names it copied."""
    source = source or app_config.BUNDLE_DIR / "models"
    target = target or config.MODELS_DIR
    copied = []
    for name in BUNDLED:
        src, dst = source / name, target / name
        if dst.exists() or not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(name)
    return copied


def missing() -> list[tuple[str, str]]:
    """What still has to be downloaded: (kind, name) pairs, smallest first."""
    from .stt_fast import parakeet_ready

    need: list[tuple[str, str]] = []
    if config.WAKE_VERIFY and not (config.WHISPER_DIR / config.WAKE_VERIFY_MODEL / "model.bin").exists():
        need.append(("whisper", config.WAKE_VERIFY_MODEL))
    if not (config.PIPER_DIR / f"{config.PIPER_VOICE}.onnx").exists():
        need.append(("piper", config.PIPER_VOICE))
    if config.STT_PROVIDER in ("whisper", "local"):
        if not (config.WHISPER_DIR / config.WHISPER_MODEL / "model.bin").exists():
            need.append(("whisper", config.WHISPER_MODEL))
    elif not parakeet_ready(config.PARAKEET_MODEL):
        need.append(("parakeet", config.PARAKEET_MODEL))
    return need


def ensure_models(say: Say = print, notify: Say | None = None) -> bool:
    """Makes sure the models are in place. `say` is the log, `notify` the line the face shows. True if nothing is missing after."""
    from . import fetch_models

    notify = notify or (lambda _text: None)
    for name in copy_bundled():
        say(f"  model ready: {name}")
    todo = missing()
    for number, (kind, name) in enumerate(todo, 1):
        step = f"({number} of {len(todo)})"
        notify(f"Getting Orbit ready: downloading {'the voice' if kind == 'piper' else 'a speech model'} {step}…")
        say(f"Downloading {kind} {name} {step}")
        fetch = {"piper": fetch_models.fetch_piper, "parakeet": fetch_models.fetch_parakeet}.get(kind, fetch_models.fetch_whisper)
        try:
            code = fetch([name])
        except Exception as e:  # noqa: BLE001 - offline, disk full, ...: carry on without it
            say(f"  could not download {name}: {type(e).__name__}: {e}")
            code = 1
        if code:
            notify("")
            return False
    notify("")
    return not missing()

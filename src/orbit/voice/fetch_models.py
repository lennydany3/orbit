"""One-time model download. This is the ONLY voice code that touches the network, and you run it by hand.

  python -m orbit.voice.fetch_models parakeet parakeet-tdt-0.6b-v2-int8      (the fast speech-to-text, about 640 MB)
  python -m orbit.voice.fetch_models whisper tiny.en base.en small.en
  python -m orbit.voice.fetch_models piper en_US-amy-medium en_US-lessac-medium
  python -m orbit.voice.fetch_models wakeword hey_jarvis alexa hey_mycroft

Models land in models/ and are loaded from there afterwards (no network at run time).
"""
import sys

from . import config

WHISPER_REPOS = {name: f"Systran/faster-whisper-{name}" for name in ("tiny.en", "base.en", "small.en")}
WHISPER_REPOS["distil-small.en"] = "Systran/faster-distil-whisper-small.en"
# sherpa-onnx exports of NVIDIA Parakeet TDT (int8). v2: English only, the most accurate on English. v3: 25 European languages.
PARAKEET_REPOS = {
    "parakeet-tdt-0.6b-v2-int8": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
    "parakeet-tdt-0.6b-v3-int8": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
}


def fetch_whisper(names: list[str]) -> int:
    from huggingface_hub import snapshot_download

    for name in names:
        if name not in WHISPER_REPOS:
            print(f"Unknown Whisper model '{name}'. Choose from: {', '.join(WHISPER_REPOS)}")
            return 2
        target = config.WHISPER_DIR / name
        print(f"Downloading {WHISPER_REPOS[name]} -> {target}")
        snapshot_download(WHISPER_REPOS[name], local_dir=target)
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1e6
        print(f"  done, {size:.0f} MB")
    return 0


def fetch_parakeet(names: list[str]) -> int:
    from huggingface_hub import snapshot_download

    for name in names:
        if name not in PARAKEET_REPOS:
            print(f"Unknown Parakeet model '{name}'. Choose from: {', '.join(PARAKEET_REPOS)}")
            return 2
        target = config.PARAKEET_DIR / name
        print(f"Downloading {PARAKEET_REPOS[name]} -> {target}")
        snapshot_download(PARAKEET_REPOS[name], local_dir=target, allow_patterns=["*.int8.onnx", "tokens.txt"])
        size = sum(f.stat().st_size for f in target.glob("*") if f.is_file()) / 1e6
        print(f"  done, {size:.0f} MB")
    return 0


def fetch_piper(names: list[str]) -> int:
    from piper.download_voices import download_voice

    config.PIPER_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        print(f"Downloading Piper voice {name} -> {config.PIPER_DIR}")
        download_voice(name, config.PIPER_DIR)
        size = sum(f.stat().st_size for f in config.PIPER_DIR.glob(f"{name}.onnx*")) / 1e6
        print(f"  done, {size:.0f} MB")
    return 0


def fetch_wakeword(names: list[str]) -> int:
    """Pre-trained openWakeWord words plus the two shared feature models they all need."""
    from openwakeword.utils import download_models

    config.WAKEWORD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading openWakeWord feature models and {', '.join(names)} -> {config.WAKEWORD_DIR}")
    download_models(model_names=names, target_directory=str(config.WAKEWORD_DIR))
    for f in sorted(config.WAKEWORD_DIR.iterdir()):
        print(f"  {f.name}  {f.stat().st_size / 1e6:.2f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) >= 2 and args[0] == "whisper":
        return fetch_whisper(args[1:])
    if len(args) >= 2 and args[0] == "parakeet":
        return fetch_parakeet(args[1:])
    if len(args) >= 2 and args[0] == "piper":
        return fetch_piper(args[1:])
    if len(args) >= 2 and args[0] == "wakeword":
        return fetch_wakeword(args[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

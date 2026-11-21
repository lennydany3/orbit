"""The microphones and speakers on this PC, as JSON, for the window's Settings page:

    python -m orbit.voice.devices
    {"inputs": ["Microphone (USB Audio)", ...], "outputs": ["Speakers (Realtek Audio)", ...],
     "default_input": "Microphone (USB Audio)", "default_output": "Speakers (Realtek Audio)"}

Each device is listed once, by the name Orbit opens it with (Windows lists the same device once per audio API; the default API is the
one Orbit uses, see `audio_io.resolve_device`), and the two "sound mapper" entries are left out: choosing the Windows default is what
the page's "System default" is for. This is the only thing the window asks Python for besides the link; it prints nothing else.
"""
from __future__ import annotations

import json
import sys

_MAPPERS = ("microsoft sound mapper", "primary sound capture driver", "primary sound driver")


def device_lists() -> dict:
    import sounddevice as sd

    from .audio_io import default_device

    apis = sd.query_hostapis()
    current = apis[sd.default.hostapi]["name"]
    out: dict = {"inputs": [], "outputs": []}
    for index, d in enumerate(sd.query_devices()):
        if apis[d["hostapi"]]["name"] != current or d["name"].strip().lower().startswith(_MAPPERS):
            continue
        for key, channels in (("inputs", d["max_input_channels"]), ("outputs", d["max_output_channels"])):
            if channels > 0 and d["name"] not in out[key]:
                out[key].append(d["name"])
    for kind, key in (("input", "default_input"), ("output", "default_output")):
        index = default_device(kind)
        out[key] = sd.query_devices(index)["name"] if index is not None else ""
    return out


def main() -> int:
    try:
        print(json.dumps(device_lists(), ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 - no sound system: an empty list, and the reason on stderr
        print(json.dumps({"inputs": [], "outputs": [], "default_input": "", "default_output": ""}))
        print(f"could not list the audio devices: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())

"""Voice tools. For now (step V1) this is the audio test bench; it becomes the voice loop later (python -m orbit --voice).

  python -m orbit.voice.run devices                 list microphones and speakers
  python -m orbit.voice.run beep [--speaker X]      play a short tone (checks playback)
  python -m orbit.voice.run vad [--seconds 30]      live mic: print speech start/end, then a CPU/RAM/timing report
Ctrl+C stops `vad` early and still prints the report.
"""
import argparse
import sys
import time

from . import config
from .audio_io import MicStream, Speaker, device_label, list_devices, resolve_device
from .monitor import Monitor, lower_priority
from .vad import SileroVAD, SpeechSegmenter


def cmd_devices(_args) -> int:
    from .audio_io import default_device

    devices = list_devices()
    for kind, key in (("input", "inputs"), ("output", "outputs")):
        default = default_device(kind)
        print(f"\n{kind.upper()} devices (the same device appears once per Windows audio API; MME is the default):")
        for d in devices:
            if d[key] > 0:
                mark = "  <- default" if d["index"] == default else ""
                print(f"  [{d['index']:>2}] {d['name']}  ({d['hostapi']}, {d['rate']:.0f} Hz){mark}")
    print("\nPick one with --device <index or name part> or the ORBIT_MIC / ORBIT_SPEAKER environment variable.")
    return 0


def cmd_beep(args) -> int:
    speaker = Speaker(args.speaker)
    print(f"Playing a tone on {device_label(speaker.device)} ...")
    speaker.beep()
    return 0


def cmd_vad(args) -> int:
    monitor = Monitor()
    print(f"Priority lowered: {lower_priority()}")
    vad = SileroVAD()
    segmenter = SpeechSegmenter(vad)
    mic = MicStream(args.device)
    print(f"Mic: {device_label(mic.device)}  |  {config.SAMPLE_RATE} Hz mono, {config.FRAME_MS} ms frames")
    print(f"Listening for {args.seconds:.0f}s. Speak; stay quiet for a while too (that gives the idle CPU number). Ctrl+C to stop.\n")

    monitor.start()
    started = time.perf_counter()
    speech_started_at = 0.0
    try:
        with mic:
            while time.perf_counter() - started < args.seconds:
                frame = mic.read(timeout=1.0)
                if frame is None:
                    print("  (no audio from the mic for 1 s: is it muted or unplugged?)")
                    continue
                t0 = time.perf_counter()
                events = segmenter.feed(frame)
                busy = time.perf_counter() - t0   # time to run the VAD on this frame: must stay far below the 80 ms the frame lasts
                monitor.add_timing("vad per 80ms frame (speaking)" if segmenter.speaking else "vad per 80ms frame (idle)", busy)
                wall = time.perf_counter() - started
                for e in events:
                    if e.kind == "start":
                        speech_started_at = wall
                        print(f"[{wall:6.2f}s] speech START   (audio clock {e.t:.2f}s)")
                    elif e.kind == "end":
                        print(f"[{wall:6.2f}s] speech END     {e.speech_s:.2f}s of speech, clip {len(e.clip) / config.SAMPLE_RATE:.2f}s, "
                              f"ended by {e.reason}; end-of-speech wait {config.VAD_END_SILENCE_S:.1f}s is built in")
                    else:
                        print(f"[{wall:6.2f}s] ignored        only {e.speech_s:.2f}s of speech (< {config.VAD_MIN_SPEECH_S}s)")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        monitor.stop()

    print("\n--- report ---")
    print(f"frames read: {mic.frames}   mic overflows (driver lost audio): {mic.overflows}   dropped (we were too slow): {mic.dropped}")
    print(monitor.report())
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m orbit.voice.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices").set_defaults(fn=cmd_devices)
    beep = sub.add_parser("beep")
    beep.add_argument("--speaker", default=config.SPEAKER_DEVICE)
    beep.set_defaults(fn=cmd_beep)
    vad = sub.add_parser("vad")
    vad.add_argument("--device", default=config.MIC_DEVICE, help="mic index or part of its name")
    vad.add_argument("--seconds", type=float, default=30.0)
    vad.set_defaults(fn=cmd_vad)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

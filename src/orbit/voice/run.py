"""Voice tools. For now (steps V1-V2) this is the audio test bench; it becomes the voice loop later (python -m orbit --voice).

  python -m orbit.voice.run devices                  list microphones and speakers
  python -m orbit.voice.run beep                     play a short tone (checks playback)
  python -m orbit.voice.run vad [--seconds 30]       live mic: print speech start/end, then a CPU/RAM/timing report
  python -m orbit.voice.run record                   record the test sentences into tests/voice/fixtures (your voice, local only)
  python -m orbit.voice.run stt [--seconds 60]       live mic -> text, with timings; add --stress to keep Whisper busy the whole time
  python -m orbit.voice.run bench                    compare tiny.en / base.en / small.en on the recorded clips
  python -m orbit.voice.run say ["text"]             speak a text (Piper; --engine windows for the fallback voice)
  python -m orbit.voice.run voices                   audition every downloaded Piper voice with the same sentence
Ctrl+C stops a live command early and still prints its report.
"""
import argparse
import gc
import itertools
import statistics
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import psutil

from . import config
from .audio_io import MicStream, Speaker, device_label, list_devices
from .monitor import Monitor, lower_priority
from .stt import Transcriber, word_error_rate
from .vad import SileroVAD, SpeechSegmenter


# ---------- helpers ----------
def read_sentences(directory: Path) -> list[tuple[str, str]]:
    path = directory / "sentences.txt"
    if not path.exists():
        path = config.FIXTURES_DIR / "sentences.txt"   # other folders (tests/voice/fixtures_raw ...) share the same sentence list
    if not path.exists():
        return []
    return [tuple(line.split("|", 1)) for line in path.read_text(encoding="utf-8").splitlines() if "|" in line]


def write_wav(path: Path, clip: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.SAMPLE_RATE)
        w.writeframes(clip.astype(np.int16).tobytes())


def load_fixtures(directory: Path) -> list[tuple[str, str, np.ndarray]]:
    """(name, expected text, int16 clip) for every sentence that has a recording."""
    found = []
    for name, text in read_sentences(directory):
        path = directory / f"{name}.wav"
        if path.exists():
            with wave.open(str(path)) as w:
                found.append((name, text, np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)))
    return found


# ---------- V1 ----------
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
    segmenter = SpeechSegmenter(SileroVAD())
    mic = MicStream(args.device)
    print(f"Mic: {device_label(mic.device)}  |  {config.SAMPLE_RATE} Hz mono, {config.FRAME_MS} ms frames")
    print(f"Listening for {args.seconds:.0f}s. Speak; stay quiet for a while too (that gives the idle CPU number). Ctrl+C to stop.\n")

    monitor.start()
    started = time.perf_counter()
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


# ---------- V2 ----------
def cmd_record(args) -> int:
    """Record each test sentence once, cut by the same VAD the real loop uses. Saved only here, only by you running this."""
    directory = Path(args.fixtures)
    sentences = read_sentences(directory)
    if not sentences:
        print(f"No sentences found in {directory / 'sentences.txt'}")
        return 1
    directory.mkdir(parents=True, exist_ok=True)
    todo = [(n, t) for n, t in sentences if args.redo or not (directory / f"{n}.wav").exists()]
    if not todo:
        print("Every sentence already has a recording. Use --redo to record them again.")
        return 0
    segmenter = SpeechSegmenter(SileroVAD())
    mic = MicStream(args.device)
    print(f"Mic: {device_label(mic.device)}")
    print(f"Recording {len(todo)} sentences into {directory} (this is the only place audio is saved). Speak each one naturally, then pause.")
    with mic:
        for name, text in todo:
            print(f'\nSay:  "{text}"')
            mic.pause()
            mic.resume()          # throw away anything heard while the prompt was printed
            segmenter.reset()
            while True:
                frame = mic.read(timeout=1.0)
                if frame is None:
                    continue
                done = [e for e in segmenter.feed(frame) if e.kind in ("end", "ignored")]
                if done and done[0].kind == "end":
                    write_wav(directory / f"{name}.wav", done[0].clip)
                    print(f"  saved {name}.wav ({len(done[0].clip) / config.SAMPLE_RATE:.1f}s)")
                    break
                if done:
                    print("  too short, try again")
    print("\nDone. Now run: python -m orbit.voice.run bench")
    return 0


def cmd_stt(args) -> int:
    monitor = Monitor()
    print(f"Priority lowered: {lower_priority()}")
    segmenter = SpeechSegmenter(SileroVAD())
    transcriber = Transcriber(args.model, threads=args.threads)
    print(f"Whisper {transcriber.model_name}: loaded + warmed up in {transcriber.load_s:.1f}s, {transcriber.threads} threads, CPU int8 (GPU untouched)")
    mic = MicStream(args.device)
    print(f"Mic: {device_label(mic.device)}")

    pending: list = []
    stop_stress = threading.Event()

    def report(future, speech_end_at: float) -> None:
        try:
            result = future.result()
        except Exception as e:  # keep the loop alive if one clip fails
            print(f"  STT failed: {e}")
            return
        monitor.add_timing("stt transcribe", result.seconds)
        monitor.add_timing("speech-end event -> text", time.perf_counter() - speech_end_at)
        print(f'  heard: "{result.text}"   [{result.seconds:.2f}s for {result.audio_s:.1f}s of audio, '
              f"{result.realtime_factor:.2f}x real time]")

    if args.stress:
        clips = [c for _, _, c in load_fixtures(Path(args.fixtures))]
        if not clips:
            print("--stress needs recorded clips (run: python -m orbit.voice.run record)")
            return 1

        def keep_whisper_busy() -> None:
            for clip in itertools.cycle(clips):
                if stop_stress.is_set():
                    return
                result = transcriber.transcribe(clip)
                monitor.add_timing("stt transcribe (under stress)", result.seconds)

        threading.Thread(target=keep_whisper_busy, name="stress", daemon=True).start()
        print("STRESS: Whisper is transcribing recorded clips back to back while the mic is read. Gaps should stay near 80 ms.")
    print(f"\nListening for {args.seconds:.0f}s. Speak a sentence, then pause. Ctrl+C to stop.\n")

    monitor.start()
    started, last = time.perf_counter(), None   # the first gap is skipped: it would include the time the mic takes to open
    try:
        with mic:
            while time.perf_counter() - started < args.seconds:
                frame = mic.read(timeout=1.0)
                now = time.perf_counter()
                if frame is None:
                    print("  (no audio from the mic for 1 s)")
                    continue
                busy = args.stress or any(not f.done() for f in pending)
                if last is not None:
                    monitor.add_timing("mic frame gap (Whisper busy)" if busy else "mic frame gap (Whisper idle)", now - last)
                last = now
                for e in segmenter.feed(frame):
                    if e.kind == "start":
                        print(f"[{now - started:6.2f}s] speech start")
                    elif e.kind == "end":
                        print(f"[{now - started:6.2f}s] speech end ({e.speech_s:.1f}s) -> transcribing")
                        future = transcriber.submit(e.clip)
                        pending.append(future)
                        future.add_done_callback(lambda f, t=now: report(f, t))
                    else:
                        print(f"[{now - started:6.2f}s] ignored (too short)")
            for f in pending:
                f.result(timeout=30)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop_stress.set()
        monitor.stop()
        transcriber.close()

    print("\n--- report ---")
    print(f"frames read: {mic.frames}   mic overflows (driver lost audio): {mic.overflows}   dropped (we were too slow): {mic.dropped}")
    print(monitor.report())
    print("Note: 'speech-end event -> text' starts at the END event, which already waited "
          f"{config.VAD_END_SILENCE_S:.1f}s of silence after you stopped talking.")
    return 0


def cmd_bench(args) -> int:
    """Same clips, each model: accuracy vs time vs CPU vs RAM."""
    clips = load_fixtures(Path(args.fixtures))
    if not clips:
        print(f"No recorded clips in {args.fixtures}. Run: python -m orbit.voice.run record")
        return 1
    print(f"Priority lowered: {lower_priority()}")
    total_audio = sum(len(c) for _, _, c in clips) / config.SAMPLE_RATE
    print(f"{len(clips)} clips, {total_audio:.1f}s of audio, {args.repeats} runs each, {args.threads} threads, CPU int8, beam {config.WHISPER_BEAM}\n")
    proc, ncpu = psutil.Process(), psutil.cpu_count(logical=True) or 1
    monitor = Monitor()
    rows, mistakes = [], {}
    for model in args.models:
        gc.collect()
        rss_before = proc.memory_info().rss
        try:
            transcriber = Transcriber(model, threads=args.threads, prompt="" if args.no_prompt else config.WHISPER_PROMPT)
        except FileNotFoundError as e:
            print(e)
            return 1
        ram_mb = (proc.memory_info().rss - rss_before) / 1e6
        times, errors, per_clip = [], [], []
        for name, expected, clip in clips:
            runs = []
            for _ in range(args.repeats):
                with monitor.stage(model):
                    result = transcriber.transcribe(clip)
                runs.append(result.seconds)
            times.append(statistics.median(runs))
            errors.append(word_error_rate(expected, result.text))
            per_clip.append((expected, result.text, errors[-1]))
        cpu = statistics.fmean(monitor.stage_cpu[model])
        rows.append((model, transcriber.load_s, ram_mb, statistics.median(times), max(times),
                     sum(times) / total_audio, cpu, cpu * ncpu / 100, 100 * statistics.fmean(errors)))
        mistakes[model] = [(e, h) for e, h, w in per_clip if w > 0]
        transcriber.close()
        del transcriber
        gc.collect()

    print(f"{'model':8} {'load s':>7} {'RAM MB':>7} {'median s':>9} {'max s':>7} {'x realtime':>11} {'CPU %':>6} {'cores':>6} {'WER %':>6}")
    for model, load, ram, med, mx, rtf, cpu, cores, wer in rows:
        print(f"{model:8} {load:7.1f} {ram:7.0f} {med:9.2f} {mx:7.2f} {rtf:11.2f} {cpu:6.1f} {cores:6.1f} {wer:6.1f}")
    print("\nmedian s / max s = seconds to transcribe one clip.  x realtime = compute seconds per audio second (lower is faster).")
    print(f"CPU % = share of the whole machine ({ncpu} logical cores) while transcribing; cores = same thing as busy cores.")
    print("WER % = word error rate against what you were asked to say (0 = perfect).  RAM = extra memory after loading (approximate).")
    for model, wrong in mistakes.items():
        if wrong:
            print(f"\n{model} mistakes:")
            for expected, heard in wrong:
                print(f'  said "{expected}"  ->  heard "{heard}"')
    return 0


# ---------- V3 ----------
SAMPLE_REPLY = ("Okay, I opened Spotify and set the volume to thirty percent. "
                "Your music is playing on the Echo Pop speaker. Anything else?")


def _stats_line(stats) -> str:
    parts = [f"engine {stats.engine}", f"total {stats.total_s:.2f}s"]
    if stats.first_audio_s is not None:
        parts.append(f"time to first audio {stats.first_audio_s * 1000:.0f} ms")
    if stats.synth_realtime_factor is not None:
        parts.append(f"synthesis {stats.synth_s:.2f}s for {stats.audio_s:.1f}s of speech ({stats.synth_realtime_factor:.2f}x real time)")
    if stats.interrupted:
        parts.append("interrupted")
    return "  " + ", ".join(parts)


def cmd_say(args) -> int:
    """Speak a text with the configured voice (Piper, falling back to the Windows voice)."""
    from .tts import FallbackTTS, WindowsTTS, make_tts

    monitor = Monitor()
    print(f"Priority lowered: {lower_priority()}")
    tts = FallbackTTS([WindowsTTS()]) if args.engine == "windows" else make_tts(args.voice, args.speaker)
    text = " ".join(args.text) or SAMPLE_REPLY
    monitor.start()
    for _ in range(args.repeat):
        with monitor.stage("speak (synthesis + playback)"):
            stats = tts.speak(text)
        print(_stats_line(stats))
        if stats.first_audio_s is not None:
            monitor.add_timing("time to first audio", stats.first_audio_s)
    monitor.stop()
    print("\n--- report ---")
    print(monitor.report())
    return 0


def cmd_voices(args) -> int:
    """Audition every downloaded Piper voice with the same sentence, one after another."""
    from .tts import PiperTTS

    voices = sorted(p.name.removesuffix(".onnx") for p in config.PIPER_DIR.glob("*.onnx"))
    if not voices:
        print(f"No Piper voices in {config.PIPER_DIR}. Download with: python -m orbit.voice.fetch_models piper en_US-amy-medium")
        return 1
    print(f"Priority lowered: {lower_priority()}")
    print(f'Each voice says: "{SAMPLE_REPLY}"\n')
    for name in voices:
        tts = PiperTTS(name, args.speaker)
        print(f"{name}{'   <- current default' if name == config.PIPER_VOICE else ''}")
        print(_stats_line(tts.speak(SAMPLE_REPLY)))
        time.sleep(0.8)
    print(f"\nTo choose one, set PIPER_VOICE in src/orbit/voice/config.py (or tell me which).")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m orbit.voice.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, fn, mic=False, fixtures=False, model=False):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if mic:
            p.add_argument("--device", default=config.MIC_DEVICE, help="mic index or part of its name")
        if fixtures:
            p.add_argument("--fixtures", default=str(config.FIXTURES_DIR))
        if model:
            p.add_argument("--threads", type=int, default=config.WHISPER_THREADS)
        return p

    add("devices", cmd_devices)
    add("beep", cmd_beep).add_argument("--speaker", default=config.SPEAKER_DEVICE)
    add("vad", cmd_vad, mic=True).add_argument("--seconds", type=float, default=30.0)
    add("record", cmd_record, mic=True, fixtures=True).add_argument("--redo", action="store_true", help="record every sentence again")
    stt = add("stt", cmd_stt, mic=True, fixtures=True, model=True)
    stt.add_argument("--seconds", type=float, default=60.0)
    stt.add_argument("--model", default=config.WHISPER_MODEL)
    stt.add_argument("--stress", action="store_true", help="keep Whisper busy the whole time to test the mic for glitches")
    bench = add("bench", cmd_bench, fixtures=True, model=True)
    bench.add_argument("--models", nargs="+", default=["tiny.en", "base.en", "small.en"])
    bench.add_argument("--repeats", type=int, default=3)
    bench.add_argument("--no-prompt", action="store_true", help="turn the vocabulary hint off, to measure its effect")
    say = add("say", cmd_say)
    say.add_argument("text", nargs="*", help="what to say (default: a sample reply)")
    say.add_argument("--voice", default=config.PIPER_VOICE)
    say.add_argument("--speaker", default=config.SPEAKER_DEVICE)
    say.add_argument("--engine", choices=["piper", "windows"], default="piper")
    say.add_argument("--repeat", type=int, default=1)
    add("voices", cmd_voices).add_argument("--speaker", default=config.SPEAKER_DEVICE)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

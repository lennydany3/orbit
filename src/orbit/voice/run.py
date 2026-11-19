"""Voice tools. For now (steps V1-V2) this is the audio test bench; it becomes the voice loop later (python -m orbit --voice).

  python -m orbit.voice.run devices                  list microphones and speakers
  python -m orbit.voice.run beep                     play a short tone (checks playback)
  python -m orbit.voice.run vad [--seconds 30]       live mic: print speech start/end, then a CPU/RAM/timing report
  python -m orbit.voice.run record                   record the test sentences into tests/voice/fixtures (your voice, local only)
  python -m orbit.voice.run stt [--seconds 60]       live mic -> text, with timings; add --stress to keep Whisper busy the whole time
  python -m orbit.voice.run bench                    compare tiny.en / base.en / small.en on the recorded clips
  python -m orbit.voice.run say ["text"]             speak a text (Piper; --engine windows for the fallback voice)
  python -m orbit.voice.run voices                   audition every downloaded Piper voice with the same sentence
  python -m orbit.voice.run keys                     test the hold-to-talk key and the kill switch (no audio)
  python -m orbit.voice.run wake [--seconds 120]     wake word only: idle CPU, false accepts / rejects
  python -m orbit.voice.run listen                   hold Right Ctrl or say the wake word -> text
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
from .audio_io import FileMic, MicStream, Speaker, device_label, list_devices
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


def make_transcriber(args) -> Transcriber:
    """Local Whisper, loaded once. Speech-to-text never leaves this PC."""
    transcriber = Transcriber(args.model, threads=args.threads)
    print(f"Speech-to-text: local Whisper {transcriber.model_name}, {transcriber.threads} threads, CPU int8 "
          f"(loaded in {transcriber.load_s:.1f}s). Audio never leaves this PC.")
    return transcriber


def heard_line(result, timing: str, how: str | None = None) -> str:
    """One CLI line per turn."""
    tag = f"({how}) " if how else ""
    if not result.text.strip():
        return f"  heard {tag}: (empty transcript: the agent will not run)"
    return f'  heard {tag}: "{result.text}"   [STT {result.seconds:.2f}s for {result.audio_s:.1f}s of audio; {timing}]'


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
    transcriber = make_transcriber(args)
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
        print(heard_line(result, f"{time.perf_counter() - speech_end_at:.2f}s from the end-of-speech event"))

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


# ---------- custom wake word ----------
def cmd_train_wake(args) -> int:
    from .wake_train import train

    return train(args.pos, args.neg, args.seed, args.folds, args.fresh)


def cmd_record_wake(args) -> int:
    """Record yourself saying "hey orbit" (positives) and normal talking (negatives) for training. Saved locally, git-ignored."""
    directory = config.FIXTURES_DIR.parent / "fixtures_wake"
    directory.mkdir(parents=True, exist_ok=True)
    segmenter = SpeechSegmenter(SileroVAD())
    mic = MicStream(args.device)
    print(f"Mic: {device_label(mic.device)}\nSaving into {directory} (local only; these are the only audio files this saves).")
    styles = ["normal voice", "a bit quieter", "a bit faster", "slowly and clearly", "from further away from the mic",
              "casually, like calling a friend", "a bit louder", "like you are tired"]
    with mic:
        have = len(list(directory.glob("hey_orbit_*.wav")))
        for i in range(args.count):
            print(f'\n[{i + 1}/{args.count}] Say "hey orbit"  ({styles[i % len(styles)]})')
            mic.pause()
            mic.resume()
            segmenter.reset()
            while True:
                frame = mic.read(timeout=1.0)
                if frame is None:
                    continue
                done = [e for e in segmenter.feed(frame) if e.kind in ("end", "ignored")]
                if done and done[0].kind == "end":
                    have += 1
                    write_wav(directory / f"hey_orbit_{have:03d}.wav", done[0].clip)
                    print(f"  saved hey_orbit_{have:03d}.wav ({len(done[0].clip) / config.SAMPLE_RATE:.1f}s)")
                    break
                if done:
                    print("  too short, try again")
        if args.talk:
            n = len(list(directory.glob("negative_*.wav"))) + 1
            print(f"\nNow talk normally for {args.talk:.0f}s (or play a YouTube video / TV / music). Do NOT say \"hey orbit\". Starting in 3s ...")
            time.sleep(3)
            mic.pause()
            mic.resume()
            frames, started = [], time.perf_counter()
            while time.perf_counter() - started < args.talk:
                frame = mic.read(timeout=1.0)
                if frame is not None:
                    frames.append(frame)
            write_wav(directory / f"negative_{n:03d}.wav", np.concatenate(frames))
            print(f"  saved negative_{n:03d}.wav ({len(frames) * config.FRAME_MS / 1000:.0f}s)")
    print("\nDone. Train with:  python -m orbit.voice.run train-wake")
    return 0


# ---------- V4 ----------
def cmd_keys(args) -> int:
    """Test the global hotkeys without any audio."""
    from .hotkey import Hotkeys

    keys = Hotkeys(args.ptt)
    keys.start()
    print(f"Hold {args.ptt} to test hold-to-talk. A quick tap, or a shortcut like {args.ptt}+C, is reported as ignored.")
    print(f"Press {config.KILL_COMBO} (the kill switch) to quit. It should work from any window.\n")
    try:
        while not keys.killed.is_set():
            for e in keys.poll():
                if e.kind == "down":
                    print("  key DOWN  -> would start listening")
                elif e.kind == "up":
                    print(f"  key UP    held {e.held_s:.2f}s -> " + ("a real hold-to-talk" if e.valid else "ignored (tap or shortcut)"))
                else:
                    print("  KILL SWITCH pressed -> would stop the microphone")
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    keys.stop()
    return 0


def cmd_wake(args) -> int:
    """Wake word only (no speech-to-text): for the idle CPU number and false accepts / rejects."""
    from .wakeword import WakeWord

    monitor = Monitor()
    print(f"Priority lowered: {lower_priority()}")
    wake = WakeWord(args.word, args.threshold)
    mic = MicStream(args.device)
    print(f"Mic: {device_label(mic.device)}")
    print(f'Wake word "{args.word.replace("_", " ")}", threshold {wake.threshold}, needs {wake.hits} frames in a row. '
          f"Listening {args.seconds:.0f}s: talk, play music or TV, and say the wake word a few times. Ctrl+C to stop.\n")
    triggers, near_misses, top, rejected, last_event = [], 0, 0.0, 0, -10.0
    monitor.start()
    started = time.perf_counter()
    try:
        with mic:
            while time.perf_counter() - started < args.seconds:
                frame = mic.read(timeout=1.0)
                if frame is None:
                    continue
                t0 = time.perf_counter()
                result = wake.process(frame)
                monitor.add_timing("wake word per 80ms frame", time.perf_counter() - t0)
                wall = time.perf_counter() - started
                top = max(top, result.score)
                if result.triggered or result.rejected:
                    last_event = wall
                if result.triggered:
                    triggers.append((wall, result.score))
                    print(f"[{wall:6.1f}s] WAKE  score {result.score:.2f}" + (f'   (Whisper check heard: "{result.heard}")' if result.heard else ""))
                elif result.rejected:
                    rejected += 1
                    print(f"[{wall:6.1f}s]   candidate REJECTED by the Whisper check (score {result.score:.2f}, heard: \"{result.heard}\")")
                elif 0.25 <= result.score < wake.threshold and wall - last_event > 2.0:   # not the tail of a word that just woke it
                    near_misses += 1
                    print(f"[{wall:6.1f}s]   near miss, score {result.score:.2f} (below {wake.threshold})")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        monitor.stop()
    elapsed = time.perf_counter() - started
    print("\n--- report ---")
    print(f"{len(triggers)} wakes in {elapsed / 60:.1f} min ({len(triggers) / max(elapsed / 60, 1e-9):.1f} per minute); "
          f"{near_misses} near misses; highest score {top:.2f}" + (f"; {rejected} candidates rejected by the Whisper check" if wake.verifier else ""))
    print(f"mic overflows: {mic.overflows}   dropped: {mic.dropped}")
    print(monitor.report())
    return 0


def cmd_listen(args) -> int:
    """Both triggers end to end (hold the key, or say the wake word) -> text. The agent is hooked up in V5."""
    from collections import deque

    from .hotkey import Hotkeys
    from .vad import speech_seconds
    from .wakeword import WakeWord, strip_wake_phrase

    monitor = Monitor()
    print(f"Priority lowered: {lower_priority()}")
    segmenter = SpeechSegmenter(SileroVAD())     # cuts the utterance after a wake word
    check_vad = SileroVAD()                      # checks that a hold-to-talk recording really contains speech
    wake = None if args.no_wake else WakeWord(args.word)
    transcriber = make_transcriber(args)
    keys = Hotkeys(args.ptt)
    mic = FileMic(args.wav) if args.wav else MicStream(args.device)
    print(f"Mic: {getattr(mic, 'label', None) or device_label(mic.device)}")
    triggers = []
    if not args.no_ptt:
        triggers.append(f"hold {args.ptt}")
    if wake:
        triggers.append(f'say "{args.word.replace("_", " ")}"')
    print(f"Start a turn: {' or '.join(triggers)}.  Kill switch: {config.KILL_COMBO}.  Ctrl+C also quits.\n")

    pending: list = []
    counts = {"ptt": 0, "wake": 0}

    def submit(clip: np.ndarray, ended_at: float, how: str) -> None:
        counts[how] += 1
        print("[transcribing]")
        future = transcriber.submit(clip)
        pending.append(future)

        def show(f) -> None:
            try:
                result = f.result()
            except Exception as e:
                print(f"  STT failed: {e}")
                return
            if how == "wake":
                result.text = strip_wake_phrase(result.text, args.word)
            total = time.perf_counter() - ended_at
            monitor.add_timing(f"{how}: transcribe", result.seconds)
            monitor.add_timing(f"{how}: {'key release' if how == 'ptt' else 'speech-end event'} -> text", total)
            print(heard_line(result, f"{total:.2f}s from "
                  f"{'key release' if how == 'ptt' else 'the end-of-speech event, which already waited %.1fs of silence' % config.VAD_END_SILENCE_S}",
                  how))

        future.add_done_callback(show)

    state, preroll, ptt_frames, wake_at, heard = "idle", deque(maxlen=4), [], 0.0, False
    keys.start()
    monitor.start()
    started = time.perf_counter()
    try:
        with mic:
            while time.perf_counter() - started < args.seconds:
                frame = mic.read(timeout=0.5)
                now = time.perf_counter()
                for e in keys.poll():
                    if e.kind == "kill":
                        print("[killed] kill switch pressed: microphone stopped")
                        raise SystemExit(0)
                    if args.no_ptt:
                        continue
                    if e.kind == "down":
                        state, ptt_frames = "ptt", list(preroll)
                        print("[listening] hold-to-talk: speak, then release the key")
                    elif e.kind == "up" and state == "ptt":
                        state = "idle"
                        if not e.valid:
                            print(f"  (held {e.held_s:.2f}s: a tap or shortcut, not talking)")
                        elif not ptt_frames:
                            print("  (no audio)")
                        else:
                            clip = np.concatenate(ptt_frames)
                            speech = speech_seconds(check_vad, clip)
                            if speech < config.VAD_MIN_SPEECH_S:
                                print(f"  (no speech heard in {len(clip) / config.SAMPLE_RATE:.1f}s of audio)")
                            else:
                                submit(clip, now, "ptt")
                if frame is None:
                    continue
                if state == "ptt":
                    ptt_frames.append(frame)
                    if len(ptt_frames) * config.FRAME_MS / 1000 >= config.PTT_MAX_S:
                        print(f"  (reached the {config.PTT_MAX_S:.0f}s limit)")
                        state = "idle"
                        submit(np.concatenate(ptt_frames), now, "ptt")
                elif state == "wake":
                    for e in segmenter.feed(frame):
                        if e.kind == "start":
                            heard = True
                            print("[listening] speech detected")
                        elif e.kind == "end":
                            state = "idle"
                            wake.reset()
                            submit(e.clip, now, "wake")
                    if state == "wake" and not heard and now - wake_at > config.WAKE_NO_SPEECH_S:
                        print(f"  (nothing heard for {config.WAKE_NO_SPEECH_S:.1f}s after the wake word, going back to sleep)")
                        state = "idle"
                        wake.reset()
                else:
                    preroll.append(frame)
                    if wake:
                        t0 = time.perf_counter()
                        result = wake.process(frame)
                        monitor.add_timing("wake word per 80ms frame", time.perf_counter() - t0)
                        if result.rejected:
                            print(f'  (wake candidate rejected: the check heard "{result.heard}")')
                        if result.triggered:
                            state, wake_at, heard = "wake", now, False
                            segmenter.reset()
                            print(f"[wake] \"{args.word.replace('_', ' ')}\" (score {result.score:.2f}) -> listening")
            for f in pending:
                f.result(timeout=30)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        keys.stop()
        monitor.stop()
        transcriber.close()
    print("\n--- report ---")
    print(f"turns: {counts['ptt']} hold-to-talk, {counts['wake']} wake word   |   mic overflows {mic.overflows}, dropped {mic.dropped}")
    print(monitor.report())
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
    stt.add_argument("--stress", action="store_true", help="keep local Whisper busy the whole time to test the mic for glitches")
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
    add("keys", cmd_keys).add_argument("--ptt", default=config.PTT_KEY, help="hold-to-talk key (pynput name, e.g. ctrl_r, f9)")
    wk = add("wake", cmd_wake, mic=True)
    wk.add_argument("--word", default=config.WAKEWORD_MODEL, help="hey_orbit (custom) | hey_mycroft | hey_jarvis | alexa")
    wk.add_argument("--threshold", type=float, default=None, help="default: the one chosen when the word was trained")
    wk.add_argument("--seconds", type=float, default=120.0)
    ls = add("listen", cmd_listen, mic=True, model=True)
    ls.add_argument("--model", default=config.WHISPER_MODEL)
    ls.add_argument("--word", default=config.WAKEWORD_MODEL)
    ls.add_argument("--ptt", default=config.PTT_KEY)
    ls.add_argument("--seconds", type=float, default=300.0)
    ls.add_argument("--no-wake", action="store_true", help="hold-to-talk only")
    ls.add_argument("--no-ptt", action="store_true", help="wake word only")
    ls.add_argument("--wav", help="test mode: replay this 16 kHz mono WAV in real time instead of using the microphone")
    tw = add("train-wake", cmd_train_wake)
    tw.add_argument("--pos", type=int, default=60, help="synthetic 'hey orbit' clips per voice")
    tw.add_argument("--neg", type=int, default=170, help="synthetic other-speech clips per voice")
    tw.add_argument("--seed", type=int, default=1)
    tw.add_argument("--folds", type=int, default=5, help="hold-out folds for testing on your own recordings")
    tw.add_argument("--fresh", action="store_true", help="regenerate the cached synthetic training data")
    rw = add("record-wake", cmd_record_wake, mic=True)
    rw.add_argument("--count", type=int, default=25, help="how many times you say 'hey orbit'")
    rw.add_argument("--talk", type=float, default=0.0, help="also record this many seconds of normal talking / TV as negatives")

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

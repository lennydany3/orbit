"""Microphone capture and speaker playback (sounddevice / PortAudio).

The mic callback does the bare minimum (copy the block into a queue) so it never blocks; everything else
(VAD, Whisper, ...) runs on the consumer side. Two counters tell you if audio was ever lost:
  overflows - PortAudio says the driver dropped audio (the OS could not deliver it in time)
  dropped   - our queue was full because the consumer was too slow
"""
import queue
import threading
import time

import numpy as np
import sounddevice as sd

from . import config


def list_devices() -> list[dict]:
    apis = sd.query_hostapis()
    return [
        {"index": i, "name": d["name"], "hostapi": apis[d["hostapi"]]["name"],
         "inputs": d["max_input_channels"], "outputs": d["max_output_channels"], "rate": d["default_samplerate"]}
        for i, d in enumerate(sd.query_devices())
    ]


def default_device(kind: str) -> int | None:
    """Index of the Windows default input/output device, or None if there is none."""
    index = sd.default.device[0 if kind == "input" else 1]
    return None if index is None or index < 0 else int(index)


def resolve_device(spec: str | int | None, kind: str) -> int | None:
    """spec = None (default device), an index, or part of a name. Raises ValueError with the options if nothing matches."""
    if spec is None or spec == "":
        return default_device(kind)
    key = "inputs" if kind == "input" else "outputs"
    devices = [d for d in list_devices() if d[key] > 0]
    if str(spec).strip().isdigit():
        index = int(spec)
        if any(d["index"] == index for d in devices):
            return index
        raise ValueError(f"No {kind} device with index {index}. Run: python -m orbit.voice.run devices")
    matches = [d for d in devices if str(spec).lower() in d["name"].lower()]
    if not matches:
        raise ValueError(f"No {kind} device whose name contains '{spec}'. Run: python -m orbit.voice.run devices")
    default_api = sd.query_hostapis(sd.default.hostapi)["name"]
    matches.sort(key=lambda d: d["hostapi"] != default_api)   # the same device appears once per host API; prefer the default one
    return matches[0]["index"]


def device_label(index: int | None) -> str:
    if index is None:
        return "(none)"
    d = sd.query_devices(index)
    return f"[{index}] {d['name']} ({sd.query_hostapis(d['hostapi'])['name']})"


class MicStream:
    """16 kHz mono int16 in 80 ms frames. `read()` returns the next frame; pause() discards audio, stop() closes the mic."""

    def __init__(self, device: str | int | None = config.MIC_DEVICE):
        self.device = resolve_device(device, "input")
        self._queue: queue.Queue[tuple[np.ndarray, float]] = queue.Queue(maxsize=config.MIC_QUEUE_FRAMES)
        self._paused = threading.Event()
        self._stream: sd.InputStream | None = None
        self.overflows = 0
        self.dropped = 0
        self.frames = 0

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=config.SAMPLE_RATE, channels=1, dtype="int16", blocksize=config.FRAME_SAMPLES,
            device=self.device, callback=self._on_audio)
        self._stream.start()

    def _on_audio(self, indata, frames, time_info, status) -> None:
        if status.input_overflow:
            self.overflows += 1
        if self._paused.is_set():
            return
        # When this block started being captured, on the time.perf_counter clock: PortAudio's own timestamps are exact,
        # unlike "now" (the callback may run a little late). Echo cancellation lines the microphone up with what was played.
        try:
            t_start = time.perf_counter() - (time_info.currentTime - time_info.inputBufferAdcTime)
        except Exception:  # noqa: BLE001 - some drivers report no timestamps
            t_start = time.perf_counter() - config.FRAME_MS / 1000
        try:
            self._queue.put_nowait((indata[:, 0].copy(), t_start))
        except queue.Full:
            self.dropped += 1

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        """Next 80 ms frame (int16, 1280 samples), or None if nothing arrived within `timeout`."""
        timed = self.read_timed(timeout)
        return None if timed is None else timed[0]

    def read_timed(self, timeout: float = 1.0) -> tuple[np.ndarray, float] | None:
        """Like read(), plus when the frame started being captured (time.perf_counter seconds)."""
        try:
            frame, t_start = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        self.frames += 1
        return frame, t_start

    def pause(self) -> None:
        """Half-duplex: stop collecting audio (e.g. while Orbit speaks) without closing the mic."""
        self._paused.set()
        self._drain()

    def resume(self) -> None:
        self._drain()
        self._paused.clear()

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "MicStream":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


class FileMic:
    """Test stand-in for MicStream: replays a 16 kHz mono WAV in real time (silence after the end), 80 ms per frame."""

    def __init__(self, path):
        import wave

        with wave.open(str(path)) as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (config.SAMPLE_RATE, 1, 2):
                raise ValueError("--wav must be a 16 kHz mono 16-bit WAV")
            self._data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        self.device, self.label = None, f"replaying {path} ({len(self._data) / config.SAMPLE_RATE:.0f}s) instead of a microphone"
        self.overflows = self.dropped = self.frames = 0
        self._pos, self._next = 0, 0.0

    def start(self) -> None:
        import time

        self._next = time.perf_counter()

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        import time

        time.sleep(max(0.0, self._next - time.perf_counter()))
        self._next += config.FRAME_MS / 1000
        frame = np.zeros(config.FRAME_SAMPLES, dtype=np.int16)
        chunk = self._data[self._pos:self._pos + config.FRAME_SAMPLES]
        frame[:len(chunk)] = chunk
        self._pos += config.FRAME_SAMPLES
        self.frames += 1
        return frame

    def read_timed(self, timeout: float = 1.0):
        frame = self.read(timeout)
        return None if frame is None else (frame, time.perf_counter() - config.FRAME_MS / 1000)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def __enter__(self) -> "FileMic":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


class Speaker:
    """Plays float32/int16 mono audio on the chosen output device."""

    def __init__(self, device: str | int | None = config.SPEAKER_DEVICE):
        self.device = resolve_device(device, "output")

    def play(self, audio: np.ndarray, sample_rate: int, blocking: bool = True) -> None:
        sd.play(audio, sample_rate, device=self.device)
        if blocking:
            sd.wait()

    def stop(self) -> None:
        sd.stop()

    def beep(self, freq: float = 880.0, ms: int = 300, volume: float = 0.2) -> None:
        sr = 22_050
        t = np.arange(int(sr * ms / 1000)) / sr
        fade = np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.02)   # 20 ms fade in/out so it does not click
        self.play((volume * fade * np.sin(2 * np.pi * freq * t)).astype(np.float32), sr)

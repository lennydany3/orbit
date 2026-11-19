"""Echo cancellation, so you can say "hey orbit" while Orbit is still talking.

The microphone hears Orbit's own voice coming out of the speakers. Orbit knows exactly what it plays (the "reference"), so an
adaptive filter learns how that sound reaches the microphone (delay, room, speaker) and subtracts it. What is left is you.

  ReferenceTimeline  where the text-to-speech tells us what it is playing, on a clock shared with the microphone
  EchoCanceller      a partitioned block frequency-domain adaptive filter (PBFDAF), 16 kHz, ~0.5 s of echo path

Lessons from real hardware (they shaped this design):
  * Alignment must be sample-continuous. PortAudio's per-frame timestamps jitter by +-15 ms here, and even +-0.5 ms of jitter
    destroys the cancellation. So timestamps are used ONCE per utterance, to fix one constant offset (ReferenceAligner); the
    adaptive filter absorbs any constant error.
  * Some microphones (laptop arrays with their own noise suppression) are not linear, so a linear filter cannot model them and
    would only add noise. The canceller therefore measures its own benefit and passes the microphone through untouched
    whenever it is not clearly helping, and resets itself if it ever diverges.
  * Earbuds and headphones produce no echo at all: then the reference never appears in the microphone and nothing happens.

Honest limits: it removes the part of the sound Orbit KNOWS (its own voice). Music from Spotify and a TV are not in the
reference, so they stay. The speaker's own distortion is not linear, so some residual echo remains; the wake-word model and its
Whisper check are what finally decide whether "hey orbit" was said. Headphones need no echo cancellation at all.
"""
import threading

import numpy as np

SAMPLE_RATE = 16_000


class ReferenceTimeline:
    """What was sent to the speakers, laid out on a time axis (seconds on time.perf_counter, the clock the microphone uses).

    Blocks are written when they are handed to the sound card, which runs a little ahead of real time, so blocks are placed
    back to back (a block never starts before the previous one ended). The constant lag between "handed over" and "heard"
    (sound card buffer, air, microphone) is not known here: the adaptive filter is long enough to absorb it.
    """

    def __init__(self, rate: int = SAMPLE_RATE, seconds: float = 120.0):
        self.rate = rate
        self._buf = np.zeros(int(seconds * rate), np.float32)
        self._t0: float | None = None
        self._end = 0                    # sample index where the next block starts
        self.epoch = 0                   # changes with every reset (a new utterance), so users of the timeline can re-align
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._buf[:] = 0
            self._t0, self._end = None, 0
            self.epoch += 1

    @property
    def t0(self) -> float | None:
        return self._t0

    def get_index(self, start: int, n: int) -> np.ndarray:
        """`n` reference samples starting at sample index `start` of this utterance (zeros outside what was played)."""
        with self._lock:
            out = np.zeros(n, np.float32)
            lo, hi = max(start, 0), min(start + n, len(self._buf))
            if hi > lo:
                out[lo - start: hi - start] = self._buf[lo:hi]
            return out

    def add(self, samples: np.ndarray, t: float) -> None:
        """`samples`: int16 at self.rate, just handed to the sound card at time `t`."""
        with self._lock:
            if self._t0 is None:
                self._t0 = t
            start = max(int(round((t - self._t0) * self.rate)), self._end)
            stop = min(start + len(samples), len(self._buf))
            if start >= len(self._buf):
                return
            self._buf[start:stop] = samples[: stop - start].astype(np.float32)
            self._end = stop

    def get(self, t_start: float, n: int) -> np.ndarray:
        """`n` reference samples for the interval starting at `t_start` (zeros where nothing was playing)."""
        with self._lock:
            if self._t0 is None:
                return np.zeros(n, np.float32)
            i = int(round((t_start - self._t0) * self.rate))
            out = np.zeros(n, np.float32)
            lo, hi = max(i, 0), min(i + n, len(self._buf))
            if hi > lo:
                out[lo - i: hi - i] = self._buf[lo:hi]
            return out

    @property
    def playing_until(self) -> float | None:
        """Time (on the shared clock) when everything handed over so far will have finished, ignoring the sound card lag."""
        with self._lock:
            return None if self._t0 is None else self._t0 + self._end / self.rate


class ReferenceAligner:
    """Gives the canceller a sample-continuous reference lined up with the microphone, with ONE constant offset per utterance.

    The microphone is a continuous stream of samples (frame after frame). The reference of an utterance is read from the
    timeline at "mic sample index - c", where c is set once from the first frame's timestamp and never touched again.
    """

    def __init__(self, timeline: ReferenceTimeline, rate: int = SAMPLE_RATE):
        self.timeline, self.rate = timeline, rate
        self._m = 0                      # microphone samples seen so far
        self._c: int | None = None
        self._epoch = timeline.epoch

    def next(self, n: int, frame_start_time: float) -> np.ndarray:
        """The reference for the next `n` microphone samples (the frame that began capturing at `frame_start_time`)."""
        if self.timeline.epoch != self._epoch:               # a new utterance started: forget the old offset
            self._epoch, self._c = self.timeline.epoch, None
        out = np.zeros(n, np.float32)
        t0 = self.timeline.t0
        if t0 is not None:
            if self._c is None:
                self._c = self._m - int(round((frame_start_time - t0) * self.rate))
            out = self.timeline.get_index(self._m - self._c, n)
        self._m += n
        return out


class EchoCanceller:
    def __init__(self, block: int = 320, partitions: int = 24, mu: float = 1.0, mu_converged: float = 0.8):
        self.B, self.P = block, partitions                 # 24 x 320 samples = 480 ms of echo path
        self.N = 2 * block
        bins = block + 1
        self._W = np.zeros((partitions, bins), np.complex64)
        self._Xh = np.zeros((partitions, bins), np.complex64)    # spectra of the most recent reference blocks, newest first
        self._xbuf = np.zeros(self.N, np.float32)
        self.mu, self.mu_converged = mu, mu_converged
        self._erle_db = 0.0                                # smoothed echo reduction, in dB, while the speaker is active
        self._hold = 0                                     # blocks left in which learning is frozen (near-end speech)
        self.helping = False                               # is the cancelled signal clearly better than the raw microphone?
        self.resets = 0                                    # how often it had to start over after diverging
        self.active_blocks = 0                             # blocks with reference signal: used by tests and reports

    @property
    def erle_db(self) -> float:
        return self._erle_db

    def reset_history(self) -> None:
        """New utterance: forget the reference history but keep what was learned about the room."""
        self._Xh[:] = 0
        self._xbuf[:] = 0

    def process(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        """mic and ref: equal-length int16/float arrays at 16 kHz (any multiple of the block size). Returns int16 with the echo removed."""
        mic = mic.astype(np.float32)
        ref = ref.astype(np.float32)
        out = np.empty_like(mic)
        for i in range(0, len(mic), self.B):
            m, r = mic[i:i + self.B], ref[i:i + self.B]
            if len(m) < self.B:                            # a short tail: pass it through untouched
                out[i:i + len(m)] = m
                break
            out[i:i + self.B] = self._block(m, r)
        return np.clip(out, -32768, 32767).astype(np.int16)

    def _block(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        B = self.B
        self._xbuf = np.concatenate([self._xbuf[B:], ref])
        X = np.fft.rfft(self._xbuf).astype(np.complex64)
        self._Xh = np.roll(self._Xh, 1, axis=0)
        self._Xh[0] = X
        echo = np.fft.irfft((self._W * self._Xh).sum(axis=0))[B:].astype(np.float32)   # the valid half (overlap-save)
        err = mic - echo
        ref_energy = float(np.dot(ref, ref))
        if ref_energy < 100.0 and not self._Xh[1:].any():   # nothing playing now or in the recent past: nothing to cancel or learn
            return mic
        self.active_blocks += 1
        mic_energy, err_energy = float(np.dot(mic, mic)) + 1e-6, float(np.dot(err, err)) + 1e-6
        if mic_energy < 4.0 * B:                             # rms below 2: the microphone hears (almost) nothing of the speaker
            self.helping = False
            return mic

        # Double-talk detection. Once the filter has learned (erle_db), the error should be about mic_energy / 10^(erle/10).
        # If it is several times bigger, somebody is talking over the echo: freeze learning (with a short hang-over) so that
        # the person's voice is not mistaken for echo and "learned".
        expected = mic_energy * 10 ** (-self._erle_db / 10)
        if self._erle_db > 6.0 and err_energy > 3.0 * expected + 50.0 * B:
            self._hold = 10                                  # ~200 ms
        talking = self._hold > 0
        if talking:
            self._hold -= 1
        else:
            erle = 10 * np.log10(mic_energy / err_energy)
            self._erle_db = erle if self.active_blocks <= 1 else 0.95 * self._erle_db + 0.05 * erle
        converged = self._erle_db > 12.0
        mu = 0.0 if talking else (self.mu_converged if converged else self.mu)
        if mu > 0:
            E = np.fft.rfft(np.concatenate([np.zeros(B, np.float32), err])).astype(np.complex64)
            power = (np.abs(self._Xh) ** 2).sum(axis=0)
            step = mu * np.conj(self._Xh) * E[None, :] / (power[None, :] + 1e-3 * self.P * B + 1.0)
            self._W += step.astype(np.complex64)
            w = np.fft.irfft(self._W, axis=1)               # keep every partition causal (no wrap-around) each block
            w[:, B:] = 0
            self._W = np.fft.rfft(w, axis=1).astype(np.complex64)
        if self._erle_db < -3.0 and self.active_blocks > 60:  # it is making things worse and not recovering: start over
            self._W[:] = 0
            self._erle_db, self.resets, self.active_blocks = 0.0, self.resets + 1, 0
            self.helping = False
            return mic
        self.helping = self._erle_db > 1.5
        return err if self.helping else mic                  # never hand over something worse than the microphone itself

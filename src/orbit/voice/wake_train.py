"""Train the custom "Hey Orbit" wake word, locally and without Colab.

How it works (the same idea openWakeWord's own pre-trained words use):
  audio -> openWakeWord's frozen feature extractor (80 ms frames -> 96-number embeddings)
        -> a small classifier over the latest 16 embeddings (1.3 s) -> a 0-1 "just heard the wake word" score.
Training audio: "hey orbit" said by your Piper voices at many speeds and styles, with noise, gain, band-limiting and room echo,
plus plenty of other speech (including look-alikes like "hey mycroft", "or a bit", "hey bud") as negatives. If you recorded
yourself (`python -m orbit.voice.run record-wake`), those clips are used too, and they matter most.

Honest limits: synthetic voices are not your voice, and minutes of negatives are not the thousands of hours a studio model sees.
The wake word check in wakeword.py therefore also confirms every candidate with a quick local Whisper pass before it counts.
"""
import random
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import signal

from . import config

SR = config.SAMPLE_RATE
FRAME = config.FRAME_SAMPLES
WINDOW = 16                       # embedding frames the classifier looks at (openWakeWord's own choice)
FEATURES = WINDOW * 96

POSITIVE_TEXTS = ["Hey Orbit", "Hey, Orbit.", "hey orbit", "Hey Orbit!", "Hey Orbit?", "hey, orbit"]
CONTINUATIONS = ["open spotify", "volume thirty", "what's playing right now", "play lofi on youtube", "close all other youtube tabs",
                 "switch to the echo pop speaker", "what windows are open", "reduce the volume a little bit", "pause", "next song",
                 "open my github", "mute", "go to the home page", "list my tabs", "make it louder"]
# Look-alikes first (given extra weight), then ordinary speech and Orbit's own commands without the wake word.
HARD_NEGATIVES = ["hey", "orbit", "Orbit, open Spotify", "hey Mycroft", "hey Jarvis", "hey Alexa", "okay Google", "hey Siri",
                  "or a bit", "a little bit", "just a bit", "hey bud", "hey bit", "hey Bob", "hey Rob", "hey it's me", "hey, are you there",
                  "hey, what are we doing", "hey Albert", "hey, open it", "the orbit of the moon", "in orbit around the earth",
                  "abort it", "robot", "hey Robert", "hey brother", "hey buddy", "hey you", "hey, over here", "hey, listen to this",
                  "oh it's a bit late", "what a habit", "hey, a bit later", "hey Ora", "hey Orion", "hey Oprah"]
NEGATIVES = [
    "open spotify", "volume thirty", "what's playing right now", "play lofi on youtube", "close all other youtube tabs",
    "switch to the echo pop speaker", "what windows are open", "reduce the volume a little bit", "pause the music",
    "go back to the previous page", "open my github and then tell me what windows are open", "make it fullscreen",
    "yo what are we doing brother", "I think we should go to the store later", "can you pass me that cable", "that was a really good game",
    "what time does the movie start", "I'll call you back in ten minutes", "did you see what happened yesterday", "turn the lights off please",
    "it's going to rain tomorrow", "I need to finish this project tonight", "let's order some food", "that's not what I said",
    "wait, say that again", "the weather is nice today", "I forgot my charger at home", "what do you want for dinner",
    "he said he would be here by noon", "this song is so good", "can you hear me now", "I don't know what you're talking about",
    "let me check my phone", "we should watch another episode", "good morning everyone", "thank you so much for coming",
    "subscribe and hit the like button", "welcome back to the channel", "today we are going to try something new",
    "this is insane, look at that", "no way, are you serious", "okay okay okay, listen", "bro that is crazy",
    "one two three four five", "the quick brown fox jumps over the lazy dog", "please remember to save your work",
    "I have a meeting at three o'clock", "the package should arrive on friday", "she walked into the room and sat down",
    "the stock market closed higher today", "breaking news from the capital", "turn it up a little", "skip this one",
    "who wants some coffee", "my laptop is running really slow", "I just need five more minutes", "where did you put the keys",
    "that makes a lot of sense", "I'm going to bed soon", "hold on, someone is at the door", "it was an amazing concert",
    "how much does this cost", "see you tomorrow morning", "I can't find my headphones", "play something relaxing",
    "what's the name of that song", "tell me a joke", "set a timer for ten minutes", "what's on my calendar today",
]


# ---------------------------------------------------------------- audio helpers
def to_16k(audio: np.ndarray, rate: int) -> np.ndarray:
    return signal.resample_poly(audio.astype(np.float32), SR, rate).astype(np.int16) if rate != SR else audio.astype(np.int16)


def speech_end(x: np.ndarray) -> int:
    """Sample index just after the last real sound (trailing silence trimmed). Works for clean and noisy clips."""
    env = np.convolve(np.abs(x.astype(np.float32)), np.ones(320) / 320, mode="same")
    loud = np.nonzero(env > 0.06 * env.max())[0]
    return int(loud[-1]) + 1 if len(loud) else len(x)


def silence(seconds: float, rng: random.Random) -> np.ndarray:
    n = int(seconds * SR)
    if rng.random() < 0.5:
        return np.zeros(n, np.int16)                                   # like a driver that outputs digital silence
    return np.array([rng.randint(-25, 25) for _ in range(n)], np.int16)  # like a real room


def augment(x: np.ndarray, rng: random.Random, nprng: np.random.Generator) -> np.ndarray:
    """Make a clean synthetic clip sound like it came through a real microphone in a real room."""
    y = x.astype(np.float32) / 32768.0
    if rng.random() < 0.35:                                            # cheap or distant mic: less treble
        b, a = signal.butter(2, rng.uniform(3000, 7000) / (SR / 2))
        y = signal.lfilter(b, a, y)
    if rng.random() < 0.3:                                             # a little room echo
        n = int(SR * rng.uniform(0.05, 0.2))
        ir = nprng.standard_normal(n) * np.exp(-np.linspace(0, rng.uniform(4, 9), n))   # a decaying burst of noise = a small room
        ir[0] = 1.0                                                                       # the direct sound
        y = np.convolve(y, ir / np.linalg.norm(ir), mode="full")[: len(y)]
    peak = max(1e-6, float(np.abs(y).max()))
    y = y / peak * 10 ** (rng.uniform(-30, -3) / 20)                    # peaks from -30 dBFS (quiet mic) to -3 dBFS
    if rng.random() < 0.75:                                            # background noise at 5-30 dB below the speech
        rms = float(np.sqrt((y ** 2).mean())) + 1e-9
        noise = nprng.standard_normal(len(y)).astype(np.float32)
        if rng.random() < 0.5:                                         # "pink-ish": more low frequencies, like fans and traffic
            noise = signal.lfilter([1.0], [1.0, -0.95], noise).astype(np.float32)
        noise *= rms / (float(np.sqrt((noise ** 2).mean())) + 1e-9) * 10 ** (-rng.uniform(5, 30) / 20)
        y = y + noise
    return (np.clip(y, -1, 1) * 32767).astype(np.int16)


# ---------------------------------------------------------------- streams and labels
@dataclass
class Stream:
    audio: np.ndarray
    labels: np.ndarray        # per 80 ms frame: 1 = the wake word just ended, 0 = not, -1 = ignore
    ends: list[int]           # frame index where each wake word ends
    source: str = ""


def build_stream(items: list[tuple[np.ndarray, int | None]], rng: random.Random, source: str = "") -> Stream:
    """Join clips with silence between them. items = (audio, sample index where the wake word ends or None)."""
    parts, end_samples = [silence(rng.uniform(1.6, 2.2), rng)], []
    pos = len(parts[0])
    for audio, end in items:
        parts.append(audio)
        if end is not None:
            end_samples.append(pos + end)
        pos += len(audio)
        gap = silence(rng.uniform(0.9, 2.0), rng)
        parts.append(gap)
        pos += len(gap)
    audio = np.concatenate(parts)
    audio = audio[: len(audio) // FRAME * FRAME]
    labels = np.zeros(len(audio) // FRAME, np.int8)
    labels[:WINDOW] = -1                                               # the extractor's start-up buffer is random noise: skip it
    ends = []
    for e in end_samples:
        f = (e - 1) // FRAME
        if f + 5 >= len(labels):
            continue
        labels[max(0, f - 2): f] = -1                                  # a word still being said: neither yes nor no
        labels[f: f + 3] = 1
        labels[f + 3: f + 5] = -1
        ends.append(f)
    return Stream(audio, labels, ends, source)


def extractor():
    from openwakeword.utils import AudioFeatures

    d = config.WAKEWORD_DIR
    return AudioFeatures(melspec_model_path=str(d / "melspectrogram.onnx"), embedding_model_path=str(d / "embedding_model.onnx"),
                         inference_framework="onnx", ncpu=2)


def stream_features(pre, audio: np.ndarray) -> np.ndarray:
    """(frames, 1536): the classifier input after every 80 ms frame, exactly as it will be fed live."""
    pre.reset()
    out = np.zeros((len(audio) // FRAME, FEATURES), np.float32)
    for i in range(len(out)):
        pre(audio[i * FRAME:(i + 1) * FRAME])
        out[i] = pre.get_features(WINDOW)[0].reshape(-1)
    return out


# ---------------------------------------------------------------- model
class Scorer:
    """The trained classifier in plain numpy (no extra runtime): standardize -> [relu layers] -> sigmoid."""

    def __init__(self, mean, scale, layers, threshold=0.5):
        self.mean, self.scale, self.layers, self.threshold = mean, scale, layers, float(threshold)

    def __call__(self, features: np.ndarray) -> np.ndarray:
        z = (np.atleast_2d(features) - self.mean) / self.scale
        for i, (w, b) in enumerate(self.layers):
            z = z @ w + b
            if i < len(self.layers) - 1:
                z = np.maximum(z, 0)
        return 1.0 / (1.0 + np.exp(-np.clip(z[:, 0], -30, 30)))

    def save(self, path: Path) -> None:
        arrays = {"mean": self.mean, "scale": self.scale, "threshold": np.array(self.threshold), "n_layers": np.array(len(self.layers))}
        for i, (w, b) in enumerate(self.layers):
            arrays[f"w{i}"], arrays[f"b{i}"] = w, b
        np.savez(path, **arrays)

    @staticmethod
    def load(path: Path) -> "Scorer":
        z = np.load(path)
        return Scorer(z["mean"], z["scale"], [(z[f"w{i}"], z[f"b{i}"]) for i in range(int(z["n_layers"]))], float(z["threshold"]))


def fit(X: np.ndarray, y: np.ndarray, kind: str, seed: int = 0) -> Scorer:
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier

    mean, scale = X.mean(0), X.std(0) + 1e-6
    Z = (X - mean) / scale
    if kind == "logreg":
        m = LogisticRegression(C=0.05, class_weight={0: 1, 1: 8}, max_iter=400).fit(Z, y)
        return Scorer(mean, scale, [(m.coef_.T.astype(np.float32), m.intercept_.astype(np.float32))])
    pos = np.nonzero(y == 1)[0]
    idx = np.concatenate([np.arange(len(y))] + [pos] * 5)               # positives are rare: show them more often
    m = MLPClassifier((64,), alpha=1e-2, max_iter=120, random_state=seed, early_stopping=False).fit(Z[idx], y[idx])
    return Scorer(mean, scale, [(m.coefs_[0].astype(np.float32), m.intercepts_[0].astype(np.float32)),
                                (m.coefs_[1].astype(np.float32), m.intercepts_[1].astype(np.float32))])


def trigger_frames(scores: np.ndarray, threshold: float, hits: int = config.WAKE_HITS, refractory: int = round(config.WAKE_REFRACTORY_S / 0.08)) -> list[int]:
    """The live trigger rule applied to a whole score track: `hits` frames in a row, then a refractory pause."""
    out, streak, blocked = [], 0, -1
    for i, s in enumerate(scores):
        streak = streak + 1 if s >= threshold else 0
        if streak >= hits and i >= blocked:
            out.append(i)
            streak, blocked = 0, i + refractory
    return out


def evaluate(scorer: Scorer, streams_with_features, thresholds) -> dict:
    """{threshold: (recall, false triggers per hour, positives, hours)} for held-out streams."""
    result = {}
    hours = sum(len(f) for _, f in streams_with_features) * 0.08 / 3600
    npos = sum(len(s.ends) for s, _ in streams_with_features)
    tracks = [(s, scorer(f)) for s, f in streams_with_features]
    for t in thresholds:
        found = false = 0
        for s, scores in tracks:
            for f in trigger_frames(scores, t):
                if any(e - 1 <= f <= e + 8 for e in s.ends):
                    found += 1
                elif not any(e - 4 <= f <= e + 12 for e in s.ends):
                    false += 1
        result[t] = (found / max(1, npos), false / max(hours, 1e-9), npos, hours)
    return result


# ---------------------------------------------------------------- data
def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return to_16k(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16), w.getframerate())


def synth(tts, text: str, rng: random.Random) -> np.ndarray:
    style = dict(length_scale=rng.choice([0.8, 0.9, 1.0, 1.1, 1.25]), noise_scale=rng.choice([0.4, 0.667, 0.85]), noise_w_scale=rng.choice([0.6, 0.8, 1.0]))
    return to_16k(np.concatenate(list(tts.synthesize(text, **style))), tts.sample_rate)


def synthetic_streams(voice: str, n_pos: int, n_neg: int, rng: random.Random, nprng: np.random.Generator, per_stream: int = 14) -> list[Stream]:
    from .tts import PiperTTS

    tts = PiperTTS(voice)
    items = []
    for _ in range(n_pos):
        a = synth(tts, rng.choice(POSITIVE_TEXTS), rng)
        end = speech_end(a)
        if rng.random() < 0.5:                                         # "hey orbit, open spotify": the word is followed by a command
            a = np.concatenate([a[:end], silence(rng.uniform(0.15, 0.5), rng), synth(tts, rng.choice(CONTINUATIONS), rng)])
        items.append((augment(a, rng, nprng), end))
    for _ in range(n_neg):
        text = rng.choice(HARD_NEGATIVES) if rng.random() < 0.3 else rng.choice(NEGATIVES)
        items.append((augment(synth(tts, text, rng), rng, nprng), None))
    rng.shuffle(items)
    return [build_stream(items[i:i + per_stream], rng, voice) for i in range(0, len(items), per_stream)]


def real_items() -> tuple[list, list]:
    """Your own recordings: (wake clips as (audio, end), everything else you recorded as (audio, None))."""
    base = config.FIXTURES_DIR.parent
    wake_dir = base / "fixtures_wake"
    pos, neg = [], []
    if wake_dir.exists():
        for p in sorted(wake_dir.glob("hey_orbit_*.wav")):
            a = load_wav(p)
            pos.append((a, speech_end(a)))
        for p in sorted(wake_dir.glob("negative_*.wav")):             # long recordings of normal talking / TV: cut into 8 s pieces
            a = load_wav(p)
            neg += [(a[i:i + 8 * SR], None) for i in range(0, len(a) - 8 * SR + 1, 8 * SR)]
    for folder in sorted(base.glob("fixtures*")):
        if folder.name != "fixtures_wake":                             # your command clips: speech without the wake word
            neg += [(load_wav(p), None) for p in sorted(folder.glob("*.wav"))]
    return pos, neg


def _streams_of(items: list, rng: random.Random, source: str, per_stream: int = 14) -> list[Stream]:
    items = list(items)
    rng.shuffle(items)
    return [build_stream(items[i:i + per_stream], rng, source) for i in range(0, len(items), per_stream)]


def _synthetic_data(pre, voices, n_pos, n_neg, seed, fresh):
    """{voice: [(stream, features)]}. Cached on disk (a local file this code wrote), because making it takes minutes."""
    import pickle

    cache = config.WAKEWORD_DIR / "_synthetic_cache.pkl"
    key = (tuple(voices), n_pos, n_neg, seed, "v2")
    if cache.exists() and not fresh:
        try:
            saved = pickle.loads(cache.read_bytes())
            if saved["key"] == key:
                print("  synthetic training data: loaded from cache (use --fresh to regenerate)")
                return saved["data"]
        except Exception:
            pass
    rng, nprng = random.Random(seed), np.random.default_rng(seed)
    data = {}
    for voice in voices:
        t0 = time.perf_counter()
        streams = synthetic_streams(voice, n_pos, n_neg, rng, nprng)
        data[voice] = [(s, stream_features(pre, s.audio)) for s in streams]
        print(f"  {voice}: {n_pos} wake + {n_neg} other clips -> {sum(len(f) for _, f in data[voice])} frames ({time.perf_counter() - t0:.0f}s)")
    cache.write_bytes(pickle.dumps({"key": key, "data": data}))
    return data


def _stack(groups):
    """Training matrix from [(items, repeat)] where items = [(stream, features)]; `repeat` shows those rows that many times."""
    X, y = [], []
    for items, repeat in groups:
        for s, f in items:
            keep = s.labels >= 0
            for _ in range(repeat):
                X.append(f[keep])
                y.append(s.labels[keep])
    return np.concatenate(X), np.concatenate(y)


def train(n_pos: int = 60, n_neg: int = 170, seed: int = 1, folds: int = 5, fresh: bool = False) -> int:
    voices = sorted(p.name.removesuffix(".onnx") for p in config.PIPER_DIR.glob("*.onnx"))
    if not voices:
        print("No Piper voices found. Download one: python -m orbit.voice.fetch_models piper en_US-amy-medium")
        return 1
    if not (config.WAKEWORD_DIR / "embedding_model.onnx").exists():
        print("openWakeWord feature models are missing. Run: python -m orbit.voice.fetch_models wakeword hey_mycroft")
        return 1
    pre, started, rng = extractor(), time.perf_counter(), random.Random(seed + 1)
    data = _synthetic_data(pre, voices, n_pos, n_neg, seed, fresh)
    all_synthetic = [item for v in voices for item in data[v]]
    pos, neg = real_items()
    print(f"  your recordings: {len(pos)} wake clips, {len(neg)} other clips")
    thresholds = [0.3, 0.5, 0.7, 0.85, 0.95, 0.99]
    cells = lambda res: "  ".join(f"{100 * res[t][0]:3.0f}%/{res[t][1]:5.1f}" for t in thresholds)

    print("\nCheck 1 - voices the model never heard: train on the other synthetic voices, test on the one left out.")
    print(f"{'left out':26} " + "  ".join(f"thr {t:<5}" for t in thresholds))
    synthetic: dict[str, dict] = {}
    for held in voices:
        X, y = _stack([([i for v in voices if v != held for i in data[v]], 1)])
        synthetic[held] = evaluate(fit(X, y, "mlp"), data[held], thresholds)
        print(f"{held:26} " + cells(synthetic[held]))
    print("(each cell: share of wake words caught % / false candidates per hour of audio)")

    real_cv, fold_data = {}, []
    if len(pos) >= folds:
        print(f"\nCheck 2 - YOUR voice: {folds}-fold test, each fold's clips are held out from the model that scores them.")
        fold_data = [[(s, stream_features(pre, s.audio)) for s in _streams_of(pos[i::folds] + neg[i::folds], rng, "real")] for i in range(folds)]
        totals = {t: [0.0, 0.0, 0, 0.0] for t in thresholds}
        for i in range(folds):
            rest = [item for j in range(folds) if j != i for item in fold_data[j]]
            X, y = _stack([(all_synthetic, 1), (rest, 4)])
            res = evaluate(fit(X, y, "mlp"), fold_data[i], thresholds)
            for t in thresholds:
                recall, per_hour, npos_, hours = res[t]
                totals[t][0] += recall * npos_
                totals[t][1] += per_hour * hours
                totals[t][2] += npos_
                totals[t][3] += hours
        real_cv = {t: (totals[t][0] / max(1, totals[t][2]), totals[t][1], totals[t][2], totals[t][3]) for t in thresholds}
        print(f"{'':26} " + "  ".join(f"thr {t:<5}" for t in thresholds))
        print(f"{'your wake words / false':26} " + "  ".join(f"{100 * real_cv[t][0]:3.0f}%/{real_cv[t][1]:4.0f}  " for t in thresholds))
        print(f"(caught % / number of false candidates in {totals[thresholds[0]][3] * 60:.1f} min of your talking and commands)")

    # Final model: everything, with your own clips counted extra. Threshold: the best recall among thresholds that keep false
    # candidates low. Each candidate is checked by Whisper before it counts, so a few false candidates per hour are acceptable.
    X, y = _stack([(all_synthetic, 1)] + ([([i for f in fold_data for i in f], 4)] if fold_data else []))
    scorer = fit(X, y, "mlp")

    def usable(t):
        return np.mean([synthetic[v][t][1] for v in voices]) <= 8 and (not real_cv or real_cv[t][1] <= 2)

    def value(t):
        return np.mean([synthetic[v][t][0] for v in voices]) + (2 * real_cv[t][0] if real_cv else 0)

    ok = [t for t in thresholds if usable(t)] or [thresholds[-1]]
    scorer.threshold = max(ok, key=value)
    out = config.WAKEWORD_DIR / "hey_orbit.npz"
    scorer.save(out)
    t = scorer.threshold
    print(f"\nTrained on {len(y)} frames ({int((y == 1).sum())} wake, {int((y == 0).sum())} other). Saved {out}")
    print(f"Chosen threshold {t}: unheard synthetic voices {100 * np.mean([synthetic[v][t][0] for v in voices]):.0f}% caught"
          + (f", YOUR voice {100 * real_cv[t][0]:.0f}% caught with {real_cv[t][1]:.0f} false candidates" if real_cv else "")
          + f".   Total time {time.perf_counter() - started:.0f}s")
    return 0

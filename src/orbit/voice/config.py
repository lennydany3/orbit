"""All voice settings in one place. These are starting values: tune them with real use and the numbers from monitor.py."""
import os

from .. import config as app_config

# --- Audio ---
SAMPLE_RATE = 16_000
FRAME_MS = 80                                    # mic block size; openWakeWord also wants 80 ms frames
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 1280
MIC_QUEUE_FRAMES = 100                           # 8 s of audio; if the consumer falls this far behind, frames are dropped and counted
MIC_DEVICE = os.environ.get("ORBIT_MIC")         # device index or part of its name; None = the Windows default mic
SPEAKER_DEVICE = os.environ.get("ORBIT_SPEAKER")  # same for playback

# --- Models (downloaded once with your approval, then cached here; never fetched at run time) ---
MODELS_DIR = app_config.PROJECT_ROOT / "models"
SILERO_MODEL = MODELS_DIR / "silero_vad.onnx"

# --- Voice activity detection (Silero VAD) ---
VAD_WINDOW = 512                 # Silero v5 at 16 kHz judges 512 samples (32 ms) at a time
VAD_CONTEXT = 64                 # ...with the last 64 samples of the previous window in front
VAD_START_THRESHOLD = 0.5        # probability that counts as "speech" when we are not yet speaking
VAD_END_THRESHOLD = 0.35         # lower bar while speaking, so a quiet word does not cut you off (hysteresis)
VAD_START_WINDOWS = 3            # consecutive speech windows (96 ms) needed before we say speech started
VAD_MIN_SPEECH_S = 0.25          # less speech than this = a noise or cough, reported as "ignored"
VAD_END_SILENCE_S = 0.9          # this much silence after speech ends the utterance
VAD_PREROLL_S = 0.3              # audio kept from before speech started, so the first word is not clipped
VAD_KEEP_TAIL_S = 0.3            # trailing silence kept in the clip (the rest is trimmed: Whisper never sees silence)
VAD_MAX_UTTERANCE_S = 20.0       # hard stop for one utterance (wake word); push-to-talk gets longer in V4

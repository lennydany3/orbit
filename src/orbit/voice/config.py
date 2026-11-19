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

# --- Which speech-to-text handles a turn ---
# "auto" = Groq first, local Whisper if Groq is unavailable; "groq" = Groq only; "local" = local only (fully offline).
# PRIVACY: with "groq" or "auto", the speech clip of a turn is sent to Groq, only after the wake word or hotkey fired.
# Nothing is sent while idle. The API key is read only from the GROQ_API_KEY environment variable.
STT_PROVIDER = os.environ.get("ORBIT_STT", "auto").lower()
GROQ_MODEL = "whisper-large-v3-turbo"
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_KEY_ENV = "GROQ_API_KEY"
GROQ_TIMEOUT_S = 8.0                             # waiting for Groq's answer; then fall back to local
STT_MIN_CLIP_S = 0.15                            # a clip shorter than this (or pure digital silence) is "empty": nothing is sent or run
STT_PRELOAD_LOCAL = True                         # load local Whisper in the background so a fallback is not delayed by the 4 s model load

# --- Local speech-to-text (faster-whisper, CPU only: the GPU belongs to the LLM) ---
WHISPER_DIR = MODELS_DIR / "whisper"             # one folder per model, e.g. models/whisper/base.en
WHISPER_MODEL = "small.en"                       # chosen from the V2 benchmark: about 11% word error on real recordings vs 18-26% for base.en
WHISPER_COMPUTE = "int8"
WHISPER_THREADS = 4                              # of 12 logical cores
WHISPER_BEAM = 1                                 # greedy decoding: fastest, good enough for short commands
# A vocabulary hint: Whisper spells names it was shown ("GitHub", not "get-up"). Empty string turns it off.
WHISPER_PROMPT = "Orbit, Spotify, GitHub, YouTube, Brave, Chrome, Echo Pop, lofi."
FIXTURES_DIR = app_config.PROJECT_ROOT / "tests" / "voice" / "fixtures"

# --- Text-to-speech (Piper on the CPU; pyttsx3 = the built-in Windows voice, as a fallback) ---
PIPER_DIR = MODELS_DIR / "piper"                 # <voice>.onnx + <voice>.onnx.json
PIPER_VOICE = "en_US-hfc_female-medium"          # a female voice, chosen in V3 (amy and lessac are also downloaded)
PIPER_LENGTH_SCALE = 1.0                         # below 1.0 = faster speech, above = slower

# --- Wake word (openWakeWord, CPU, ONNX) and push-to-talk ---
WAKEWORD_DIR = MODELS_DIR / "openwakeword"
WAKEWORD_MODEL = "hey_orbit"                     # trained here: python -m orbit.voice.run train-wake  (pre-trained stand-ins: hey_mycroft, alexa)
WAKE_THRESHOLD = 0.5                             # model score (0-1) that counts as a hit; tune from the scores `wake` prints
WAKE_HITS = 2                                    # consecutive 80 ms frames above the threshold before it counts as a wake
WAKE_VERIFY = True                               # custom words only: confirm each candidate with a quick local Whisper pass
WAKE_VERIFY_MODEL = "tiny.en"
WAKE_REJECT_COOLDOWN_S = 1.0                     # after a rejected candidate, wait this long before checking again
WAKE_REFRACTORY_S = 1.5                          # ignore further wakes for this long after one
PTT_KEY = "ctrl_r"                               # hold to talk (pynput key name)
PTT_MIN_HOLD_S = 0.25                            # a shorter tap is ignored (Right Ctrl is also used for shortcuts)
PTT_MAX_S = 45.0                                 # longest hold-to-talk utterance
WAKE_NO_SPEECH_S = 3.5                           # after a wake word, give up if you say nothing for this long
KILL_COMBO = "<ctrl>+<alt>+<esc>"                # global kill switch: stops listening instantly

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

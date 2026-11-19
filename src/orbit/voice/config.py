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

# --- Speech-to-text (faster-whisper, local, CPU only: the GPU belongs to the LLM) ---
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
# The hint matters: without it Whisper (tiny) kept only 11 of 25 real "hey orbit" clips; with it, 25 of 25. It also makes Whisper
# say "Hey Orbit" for some look-alike speech, so the check cuts false candidates by roughly 80%, not 100%.
WAKE_VERIFY_PROMPT = "Hey Orbit."
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

# --- Voice loop (V5): `python -m orbit --voice` ---
FOLLOW_UP = True                                 # after a reply, listen this long for a next request without the wake word
FOLLOW_UP_S = 2.0
CONFIRM_TIMEOUT_S = 6.0                          # how long to wait for yes / no after a spoken confirmation question
SPOKEN_MAX_SENTENCES = 2                         # replies are spoken short; the full text stays in the CLI
SPOKEN_MAX_CHARS = 220
LOG_TRANSCRIPTS = False                          # what you said is shown in the console, and only written to logs/voice.log if True
VOICE_LOG = app_config.LOG_DIR / "voice.log"     # every state transition, with a timestamp (never the audio)

# --- Conversational voice ---
CONVERSATIONAL = True                            # speak natural sentences instead of reading tool reports aloud (the screen shows the original)
FILLERS = True                                   # "One moment." while the agent is slow, so there is no long silence
FILLER_AFTER_S = 2.5                             # first filler after this long without an answer...
FILLER_AGAIN_S = 8.0                             # ...and one more ("Still working on it.") after this long
FOLLOW_UP_QUESTION_S = 6.0                       # when Orbit asks YOU a question, wait this long for the answer (no wake word needed)

# --- Interrupting Orbit by voice ---
BARGE_IN = True                                  # say the wake word while Orbit is talking: it stops and listens
AEC = True                                       # echo cancellation for that (only engages when it measurably helps; headphones need none)

# --- Not acting on background sound ---
FOLLOW_UP_WHILE_MEDIA = False                    # False: while music / a video plays, the no-wake follow-up stays closed (lyrics are not commands)

# --- Speculative speech-to-text ---
SPECULATIVE_STT = True                           # start transcribing at a short pause; keep the result only if you really stopped
SPECULATIVE_PAUSE_S = 0.55                       # silence after which the early transcription starts (the full end-of-speech wait is 0.9 s)

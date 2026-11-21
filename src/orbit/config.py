"""All tunable settings in one place."""
import ctypes
import os
import uuid
from pathlib import Path

from .env import load_env

load_env(Path(__file__).resolve().parents[2] / ".env")   # API keys (GROQ_API_KEY, SARVAM_API_KEY) come from this git-ignored file

APP_NAME = "Orbit"  # the only place the product name lives: rename here.

# --- Which language model answers: "auto" = Groq (cloud, fast) when GROQ_API_KEY is set, otherwise Ollama (local) ---
LLM_PROVIDER = os.environ.get("ORBIT_LLM", "auto").lower()        # auto | groq | ollama
GROQ_MODEL = os.environ.get("ORBIT_GROQ_MODEL", "openai/gpt-oss-120b")   # any Groq model that supports tool calling (list: console.groq.com/docs/models)
GROQ_REASONING = os.environ.get("ORBIT_GROQ_REASONING", "low")           # gpt-oss models think before answering: low = fastest (low | medium | high)
GROQ_FALLBACK_MODEL = os.environ.get("ORBIT_GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")   # used once when the main model hits its rate limit ("" = never)
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_TIMEOUT_S = 30

# --- Friend mode: chat with Orbit about your day, doubts, roasts... (anything that is not a PC command) ---
FRIEND_MODE = os.environ.get("ORBIT_FRIEND", "1").lower() not in ("0", "off", "false", "no")
USER_NAME = os.environ.get("ORBIT_USER_NAME", "")    # optional: what Orbit calls you in its head ("Lenny"); it learns your name in chat anyway
CHAT_TEMPERATURE = 0.8      # higher than for commands: a friend should not repeat itself
MAX_CHAT_TURNS = 8          # earlier chat messages Orbit remembers (kept small: Groq's free tier counts tokens per minute)
MAX_CHAT_CHARS = 3500

# --- LLM (local, via Ollama) ---
MODEL = "qwen2.5:7b"
OLLAMA_HOST = "http://localhost:11434"
NUM_CTX = 4096          # context window; keep small on a 6 GB GPU
TEMPERATURE = 0.1       # low = predictable tool calls
LLM_TIMEOUT_S = 120
KEEP_ALIVE = "30m"            # how long Ollama keeps the model in memory after a request (loading it again takes ~20 s)

# --- Agent limits ---
MAX_STEPS = 8                 # LLM round-trips per request
MAX_CONSECUTIVE_FAILURES = 3  # stop after this many failed tool calls in a row
MAX_TOOLS_PER_REQUEST = 10    # only this many tool schemas are sent to the model
MAX_HISTORY_TURNS = 6         # earlier requests the model can see (so 'close it' works)
MAX_HISTORY_CHARS = 5000      # hard cap on remembered text, to protect the small context window
VERIFY_RETRIES = 1            # re-run an action once if it fails or fails to verify

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- Browser layer ---
BROWSER = os.environ.get("ORBIT_BROWSER", "brave").lower()  # "brave" or "chrome": which installed browser Orbit drives
# Where "open <website>" goes: "mine" = a link opened in your own Brave profile (your logins work; Orbit reads, clicks, scrolls and
# types in that page through Windows UI Automation and the mouse wheel); "orbit" = Orbit's separate automated window (driven
# with Playwright, but sites that distrust automated browsers, like Google sign-in, refuse to log you in there).
BROWSER_MODE = os.environ.get("ORBIT_BROWSER_MODE", "mine").lower()
BRAVE_PROFILE = os.environ.get("ORBIT_BRAVE_PROFILE", "Personal")  # the Brave profile name shown in Brave's profile menu
BROWSER_HEADLESS = False        # visible window: you can watch it work, and fullscreen is real
BROWSER_PROFILE_DIR = PROJECT_ROOT / ".browser-profile"  # Orbit-only profile: never your real browser data
BRAVE_PATHS = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "BraveSoftware/Brave-Browser/Application/brave.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "BraveSoftware/Brave-Browser/Application/brave.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware/Brave-Browser/Application/brave.exe",
]

# "open my github" opens this profile (github.com/<name>). Change it with the ORBIT_GITHUB_USER environment variable.
GITHUB_USER = os.environ.get("ORBIT_GITHUB_USER", "lennydany3")

# --- Questions about the screen ("what does this mean?", pointing at something): the text on the screen is read (OCR and Windows'
# accessibility info), secrets in it are masked, and that text goes to the language model. With the cloud model (Groq) it leaves
# this PC for that one question; with the local model it never does. 0 = Orbit never reads the screen to answer.
SCREEN_HELP = os.environ.get("ORBIT_SCREEN_HELP", "1").lower() not in ("0", "off", "false", "no")

# --- On-screen animations: Orbit's pointer gliding to what it clicks, the click ripple, the "working" pill ---
VISUALS = os.environ.get("ORBIT_VISUALS", "1").lower() not in ("0", "off", "false", "no")   # --no-visuals turns them off for one run
VISUALS_SPEED = float(os.environ.get("ORBIT_VISUALS_SPEED", "1"))                            # 2 = twice as fast, 0.5 = half as fast



def _pictures_folder() -> Path:
    """The user's real Pictures folder (it is often moved to OneDrive), else ~/Pictures."""
    try:
        guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID("33E28130-4E1E-4676-835A-98395C3BC3BB").bytes_le)    # FOLDERID_Pictures
        path = ctypes.c_wchar_p()
        shell32 = ctypes.WinDLL("shell32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0 and path.value:
            found = Path(path.value)
            ctypes.WinDLL("ole32").CoTaskMemFree(ctypes.cast(path, ctypes.c_void_p))
            return found
    except (OSError, AttributeError):
        pass
    return Path.home() / "Pictures"


# --- Screenshots: "take a screenshot" saves a PNG here (Windows' own Screenshots folder) and copies the picture to the clipboard ---
SCREENSHOT_DIR = Path(os.environ.get("ORBIT_SCREENSHOT_DIR") or _pictures_folder() / "Screenshots")
SCREENSHOT_CLIPBOARD = os.environ.get("ORBIT_SCREENSHOT_CLIPBOARD", "1").lower() not in ("0", "off", "false", "no")

# --- Safety ---
LOG_DIR = PROJECT_ROOT / "logs"
# Tools may only touch files under these folders.
ALLOWED_DIRS = [
    Path.home() / "Downloads",
    Path.home() / "Documents" / APP_NAME,
    SCREENSHOT_DIR,                   # the one place a screenshot is written (the tool takes no path from anyone)
]

# --- Link to the face (the sphere window): `python -m orbit --voice --ipc`. Only this PC can connect (127.0.0.1). ---
IPC_PORT = int(os.environ.get("ORBIT_IPC_PORT", "8765"))
IPC_TOKEN = os.environ.get("ORBIT_IPC_TOKEN", "")     # empty = a new random token every run, printed at start (the Rust window is handed it)
# Web pages that may connect (comma-separated origins). Empty = none: a browser always sends its page's origin, and any
# website could otherwise listen in on your voice turns and answer "are you sure?" for you.
IPC_ORIGINS = tuple(o.strip() for o in os.environ.get("ORBIT_IPC_ORIGINS", "").split(",") if o.strip())

"""All tunable settings in one place."""
import os
from pathlib import Path

APP_NAME = "Orbit"  # the only place the product name lives: rename here.

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
# Where "open <website>" goes: "mine" = a link opened in your own Brave profile (your logins work, but Orbit cannot read or
# click in that page); "orbit" = Orbit's separate automated window (Orbit can read and click, but sites that distrust
# automated browsers, like Google sign-in, refuse to log you in there).
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

# --- Safety ---
LOG_DIR = PROJECT_ROOT / "logs"
# Tools may only touch files under these folders.
ALLOWED_DIRS = [
    Path.home() / "Downloads",
    Path.home() / "Documents" / APP_NAME,
]

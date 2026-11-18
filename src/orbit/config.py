"""All tunable settings in one place."""
from pathlib import Path

APP_NAME = "Orbit"  # the only place the product name lives: rename here.

# --- LLM (local, via Ollama) ---
MODEL = "qwen2.5:7b"
OLLAMA_HOST = "http://localhost:11434"
NUM_CTX = 4096          # context window; keep small on a 6 GB GPU
TEMPERATURE = 0.1       # low = predictable tool calls
LLM_TIMEOUT_S = 120

# --- Agent limits ---
MAX_STEPS = 8                 # LLM round-trips per request
MAX_CONSECUTIVE_FAILURES = 3  # stop after this many failed tool calls in a row
MAX_TOOLS_PER_REQUEST = 10    # only this many tool schemas are sent to the model
VERIFY_RETRIES = 1            # re-run an action once if it fails or fails to verify

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# --- Browser layer ---
BROWSER_CHANNEL = "chrome"       # drive the installed Google Chrome (no separate download)
BROWSER_HEADLESS = False        # visible window: you can watch it work, and fullscreen is real
BROWSER_PROFILE_DIR = PROJECT_ROOT / ".browser-profile"  # Orbit-only profile: never your real Chrome data

# --- Safety ---
LOG_DIR = PROJECT_ROOT / "logs"
# Tools may only touch files under these folders.
ALLOWED_DIRS = [
    Path.home() / "Downloads",
    Path.home() / "Documents" / APP_NAME,
]

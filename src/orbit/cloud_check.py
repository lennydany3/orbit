"""`python -m orbit.cloud_check`: tests your Groq key with one tiny request and prints how long it took.

Sends only the words "Say ok.". Speech-to-text and the voice run on this PC, so they have no key to check.
"""
import sys
import time

from . import config


def _step(label: str, fn) -> bool:
    start = time.perf_counter()
    try:
        detail = fn()
    except Exception as e:  # noqa: BLE001 - this is a diagnostic: show whatever went wrong
        print(f"  FAIL  {label}: {e}")
        return False
    print(f"  ok    {label}: {time.perf_counter() - start:.2f}s  {detail}")
    return True


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    from .llm.groq_llm import GroqLLM, config_key

    results = []
    print(f"Groq key: {'found' if config_key() else 'MISSING (GROQ_API_KEY in .env)'}\n")
    if config_key():
        llm = GroqLLM()
        print(f"Groq ({config.GROQ_MODEL}):")
        results.append(_step("connect", lambda: f"({llm.warm_up():.2f}s of that was the connection)"))
        results.append(_step("chat", lambda: repr(llm.chat([{"role": "user", "content": "Say ok."}], []).content[:40])))
    if not results:
        print("No Groq key found. Copy .env.example to .env and paste your key (see the README).")
        return 2
    print("\nAll good." if all(results) else "\nSomething failed: fix it, or Orbit will use the local language model (Ollama).")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

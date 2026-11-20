"""`python -m orbit.cloud_check`: tests your Groq and Sarvam keys with one tiny request each and prints how long each took.

Sends only the words "Say ok." and a one-sentence test voice clip. Nothing from your microphone is used.
"""
import sys
import time

from . import config
from .voice import config as config_voice


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
    import numpy as np

    from .llm.groq_llm import GroqLLM, config_key
    from .voice.cloud import SarvamSession, SarvamTranscriber, sarvam_key
    from .voice.tts import SarvamTTS

    results = []
    print(f"Groq key:   {'found' if config_key() else 'MISSING (GROQ_API_KEY in .env)'}")
    print(f"Sarvam key: {'found' if sarvam_key() else 'MISSING (SARVAM_API_KEY in .env)'}\n")
    if config_key():
        llm = GroqLLM()
        print(f"Groq ({config.GROQ_MODEL}):")
        results.append(_step("connect", lambda: f"({llm.warm_up():.2f}s of that was the connection)"))
        results.append(_step("chat", lambda: repr(llm.chat([{"role": "user", "content": "Say ok."}], []).content[:40])))
    if sarvam_key():
        session = SarvamSession()
        print("Sarvam:")
        engine = SarvamTTS(session=session)
        voice = {}

        def speak_test() -> str:
            voice["audio"] = engine.fetch("Hello, I am Orbit.")
            return f"{len(voice['audio']) / engine.sample_rate:.1f}s of audio"

        results.append(_step("text-to-speech", speak_test))
        if "audio" in voice:                       # feed the voice back through speech-to-text: a full round trip
            from scipy.signal import resample_poly

            clip = resample_poly(voice["audio"].astype(np.float32), config_voice.SAMPLE_RATE, engine.sample_rate).astype(np.int16)
            stt = SarvamTranscriber(session)
            results.append(_step("speech-to-text", lambda: repr(stt.transcribe(clip).text)))
    if not results:
        print("No keys found. Copy .env.example to .env and paste your keys (see the README).")
        return 2
    print("\nAll good." if all(results) else "\nSomething failed: fix it, or Orbit will use the local engines for that part.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

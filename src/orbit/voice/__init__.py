"""Voice for Orbit: mic -> VAD -> speech-to-text -> the existing agent -> text-to-speech.

Everything runs on this PC (CPU only) except speech-to-text, which by default (ORBIT_STT=auto) sends the speech clip
of one turn to Groq after the wake word or hotkey fired, and falls back to local Whisper. ORBIT_STT=local is fully offline.
"""

"""Local voice for Orbit: mic -> VAD -> speech-to-text -> the existing agent -> text-to-speech.

Everything runs on this PC (CPU only) and offline. The only network use is `fetch_models`, which you run by hand.
"""

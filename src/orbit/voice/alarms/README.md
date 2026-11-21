# Alarm sounds

Orbit plays one of these, over and over, when a reminder, timer or alarm comes due, until you say "ok", "thanks", "fine"... (see `../alarm.py`).

| File | Rings for | What it is |
|---|---|---|
| `reminder.wav` | a reminder | a warm, gentle bell: a nudge, not a shout (2.7 s) |
| `timer.wav` | a timer, and an alarm clock ("wake me up at 6:30") | digital beeps: clearly a different sound, and clearly a timer (5.1 s) |

Both were chosen from TuneTank's sound-effect library (searched with the TuneTank MCP), then prepared the same way so neither is louder:

| File | TuneTank sound effect | Id |
|---|---|---|
| `reminder.wav` | "Bell gentle alarm notification" | 28233 |
| `timer.wav` | "Digital alarm beeping" | 26081 |

Preparation: the free preview was decoded, turned to mono at 22.05 kHz / 16-bit, trimmed to the sound, given a 4 ms fade-in and a 60 ms fade-out (so it loops without a click), set to the same average loudness (about -19 dB RMS) and kept below -1.5 dB at its peak.

**Licence:** the TuneTank search gives no licence terms for these previews. They are used here for a personal project; check TuneTank's terms for sound effects before you share or sell Orbit, and replace the two files if they do not cover that.

## Using your own sounds

Replace either file with any 16-bit PCM WAV (mono or stereo, any sample rate; 1 to 8 seconds works best; start and end it quietly so it loops without a click). Nothing else needs to change.

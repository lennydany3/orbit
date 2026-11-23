# Voice latency: measurement protocol and results

Goal: cut the time from **the end of the user's speech to Orbit's first spoken word**, with no loss of recognition accuracy.
The plan is in [`../voice-latency-ARCHITECTURE.md`](../voice-latency-ARCHITECTURE.md). This file is where the numbers go.

## What is measured

Every finished voice turn prints one `[turn]` line, and the run ends with a `--- voice report ---` block (printed on Ctrl+C).

```
[turn] transcribe 0.62s | agent 0.31s | reply -> first audio 0.44s | = 1.37s from end of speech to first spoken word (+0.9s end-of-speech wait)
```

| Number | Where it comes from | What it covers |
|--------|--------------------|----------------|
| `transcribe` | `speech_end` → `text` mark | The wait for the transcript (speech-to-text, or the tail of a speculative one). |
| `agent` | `text` → `reply` mark | Rules, classifier and LLM, until the whole reply string exists. |
| `reply -> first audio` | `reply` → `first_audio` mark | TTS startup: stream open, first synthesis or network call. |
| **end of speech → first spoken word** | `speech_end` → `first_audio` mark | The headline number. `speech_end` is the moment the segmenter decides you stopped, so it **excludes** the end-of-speech silence wait (`VAD_END_SILENCE_S`, 0.9 s by default). |
| **last word → first spoken word** | the line above + `VAD_END_SILENCE_S` | What you actually feel on a wake-word turn. Not recorded for push-to-talk, which has no wait. |

The report lists `n`, `median`, `p90`, `p95` and `max` for each name below. Use **median** and **p90** in the tables.

| Report line | Meaning |
|-------------|---------|
| `transcribe (end of speech -> text)` | wall-clock transcribe, as the user waited |
| `speech-to-text` | the engine's own compute time (a speculative run that finished early shows less than the wait) |
| `agent` | agent time |
| `reply -> first audio` | TTS startup |
| `end of speech -> first spoken word` | headline |
| `last word -> first spoken word (incl. end-of-speech wait)` | headline plus the VAD wait (wake word / follow-up turns only) |

Notes on reading the numbers:

- `first_audio` is the first audible sample **of the reply**. A filler ("One moment.") is not counted as the first spoken word.
- A turn that is interrupted, or whose TTS produces no audio, prints no `[turn]` line and is not counted.
- Turns that fail (STT error, empty transcript) are not counted either.
- The `[turn]` lines are not logged to a file unless you redirect the console. To keep them: `python -m orbit --voice 2>&1 | Tee-Object run.txt`
  in PowerShell. (The transcripts shown there are never written by Orbit itself; see `LOG_TRANSCRIPTS`.)

## Protocol

Use the **same 20 spoken requests for every run**, in the same order, at the same distance from the same microphone, in a quiet room,
on the same network, with the same provider keys. Wake word and push-to-talk are measured **separately** (the wake word adds the
end-of-speech wait; push-to-talk does not).

Three groups, each in **its own session** so the report's median and p90 belong to one group (start Orbit, say the group, press Ctrl+C,
copy the report):

**Group A: 8 rule-matched commands (no LLM call).** Say each one clearly, wait for the answer to finish, then the next.

1. "volume 30"
2. "open Spotify"
3. "pause the music"
4. "next song"
5. "mute"
6. "volume up"
7. "open GitHub"
8. "volume down"

**Group B: 6 LLM commands (the model picks the tool).**

1. "switch to Chrome"
2. "what windows do I have open?"
3. "what's playing right now?"
4. "put the Spotify window on my second monitor"
5. "scroll down on this page"
6. "minimize everything except the browser"

(If one of these turns out to be handled by the rules, swap it for something that is not. The `agent` time will tell: well under
100 ms means rules.)

**Group C: 6 friend-mode chats.** The first four are caught by the quick heuristics; the last two are not, so they also pay for the
classifier call (B2).

1. "how are you today?"
2. "I'm feeling a bit tired"
3. "tell me a joke"
4. "why is the sky blue?"
5. "I got the job!"
6. "my sister is mad at me"

Skip the follow-up window: say the request, let Orbit finish, then wait for it to return to idle (or run with `--no-follow-up`).

### Commands to run

Open a terminal in the repo root with the virtual environment active (`.venv\Scripts\activate`).

```powershell
# Wake word turns (say "hey orbit", then the request)
python -m orbit --voice --no-follow-up

# Push-to-talk turns (hold Right Ctrl while you talk; wake word off)
python -m orbit --voice --no-follow-up --no-wake
```

Run each of those **three times** (one session per group A, B, C). Press **Ctrl+C** after the 8th / 6th / 6th request and copy the
`--- voice report ---` block, or paste the `[turn]` lines into the chat. Record the config used (below) for every set of runs.

Before the first run: `python -m orbit.cloud_check` to confirm which providers are live, and keep the first turn of every session
out of the numbers (it pays for any lazy loading). The simplest way is to say a throw-away "volume 30" first and ignore it.

### Always record

- Git commit and the phase it belongs to.
- `ORBIT_LLM`, `ORBIT_STT`, `ORBIT_TTS` and any new `ORBIT_...` flag set in `.env` or the terminal.
- Which providers actually ran: the "Cloud mode: ..." (or "all run on this PC") line printed at start-up says which parts use the
  cloud. A line like "cloud speech-to-text unavailable ...; using local Whisper" during a run means a fallback kicked in: note it,
  the turn is then not representative.
- Whisper model and thread count, Piper voice, speaker device, wake word or push-to-talk.
- Anything unusual (Wi-Fi hiccup, a 429 from Groq, a model that was cold).

## Results

Fill one table per phase and per trigger (wake word / push-to-talk). Times are in seconds. Format: `median / p90`.

### Phase 0: baseline (no change)

Config: commit `__________`, `ORBIT_LLM=____` `ORBIT_STT=____` `ORBIT_TTS=____`, providers that ran: `______________________`

**Trigger: wake word**

| Group | n | transcribe | agent | reply → first audio | end of speech → first word | last word → first word |
|-------|---|-----------|-------|--------------------|---------------------------|------------------------|
| A: rule-matched commands (8) | | | | | | |
| B: LLM commands (6) | | | | | | |
| C: friend chats (6) | | | | | | |

**Trigger: push-to-talk**

| Group | n | transcribe | agent | reply → first audio | end of speech → first word |
|-------|---|-----------|-------|--------------------|---------------------------|
| A: rule-matched commands (8) | | | | | |
| B: LLM commands (6) | | | | | |
| C: friend chats (6) | | | | | |

### Phase 1: quick TTS and VAD wins

*(not started)*

### Phase 2: STT engines

*(not started)*

STT bench (`python -m orbit.voice.run bench`, fixtures in `tests/voice/fixtures`):

| Engine | WER | median latency | p90 latency |
|--------|-----|---------------|-------------|
| | | | |

### Phase 3: classifier speedup

*(not started)*

### Phase 4: streaming chat replies into TTS

*(not started)*

## Definition of done

- Median **end of speech → first spoken word** drops clearly, for both commands (groups A and B) and chat (group C).
- WER on the fixtures is not worse for whichever STT engine becomes the default.
- Every new behaviour is behind a flag, every fallback still works, and all tests pass.

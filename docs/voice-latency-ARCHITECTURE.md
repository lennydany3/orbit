# Orbit voice latency: architecture and optimization plan

Audience: the Claude Code instance that will implement this in the Orbit repo.
Scope: make a voice turn faster, from the end of the user's speech to Orbit's first spoken word. Covers STT, the
agent/LLM handoff and TTS. Accuracy must not get worse.

All paths are relative to the Orbit repo root. Line numbers are approximate: find code by the function and class names.

---

## 1. How a voice turn works today

```
mic (80 ms int16 frames, 16 kHz)                                   voice/audio_io.py
  └─> wake word (openWakeWord) or push-to-talk (Right Ctrl)        voice/wakeword.py, voice/hotkey.py
        └─> SpeechSegmenter (Silero VAD, 32 ms windows)            voice/vad.py
              ├─ "pause" event after SPECULATIVE_PAUSE_S = 0.55 s  -> transcription starts early
              └─ "end" event after VAD_END_SILENCE_S = 0.9 s       -> TurnTimer.mark("speech_end")
                    └─> VoiceLoop._begin_transcribe(clip)          voice/loop.py
                          transcriber.transcribe(clip)             voice/stt.py (faster-whisper small.en int8, CPU, 4 threads)
                                                                   voice/cloud.py (Sarvam saarika:v2.5, fallback to local)
                    └─> VoiceLoop._tick_transcribing               mark("text")
                          spawn(agent.handle, text)                agent/loop.py  (returns ONE full reply string)
                    └─> VoiceLoop._tick_thinking                   mark("reply")
                          chat:   spoken_summary(reply, 5 sentences, 520 chars)
                          action: phraser.say(reply) -> spoken_summary(reply, 2 sentences, 220 chars)
                          VoiceLoop._speak(spoken)                 -> spawn(tts.speak, spoken)
                    └─> StreamingTTS.speak(text)                   voice/tts.py
                          producer thread: sentences(text) -> queue(maxsize=3)
                          opens a NEW sd.OutputStream on every call
                          writes 2048-sample blocks, records each block into the AEC ReferenceTimeline (16 kHz)
                          SarvamTTS: one REST call per sentence, 3 in parallel, base64 WAV
                          PiperTTS:  local ONNX, 2 intra-op threads
                    └─> VoiceLoop._tick_speaking                   mark("first_audio"), prints "[turn] ..." summary
```

The `TurnTimer` (`voice/state.py`) already measures the stages:
`transcribe` (speech_end→text), `agent` (text→reply), `reply -> first audio` (reply→first_audio), and the total. The fixed
end-of-speech wait (0.9 s) comes on top of that total.

### Already optimized (do NOT redo)
- Whisper: `beam_size=1`, `temperature=0`, `int8`, `without_timestamps`, `condition_on_previous_text=False`, warm-up at load.
- Speculative STT at a 0.55 s pause, kept only if the final clip is identical (`_begin_transcribe`).
- Clips are VAD-trimmed: Whisper never sees silence.
- TTS is pipelined per sentence. Sarvam prefetches 3 sentences in parallel.
- One long-lived `httpx.Client` per cloud provider (TLS reuse).
- LLM keep-warm thread (`_keep_model_warm`). Groq `reasoning_effort` is already `low` (`config.GROQ_REASONING`).
- Groq falls back to `gpt-oss-20b` on a 429.
- Measurement (Phase 0, done): the monitor's end-of-run report now has median and p90 for `transcribe (end of speech -> text)`,
  `agent`, `reply -> first audio`, `end of speech -> first spoken word` and (wake-word turns) `last word -> first spoken word`.
  The protocol and the results tables are in `docs/perf/voice-latency.md`.

---

## 2. Where the time goes (bottlenecks, ranked)

| # | Bottleneck | Where | Why it costs time |
|---|-----------|-------|-------------------|
| B1 | The LLM reply is not streamed | `agent/loop.py` `_chat`, `_llm_loop`; `llm/groq_llm.py` `chat` | TTS cannot start until the whole reply has been generated. This matters most for friend-mode chat (up to 5 spoken sentences). |
| B2 | Friend mode can make two LLM calls in a row | `agent/loop.py` `_wants_chat` → `_classify` → `_chat` | When the quick heuristics can't decide, the full 120B model classifies first, then a second call answers. |
| B3 | Whisper on short clips | `voice/stt.py` | Whisper's encoder always processes a padded 30 s window, so a 2 s command costs about as much compute as 30 s of audio. CPU only, 4 of 12 threads. |
| B4 | The end-of-speech wait | `voice/config.py` `VAD_END_SILENCE_S = 0.9` | A fixed wait added to every wake-word turn (push-to-talk doesn't have it). |
| B5 | A new output stream on every `speak()` | `voice/tts.py` `StreamingTTS.speak` | Opening a stream with `sd.OutputStream(...)` and `start()` on Windows adds startup latency before the first audio, every reply and every filler. |
| B6 | Fixed phrases are synthesized each time | fillers (`Phraser.filler`), "Sorry, I didn't catch that.", farewells, alarm lines | They're re-synthesized, or re-fetched over the network, every time. |
| B7 | The first TTS request is a whole sentence | `voice/tts.py` `split_sentences`, `SarvamTTS.sentences` | The first audio waits for one full network round trip plus synthesis of the entire first sentence. |
| B8 | Small TTS costs on the critical path | `PiperTTS(threads=2)`; `resample_poly` for the AEC reference before the first write | A few ms to tens of ms. Measure before touching. |

B1 and B2 sit between STT and TTS. They are very likely the biggest share of "agent" time in chat mode.
B3 and B4 make up most of the STT time. B5–B7 make up most of the "reply → first audio" time.

---

## 3. Target architecture

```
speech end ─┬─ (earlier, tuned VAD) ───────────────> STT engine chain
            │                                         Groq whisper-large-v3-turbo | Sarvam | local Parakeet/Moonshine (sherpa-onnx) | faster-whisper
            │                                         + shared vocabulary post-correction
            └─> agent.handle(text, sentence_sink=...)
                   chat path: llm.chat_stream() ──> incremental spoken-sentence splitter ──> sink(sentence)
                   action path: unchanged (short replies, rewritten by Phraser)
                   classifier: small fast model, max_tokens small
                          │
                          ▼
               VoiceLoop starts SPEAKING on the FIRST sentence (not at the end of the reply)
                          │
                          ▼
               StreamingTTS.speak_iter(sentences)
                   phrase/sentence audio cache (LRU + prewarmed fixed phrases)
                   short first chunk (first clause)
                   PERSISTENT sd.OutputStream per (device, sample_rate)
                   AEC reference recording unchanged
```

Design rules for every change:
1. **Put each change behind a config flag in `voice/config.py`**, also readable from an environment variable (follow the
   existing `os.environ.get("ORBIT_...")` pattern). The old behaviour must stay reachable.
2. **Keep every fallback.** Cloud → local → Windows voice → text-only must still work. A new engine joins the chain; it
   doesn't replace it.
3. **Privacy stays as it is.** With no API keys, nothing leaves the PC. Never log audio, transcripts (unless
   `LOG_TRANSCRIPTS`) or keys.
4. **Same interfaces.** Transcribers expose `name`, `model_name`, `load_s`, `transcribe(clip) -> Transcript`,
   `submit(clip) -> Future`, `close()`. TTS engines subclass `StreamingTTS`. `agent.handle(text)` still returns the full reply
   string, so the CLI, history, UI and the 1888 existing tests keep working.
5. **Barge-in, echo cancellation, pause/resume and `stop()` within ~100 ms must keep working.**

---

## 4. Change designs

### 4.1 Persistent audio output stream (B5) — low risk
File: `voice/tts.py` (`StreamingTTS`).
- Add `_ensure_stream()`, which returns an open, started `sd.OutputStream` cached on the engine for `(self.device, self.sample_rate)`.
  Reopen it when the device changes (`set_device`), when the sample rate differs, or after a PortAudio error.
- `speak()` writes to the cached stream and no longer closes it.
- **Interrupt:** keep today's semantics (cut audio at once). Use `stream.abort()` and mark the stream for re-`start()` on the
  next speak.
- **Normal end:** `stream.stop()` used to wait for the buffer to drain. Without it, `speak()` returns up to one buffer early, and the
  follow-up mic could hear Orbit's own tail. After the last block, wait for `stream.latency` seconds, or write that much silence,
  so the return time stays the same as before.
- Close the stream in a new `close()` and at shutdown.
- Flag: `TTS_PERSISTENT_STREAM = True`.

### 4.2 Phrase and sentence audio cache (B6) — low risk
File: `voice/tts.py`.
- Cache key: `(engine name, voice/speaker, model, emotion, effective pace, gain-independent, sentence text)` → `np.ndarray` int16
  at `self.sample_rate`. Apply gain at play time, as today, so the cached audio stays raw.
- An LRU of about 128 entries covers repeated replies ("Done.", "Volume set to thirty.").
- **Prewarm** in a background daemon thread after the engines load: `Phraser` fillers (all variants), "Sorry, I didn't catch that.",
  "Sorry, I couldn't understand that.", farewell replies (`voice/farewell.py`), and the alarm announcements that are fixed text.
  Prewarming must never block startup, and it must never spend Sarvam calls when `TTS_PREWARM_CLOUD=False`.
- Hook: wrap the `sentences()` iteration so each sentence is looked up first and fetched or synthesized only on a miss.
- Flags: `TTS_CACHE = True`, `TTS_CACHE_SIZE = 128`, `TTS_PREWARM = True`, `TTS_PREWARM_CLOUD = True`.

### 4.3 Short first chunk (B7) — low risk
File: `voice/tts.py`.
- Replace `split_sentences` in `SarvamTTS.sentences` with `split_for_speech(text, first_max_words=TTS_FIRST_CHUNK_WORDS)`.
  If the first sentence has more words than that, split it at its first `,` `;` `:` or `—` after word 3. Never split mid-word,
  and never split when there's no clause boundary.
- Piper splits sentences internally via `voice.synthesize`. Apply the same first-chunk split there by synthesizing the first clause
  as its own call, then the rest.
- Add no extra `_SENTENCE_PAUSE_S` between the two halves of a split sentence; the gap must stay natural.
- Flag: `TTS_FIRST_CHUNK_WORDS = 8` (`0` = off).

### 4.4 Piper threads and the AEC resample (B8) — measure first
- `PIPER_THREADS` config (default 4, was hard-coded 2). Check that the mic loop keeps its 80 ms rhythm (the monitor reports dropped
  frames).
- Only if measured to matter: compute `ref16` for the first block only before writing it, and resample the rest of the sentence
  while it plays. The AEC needs timestamps from the moment each block is written, so keep `reference.add(..., perf_counter())` per
  block exactly as now.

### 4.5 VAD timing (B4) — tune by measurement
File: `voice/config.py`.
- Make `VAD_END_SILENCE_S` and `SPECULATIVE_PAUSE_S` environment-overridable (`ORBIT_VAD_END_SILENCE_S`, `ORBIT_SPECULATIVE_PAUSE_S`).
- Candidates: end 0.7 s, speculative 0.4 s. Accept only if, over a 20-turn session, no utterance is cut off mid-sentence.
- Note: with a cloud STT, every speculative transcription is a billed request that also counts against rate limits (Groq limits audio
  seconds per hour). Count discarded speculative requests in the monitor.

### 4.6 STT engine chain (B3)
Files: `voice/cloud.py` (or a new `voice/groq_stt.py`), a new `voice/stt_sherpa.py`, `voice/stt.py`, `voice/fetch_models.py`,
`voice/run.py` (bench).

**a) GroqTranscriber (cloud).**
- `POST https://api.groq.com/openai/v1/audio/transcriptions`, multipart: `file` (the existing `wav_bytes(clip)`),
  `model=whisper-large-v3-turbo`, `language=en`, `prompt=config.WHISPER_PROMPT`, `temperature=0`, `response_format=json`.
  Verify the parameter names against the current Groq docs before coding.
- Reuse `GROQ_API_KEY` (`llm/groq_llm.py:config_key`) and a long-lived `httpx.Client`. Add a cool-down on failure like
  `SarvamSession`, and raise `CloudError` so the chain falls through.
- Generalize `FallbackTranscriber` into a chain of N engines. Local engines are built lazily, as today.
- Provider selection: `ORBIT_STT=auto|groq|sarvam|local`, plus `ORBIT_STT_ORDER` (default keeps today's behaviour: Sarvam first when
  its key is set, then local). Sarvam was probably chosen for the en-IN accent. Do NOT change the default order without the user's
  benchmark.
- `privacy_note()` in `voice/loop.py` must mention Groq STT when it's active.

**b) Local Parakeet or Moonshine via sherpa-onnx.**
- New `SherpaTranscriber` with the same interface. It wraps `sherpa_onnx.OfflineRecognizer`:
  - Parakeet TDT 0.6B v2 int8: `from_transducer(..., model_type="nemo_transducer")`
  - Moonshine: `from_moonshine(...)`
  Verify the constructor names and arguments against the installed sherpa-onnx version.
- Warm up at load. `num_threads` comes from config. Input is float32 at 16 kHz (convert from int16 as `stt.py` does).
- `fetch_models.py`: add a `sherpa <model>` target that downloads and caches under `models/sherpa/<model>/`. Only download with the
  user's approval, the same as existing models; never at run time.
- Selection: `ORBIT_LOCAL_STT=whisper|parakeet|moonshine`. Default stays `whisper` until the bench proves the new engine.
- **The vocabulary hint is lost.** Parakeet and Moonshine take no `initial_prompt`. Add one shared post-correction step,
  `apply_vocab_fixes(text)`, with a table in config (e.g. "get hub"→"GitHub", "you tube"→"YouTube", "echo pop"→"Echo Pop",
  "low fi"→"lofi", "orbits"→"Orbit" when it's the first word). Run it for every engine, in one place, before `strip_wake_phrase`.
  Build the table from real misrecognitions in the bench output.

**c) Optional: faster-whisper on CUDA.** When `LLM_PROVIDER` resolves to Groq, the GPU is free.
`ORBIT_WHISPER_DEVICE=auto|cpu|cuda`, with `compute_type=float16` on CUDA. On any failure loading CUDA (missing cuDNN/cuBLAS DLLs
on Windows), log the reason and fall back to CPU int8.

**Accuracy gate (required for 4.6).** Extend `python -m orbit.voice.run bench` to accept engines (`--engines whisper:small.en
parakeet moonshine groq sarvam`) and print WER (`stt.word_error_rate`) and median and p90 latency on `tests/voice/fixtures`. A new
engine may become a default only if its WER is ≤ small.en's on the same fixtures and its median latency is lower.

### 4.7 Classifier call (B2)
File: `agent/loop.py` `_classify`.
- Use a dedicated small, fast model: `ORBIT_GROQ_CLASSIFIER_MODEL`. Pick a currently available small Groq model (check the model
  list), and send `max_tokens` of about 5 with `temperature=0`. With Ollama, keep the current model.
- Optional, behind a flag: speculative parallel start. Begin the chat stream while the classifier runs, and discard it if the answer
  is "action". Only do this after 4.8 lands.

### 4.8 Streaming the LLM reply into TTS (B1) — biggest change, do last
Scope: the **friend-mode chat path only** (`AgentLoop._chat`). It's a single LLM call with no tools, its reply is spoken almost
whole, and the Phraser doesn't rewrite it. Leave the action path (`_llm_loop`) unchanged: tool loops and the Phraser rewrite need
the full text, and spoken action replies are capped at 2 sentences anyway.

1. **LLM:** add `chat_stream(messages) -> Iterator[str]` (text deltas) to `GroqLLM` and `OllamaLLM`.
   - Groq: `stream: true`; read the response with `client.stream("POST", ...)` and parse SSE `data:` lines until `[DONE]`. Keep
     the 429 fallback-model behaviour for the request that opens the stream.
   - Ollama: `client.chat(..., stream=True)`.
   - Add it to the `LLM` Protocol as optional (`hasattr` check). Anything without it uses `chat()`.
2. **Agent:** `handle(text, sentence_sink=None)`. In `_chat`, when a sink is given and the LLM can stream:
   - Parse the emotion tag from the head of the stream first. Read `friend.split_emotion` to learn the exact format. Speaking can't
     start until the emotion is known, because `SarvamTTS` fixes the emotion per reply.
   - Run an **incremental spoken-sentence splitter**: buffer deltas, emit a sentence at `.`, `!`, `?` or `।` followed by whitespace.
     Apply the same cleaning `spoken_summary` + `strip_emoji` would (no markdown, URLs or code), and stop emitting after
     `CHAT_SPOKEN_MAX_SENTENCES` / `CHAT_SPOKEN_MAX_CHARS`.
   - Still return the full reply string and call `_remember_chat` exactly as now.
   - Unit-test that the concatenated streamed sentences equal `spoken_summary(strip_emoji(full_reply), 5, 520)` for a set of
     replies, minus the "The rest is on screen." suffix rule. Decide explicitly whether that suffix is still spoken at the end.
3. **TTS:** `StreamingTTS.speak_iter(sentences: Iterable[str]) -> SpeakStats`. Same player loop and stats, but the producer pulls
   sentence text from a thread-safe queue instead of splitting one string. Sarvam prefetch applies to queued sentences. `speak(text)`
   becomes `speak_iter(split_for_speech(text))`. `FallbackTTS` needs a matching `speak_iter`. On engine failure before the first
   audio, fall back to the next engine with the sentences received so far plus the rest.
4. **VoiceLoop:**
   - On the first sentence from the sink: finish or cancel any filler, run the same mic/barge-in setup as `_speak`, switch to
     `State.SPEAKING`, and start `tts.speak_iter(queue)`.
   - Mark `reply` when the first sentence arrives. Add a new mark `reply_done` for the full reply, and update `TurnTimer.summary`.
   - Call `ui.reply(...)` and print the CLI line when the agent future completes.
   - Agent error mid-stream: stop TTS, then speak the error line.
   - A confirm request (`self.confirm.poll()`) can't happen on the chat path (no tools). Assert that, or handle it by stopping speech.
   - `_tick_thinking` and `_tick_speaking` must not both act on the same turn. Keep exactly one owner of `self.future`.
   - Flag: `STREAM_CHAT_REPLIES = True`. When off, behaviour is byte-for-byte as before.

### 4.9 Optional later: streaming STT while the user speaks
sherpa-onnx `OnlineRecognizer` (a streaming Zipformer or Nemotron streaming model), fed from `_tick_listen` frame by frame. At "end",
only the tail is left to decode. Local only. Gate it with the same accuracy bench. Not part of this plan's required work.

---

## 5. Measuring

- Baseline before any change, then after each phase. Use the `[turn]` lines printed by `VoiceLoop` (`TurnTimer.summary`) and the
  monitor's timings.
- Use the same 20 spoken requests for every run: 8 rule-matched commands ("volume 30", "open notepad"), 6 LLM commands, 6 friend-mode
  chats. Push-to-talk and wake word separately.
- Record the median and p90 of: transcribe, agent, reply→first audio, and end of speech→first spoken word.
- Results go in `docs/perf/voice-latency.md`: one table per phase, plus the config used and the provider combination.

## 6. Risks

| Risk | Mitigation |
|------|-----------|
| A persistent stream returns early, so the mic hears Orbit's tail | Wait `stream.latency` after the last block (4.1) |
| A new STT engine misspells names | Vocabulary post-correction (4.6b) plus the WER gate |
| Speculative and cloud STT burn rate limits | Count discarded requests; keep the cool-down |
| Streaming breaks the state machine (fillers, barge-in, confirm) | Single owner of `self.future`; tests for each transition; flag off restores the old path |
| A mid-sentence split sounds choppy | Split only at clause punctuation, and only the first sentence |
| CUDA DLLs missing on Windows | Auto-fallback to CPU with a logged reason |

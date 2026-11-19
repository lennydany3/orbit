# Orbit

A local assistant that operates your Windows PC from plain-language text requests
("open Spotify", "volume 30", "open my GitHub"). Runs on a local model (Qwen2.5 7B via Ollama), no paid API.

**Status: Step 4 of 5 (Spotify API done).** Open apps, windows, media keys, volume, websites, YouTube, and Spotify search/play/transfer. Vision fallback (Step 5) is optional and not built.

## Setup (Windows 11, Python 3.12)

1. Install [Ollama](https://ollama.com) and start it. Install Brave or Google Chrome (the browser layer drives it).
2. `ollama pull qwen2.5:7b` (about 4.7 GB).
3. In this folder: `python -m venv .venv`
4. `.venv\Scripts\activate`
5. `pip install -e ".[dev]"`
6. `python -m orbit` (add `--no-llm` for rules only; `python -m orbit "volume 30"` runs one request; `--fake-tools` adds the Step 1 test tools).
7. `pytest` to run the tests.
8. Optional, Spotify: create an app at developer.spotify.com (redirect URI `http://127.0.0.1:8888/callback`, Web API), add your account under User Management, then `setx SPOTIFY_CLIENT_ID "<client id>"`, open a new terminal and run `python -m orbit auth spotify` once.

## How it works

```
text -> fast rules (no model) --match--> tool call
     -> LLM (tool calling)  ----------->     |
                                      validate (pydantic)
                                      safety gate (risky = ask you first)
                                      router: api -> os -> browser (best layer first)
                                      verify the effect; retry once; fall back to next layer
                                      result -> back to the model / final reply
```

- Everything the agent does is printed first and logged to `logs/orbit.log` (secrets redacted).
- Ctrl+C stops a running request. Step cap: 8 model round-trips. Stops after 3 failed tool calls in a row.
- Name lives in `src/orbit/config.py` (`APP_NAME`). Limits and the model name are there too.

## Spotify (API layer)

The router prefers the API layer over OS and browser. Login uses PKCE, so there is no client secret anywhere; the refresh token lives in the Windows Credential Manager (entry `Orbit/spotify-refresh-token`), never in a file or the logs. Tools: play (song, artist, album, playlist, or "my favourite music" = your liked songs), transfer playback (to a device by name, optionally with a volume; "this" = the Spotify app on this PC), set a device's volume, play/pause/next/previous on whichever device is playing, list devices, now playing. Volume follows the sound: when Spotify is playing on a speaker or phone, "reduce the volume", "make it louder" and "volume 40" change THAT device's volume (relative or absolute; the PC's volume would change nothing you can hear); when it plays on this PC or something else is playing, they change the PC volume. "echo pop volume 50", "in echo pop volume 50%", "reduce the volume in echo pop" and "set the volume of the speaker to 30" name the device. "speaker", "phone" and "tv" work as device kinds ("switch to speaker"), and are never guessed when there are two of a kind. "pause", "play", "next" and "previous" go through Spotify itself whenever Spotify is the current media player (it may be playing on a speaker, which the PC media keys cannot reach); if a media key does nothing and Spotify is involved, Orbit asks Spotify directly. Right after a Spotify request, "switch to echo" also means Spotify (so they reach a speaker too); otherwise "pause" uses the PC media keys. Spotify tools only appear when `SPOTIFY_CLIENT_ID` is set.

## Browser layer

Two ways to use a browser, chosen with `--browser-mode` (or `ORBIT_BROWSER_MODE`):

- **`mine` (default): your own Brave window and profile.** "open my github", "open youtube", "search cats" open the link in your **Personal** profile (change with `ORBIT_BRAVE_PROFILE`), where you are already logged in. Orbit can then **read and click inside that page**: "click the ishowspeed live", "what page am I on", "play some video", "fullscreen". This uses Windows UI Automation (what screen readers use), not remote debugging, which Chromium blocks for a profile that is in use, and sites that distrust automated browsers (Google sign-in) do not object to it. Navigation: "go back", "go forward", "reload", "go to the home page" and "go to youtube home page" press the browser's own buttons or load the address in the **current tab** ("open ..." opens a new tab). "go back" means the browser, not the previous song (say "previous song" for that). A link that ignores the accessible press is opened by its own address. Tabs: "list my tabs", "close this tab", "close all other youtube tabs", "close all youtube tabs", "switch to the github tab". Closing keeps the tab you are on, asks first when 4 or more tabs would close, refuses to close every tab (that is the window: say "close brave"), and never sends Ctrl+W unless Brave has the keyboard. Clicks forgive typos and filler words ("clcik theh traver walles viedo" finds "Trevor Wallace Making ...") but never invent a match: if nothing on the page is close, it says so and lists what it can see. Limits: it sees links, buttons and fields by their names, not images or video frames; it works on the Brave window in front (or the top one); a click is confirmed by the page, address or the control's own label changing, so a click with no visible effect is reported as failed; clicking something that looks risky (buy, send, delete...) asks you first, and Orbit refuses to press a risky-looking control you did not name.
- **`orbit`: Orbit's own automated window** (also used for "play X on youtube" + fullscreen, which Orbit verifies from the video element). It has a separate profile in `.browser-profile/` and never touches your everyday profiles. Sites that distrust automated browsers refuse sign-in there (Google shows "Couldn't sign you in"); GitHub's own username/password login works. Switch the browser itself with `--browser chrome`.

"open my github" opens `github.com/lennydany3` (change with `ORBIT_GITHUB_USER`); "open github" opens the site. Words like "in brave" are ignored. Only http(s) links are opened. Clicking a button whose text looks risky (buy, send, delete, pay...) asks you first. Web search uses DuckDuckGo.

## Voice (in progress) and privacy

Voice lives in `src/orbit/voice/` (test bench: `python -m orbit.voice.run --help`).

- **Local by default, cloud when you add keys.** Microphone capture, wake word, voice-activity detection, echo cancellation and hotkeys always run on this PC. With no API keys, speech-to-text (Whisper `small.en` on the CPU), the spoken reply (Piper) and the language model (Ollama) are local too, and nothing leaves the PC. With keys (next section), the speech clip, the reply text and your requests go to Sarvam and Groq. Orbit prints which mode it is in when voice starts.
- **Nothing is recorded while idle.** Audio is only kept in memory for the turn being processed. No audio is saved by default: the only recordings are the test clips you make yourself with `python -m orbit.voice.run record` / `record-wake` (git-ignored, local only).
- **Models:** `python -m orbit.voice.fetch_models ...` is the only thing that downloads voice models, and only when you run it.

### Cloud mode: Groq (language model) + Sarvam AI (speech-to-text and voice), for lower latency

1. Get keys: Groq at <https://console.groq.com/keys>, Sarvam at <https://dashboard.sarvam.ai>.
2. Copy `.env.example` to `.env` in the project folder and paste them in (`.env` is git-ignored; a variable set in your terminal overrides the file):
   ```
   GROQ_API_KEY=gsk_...
   SARVAM_API_KEY=...
   ```
3. Check them: `python -m orbit.cloud_check` (one tiny request each, prints the seconds each took).
4. Run as usual: `python -m orbit --voice`. Each part switches to the cloud on its own when its key exists (`ORBIT_LLM`, `ORBIT_STT`, `ORBIT_TTS` = `auto` | cloud name | local force one side; `--llm ollama` forces the local model for one run).

What goes where: only the speech clip trimmed by the local silence detector is uploaded (never the always-on microphone stream), the wake word and echo cancellation stay local, and Orbit saves no audio. If the internet or a service fails, Orbit falls back to Whisper / Piper / Windows voice for the next minute instead of waiting on timeouts; the language model has no automatic fallback (use `--llm ollama`). The Sarvam voice plays through the same output path as Piper and records what it plays for echo cancellation, so interrupting it by voice works the same. Model and voice names (`ORBIT_GROQ_MODEL`, `ORBIT_SARVAM_*`) are settings because providers rename them; `ORBIT_LANGUAGE` picks the language you speak (`en-IN`, `hi-IN`, ...).

### Friend mode: talk to Orbit about anything

Orbit is also a friend you can talk to, not just a PC remote. Tell it about your day and it reacts and encourages you; ask it for a roast; ask a doubt ("what's the difference between a process and a thread?") and it explains in plain words with an example; say you're stressed or low and it slows down and is kind. It remembers the conversation for the session (last 8 exchanges, in memory only; `clear` forgets it).

- **How it decides chat or command.** PC commands ("open spotify", "volume 30", "what's playing?") still go through the rules and tools, exactly as before. Anything else is chat, answered by a friend persona with no tools (so it is also cheaper on Groq's token limit). Obvious cases are decided by simple word checks; only unclear ones cost one tiny extra model call; right after a chat, vague follow-ups ("and then?") stay chat. Turn it off with `--no-friend` or `ORBIT_FRIEND=0`.
- **Emotion.** Every chat reply starts with a hidden feeling tag (neutral, happy, excited, proud, caring, sad, teasing, calm, serious). The tag is never shown or spoken (the console prints `[feeling] proud`); it sets the voice's speed and expressiveness (`EMOTION_STYLE` in `voice/config.py`). Honest limit: Sarvam's voice only lets us change pace and how varied the delivery is, so most of the feeling comes from the words the model chooses (the voice is not a real "sad" or "laughing" voice). Offline, Piper only changes speed.
- **Spoken whole.** Chat replies are spoken in full (up to 5 sentences), and the conversation stays open for 8 s after a reply without the wake word, so you can just keep talking.
- **Safety.** Roasts are playful and only about what you did (procrastination, snacks, sleep), never looks, family, religion, money or health; if you sound low it skips the jokes; if you say something that sounds like hurting yourself it drops the humour, answers seriously and encourages you to reach out to someone you trust or a local helpline. It is an AI model, not a therapist, and it cannot browse the internet, so double-check facts that matter.
- **Settings (`.env`).** `ORBIT_USER_NAME` (what Orbit knows you as), `ORBIT_FRIEND`. If the big Groq model hits its free-tier token limit, that one request is answered by the small model (`ORBIT_GROQ_FALLBACK_MODEL`) instead of waiting. Chat uses more tokens than a command (persona + history), so a very fast conversation on the free tier can still hit the limit.

### Talking to Orbit: `python -m orbit --voice`

Everything the text REPL does, by voice. Start a turn by **holding Right Ctrl** while you speak and releasing it, or by saying **"hey orbit"** (add `--no-wake` for hold-to-talk only; `--mic "<name>"` picks the microphone). Orbit transcribes what you said, runs the same `agent.handle(text)` as the text REPL (the agent is unchanged), and speaks a short reply.

- **Short, natural spoken replies.** The agent's terse tool reports are rephrased for speech only ("asked 'Spotify Premium' to close" is spoken as "Okay, closing Spotify."; emoji, "(204)" counts and internal tool names are dropped; the opener varies). When it did not understand, it says what it heard and asks again. One or two sentences, never URLs, code or lists; the original reply is always printed in the terminal, with a `[says]` line when the spoken version differs. `CONVERSATIONAL = False` in `voice/config.py` reads the reports out as they are.
- **No dead air.** If the agent takes longer than about 2.5 s, Orbit says "One moment." (and "Still working on it." after about 8 s). `FILLERS = False` turns that off.
- **Questions get answers.** When Orbit asks you something ("Would you like to play something?"), it listens for 6 s without the wake word, so you can just reply.
- **Risky actions ask by voice.** "Do you want me to close window Notepad? Say yes or no." Only a clear yes runs it; silence, "maybe" or anything unclear is a no.
- **Follow-up window.** For 2 s after a reply you can say the next request without the wake word (`--no-follow-up` turns it off).
- **Interrupt it by voice.** Say **"hey orbit"** while Orbit is talking: it mutes the instant the wake word model fires, and stops for good once the Whisper check agrees (if the check says no, it carries on). Holding the key also interrupts at once. The microphone stays open while Orbit speaks (it is off while Orbit transcribes and thinks). To avoid hearing itself, Orbit subtracts its own voice from the microphone (echo cancellation, `AEC` in `voice/config.py`); with earbuds or headphones there is no echo to begin with. A reply that contains the word "orbit" is spoken half-duplex, so Orbit cannot trigger itself. Switch off with `BARGE_IN = False`.
  - **Does it work with your speaker and microphone?** Run `python -m orbit.voice.run echotest`: it plays a reply, measures how much your microphone hears it and how much the canceller removes, plays a second voice saying the wake word over the reply, and tells you the result. Honest limits: it only removes sound Orbit *knows* (its own voice), not music or a TV; Bluetooth speakers delay the sound by an unknown amount (the filter covers about 0.5 s); some laptop microphones process the sound in a way a linear filter cannot undo. The canceller measures its own benefit and passes the microphone through untouched when it does not clearly help, and was verified in simulation (including a person talking over the echo, and 15 ms of timing jitter like real hardware has) but not on the author's own speakers, whose sound the laptop microphone barely picks up.
- **Not fooled by background sound.** The no-wake follow-up window stays closed while music or a video is playing (lyrics are not commands; say "hey orbit" or hold the key), filler "transcripts" like "Uh..." and Whisper's silence hallucinations ("Thank you.") are ignored, and a model reply that is a raw tool call is never read out loud. `FOLLOW_UP_WHILE_MEDIA = True` restores the old behaviour.
- **Faster.** The language model is loaded when voice starts and kept loaded (Ollama otherwise unloads it after a few minutes and the next request waits ~20 s); transcription starts at a 0.55 s pause and is kept only if you really stopped (saves about 0.4 s a turn); "play X from / on / in / via Spotify" is a rule, not a 3 to 8 s model call.
- **Kill switch:** Ctrl+Alt+Esc stops the microphone instantly; Orbit carries on in text mode. If voice cannot start at all (no microphone, a missing model) it says why and falls back to the text prompt instead of crashing.
- **States you can see:** `[listening]`, `[thinking]`, `[speaking]`, plus the wake-word and confirmation lines. Every state change is also logged with a timestamp to `logs/voice.log` (add `--verbose` in the dev bench to print them). What you said is only written to that log if `LOG_TRANSCRIPTS = True` in `voice/config.py`; audio is never written.
- **Where the time goes** (printed after each turn): on this PC, from the end of your speech to the first spoken word is about 2.3 s (speech-to-text about 2.0 s, the agent for a rule command about 0.1 s, the voice about 0.3 s) plus the 0.9 s end-of-speech wait, so about 3.2 s after you stop talking; with hold-to-talk the key release ends the turn, so about 2.8 s. A busy PC makes it slower (one run took 5.5 s while the system was at 85% load). Speech-to-text dominates; options are listed in "Known limits".

Dev bench without a microphone or a real agent: `python -m orbit.voice.loop --echo-agent --wav replay.wav` (a stand-in agent that only repeats your request; "close notepad" pretends to be risky so you can try the spoken yes / no).

### Wake word: "Hey Orbit"

openWakeWord ships no "Orbit" model, so `hey_orbit` is trained here, offline, in about 6 minutes: `python -m orbit.voice.run train-wake` (needs the feature models: `fetch_models wakeword hey_mycroft`). It learns from "hey orbit" spoken by your Piper voices at many speeds, with noise and room echo, against ordinary speech and look-alikes ("hey mycroft", "a little bit"). The result is `models/openwakeword/hey_orbit.npz` (not committed).

- **Train it on your own voice** for much better results: `python -m orbit.voice.run record-wake --device "Microphone Array" --count 25 --talk 60`, then `train-wake` again. These clips are saved locally in `tests/voice/fixtures_wake/` (git-ignored).
- **Every candidate is double-checked** by a quick local Whisper pass (`tiny.en`) that must hear "orbit" in the last 2.4 s, so look-alikes are rejected. The wake-word classifier itself runs all the time at under 1% CPU; the check runs only on candidates.
- **Honest numbers (V4, synthetic voices only):** on Windows David and Zira at three speeds, which were not used for training, 22 of 24 "hey orbit" clips woke it and 0 of 84 other clips did. Real rooms, TV and your voice are a different test: use `python -m orbit.voice.run wake` to measure false wakes yourself.
- Pre-trained stand-ins still work: `--word hey_mycroft` (or `alexa`, `hey_jarvis`; note "Jarvis" alone triggers `hey_jarvis`).

## Known limits (honest list)

- A phone or speaker only shows up as a Spotify device while its Spotify app is open or was used recently (Echo speakers can be slow to wake and sometimes need a second try). Spotify's own "transfer" call sometimes answers HTTP 500 for this PC; Orbit then restarts the current song on the target device instead, and says so.

- Spotify needs Premium for play/transfer. In development mode only accounts listed under User Management (max 5) can log in.
- Spotify must be running on the PC for it to appear as a device; the multi-step request opens it first. Spotify's own volume is separate from Windows volume.
- Playlist playback can only be confirmed as "something is playing", not that it came from that playlist. "Favourite music" means your liked songs (the 50 most recent).
- Spotify's API sometimes does not report what context is playing, so artist/album playback is verified from the current track.

- YouTube "playing" is verified from the video element, so a pre-roll ad counts as playing. Cookie-consent pages (EU) must be accepted once by hand.
- `browser_click` finds things by visible text and can't be retried automatically (a second click could undo the first); if nothing visibly changes it reports failure.
- Pages that block automation (captchas, logins with 2FA) cannot be driven.
- Media keys go to whatever Windows treats as the current media session (often the last app that played). `pause`/`play` are safe to repeat; `next`/`prev` are verified by track change, which can't be confirmed for live streams or one-track playlists.
- Opening an app finds it by Start-menu name; if two apps match ("visual studio") it asks instead of guessing. Store apps can show their window under a generic host, so window matching may miss some.
- Windows may refuse to bring a window to the front; the tool then reports failure rather than pretending.
- `open <name>` is a rule for apps only. Websites ("open github") come with the browser layer in Step 3.
- The 7B model sometimes misreads amounts ("quieter, around 20 percent" became 67%). Plain commands use rules, not the model.
- "Works on every app" is not achievable. Games and custom-drawn UIs may not be controllable.
- **Administrator windows (Task Manager, anything started "as administrator") cannot be controlled by a normal program: Windows refuses to let Orbit close, minimize or move them.** Orbit now checks for this first and says so ("'Task Manager' runs as administrator, so Windows does not let Orbit close it") instead of reporting a close that never happened. To control such windows, open the terminal with "Run as administrator" and start Orbit from there; that gives Orbit (and anything it runs) full administrator rights, so only do it when you need it.
- **Closing a window is not quitting the app.** Many apps (ChatGPT, Spotify, Discord ...) keep running in the system tray after their window closes. "close chatgpt" closes the window and tells you it is still running in the background; **"quit chatgpt"** (also "exit", "kill", "force close", "close chatgpt completely") ends all of the app's processes. It always asks first, because quitting can lose unsaved work. It never quits Windows itself, system services, the Ollama model server, or the program Orbit is running inside.
- A 7B local model makes mistakes on vague or multi-step requests; expect 10-20 s for those. Simple commands skip the model.
- Memory is short-term: the model sees your last 6 requests (capped at about 5,000 characters) so "close it" works. It is forgotten when you quit; type `clear` to forget sooner. Rule commands like "pause" are remembered but never need memory.
- "Close" asks the window to close like its X button (nothing is force-killed). If the app shows a "save changes?" box the window stays open and Orbit reports that. A window whose title looks unsaved (starts with `*`) asks you first. Orbit never closes its own terminal.
- Ctrl+C during a model call takes effect when that call returns (at most the 120 s timeout).

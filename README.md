# Orbit

A desktop assistant for Windows 11 that you talk to. Say "hey orbit, open Spotify and play lofi on the Echo", "volume 30",
"close Notepad" or "open my GitHub" and it does it, using whichever way is most reliable (the Spotify API, Windows itself, or
your browser). It is also a friend you can chat with. It lives in the system tray, and a small orb floats up over your work
only while Orbit is doing something.

<p align="center">
  <img src="docs/screenshots/sphere-speaking.png" alt="Orbit's window: the particle sphere while Orbit speaks a reply" width="860">
</p>

- **Local by default, cloud when you add keys.** With no keys, the language model (Qwen2.5 7B via Ollama), speech-to-text
  (Whisper) and the voice (Piper) all run on this PC and nothing leaves it. Add a Groq and/or Sarvam key and each part switches
  to the cloud on its own, for much lower latency.
- **Safe by design.** Risky actions (closing a window, quitting an app, buying, sending, deleting) ask you yes or no first, by
  voice, by clicking, or with a key chord. Everything Orbit does is printed first and logged with secrets redacted.
- **Tested.** 1172 Python tests and 193 Rust tests pass.

## Contents

[What it can do](#what-it-can-do) · [Setup](#setup-windows-11) · [How it works](#how-it-works) · [The desktop app](#the-desktop-app) ·
[Voice](#voice) · [Spotify](#spotify-api-layer) · [Browser](#browser-layer) · [Friend mode](#friend-mode) ·
[Privacy](#privacy) · [Known limits](#known-limits-honest-list)

## What it can do

| Layer | What Orbit can do |
|---|---|
| **API** (best, tried first) | Spotify: play a song, artist, album, playlist or your liked songs; transfer playback to a speaker or phone; per-device volume; play, pause, next, previous; now playing. |
| **OS** (Windows itself) | Open and close apps ("quit" ends every process of an app), list, focus, move and arrange windows across monitors, volume and mute, media keys, the time and date, click UI controls in any app (UI Automation, or OCR as a fallback) and read what a window shows. |
| **Browser** | Open sites and searches, read and click inside your own Brave page, navigation (back, forward, reload, home), tabs (list, switch, close), YouTube play and fullscreen. |
| **Chat** | Talk about your day, ask a doubt, get encouragement or a roast. Anything that is not a PC command is chat. |
| **Your own commands** | Make a phrase that runs a list of steps: open apps, websites and folders, press keys, type text, wait. |

Several requests in one sentence work ("open Spotify, then play my favourite music on the speaker"). Simple commands are handled
by fast rules with no model call.

## Setup (Windows 11)

**Orbit (Python 3.12):**

1. Install [Ollama](https://ollama.com) and start it, then `ollama pull qwen2.5:7b` (about 4.7 GB). Install Brave (or Google
   Chrome; `--browser chrome`).
2. In this folder: `python -m venv .venv`, then `.venv\Scripts\activate`.
3. `pip install -e ".[dev,voice,ipc]"` (`dev` for the tests, `voice` for the microphone and speech, `ipc` for the desktop app).
4. Text mode: `python -m orbit` (add `--no-llm` for rules only; `python -m orbit "volume 30"` runs one request and exits;
   `--fake-tools` adds the Step 1 test tools).
5. Voice (optional), download the models once; this is the only thing that touches the network for voice, and only when you run it:
   ```
   python -m orbit.voice.fetch_models whisper small.en tiny.en
   python -m orbit.voice.fetch_models piper en_US-amy-medium
   python -m orbit.voice.fetch_models wakeword hey_mycroft
   python -m orbit --voice
   ```
6. Spotify (optional): create an app at developer.spotify.com (redirect URI `http://127.0.0.1:8888/callback`, Web API), add your
   account under User Management, then `setx SPOTIFY_CLIENT_ID "<client id>"`, open a new terminal and run
   `python -m orbit auth spotify` once.
7. Cloud (optional): copy `.env.example` to `.env` and add `GROQ_API_KEY` / `SARVAM_API_KEY` (see [Cloud mode](#cloud-mode-groq--sarvam-ai)).

**The desktop app (Rust, optional)**, in `ui/`: install [Rust](https://rustup.rs) (the toolchain is pinned to 1.99.1 and
installs itself), then `cargo run --release -- --spawn`. It starts Orbit for you and puts it in the tray.

**Tests:** `pytest` (Python) and, from `ui/`, `cargo test` (Rust; the link tests use this project's `.venv`).

## How it works

```
text or voice -> fast rules (no model) --match--> tool call
              -> LLM (tool calling)  ----------->     |
                                               validate (pydantic)
                                               safety gate (risky = ask you first)
                                               router: api -> os -> browser (best layer first)
                                               verify the effect; retry once; fall back to the next layer
                                               result -> back to the model / final reply
```

- Everything the agent does is printed first and logged to `logs/orbit.log` (secrets redacted).
- Ctrl+C stops a running request. Step cap: 8 model round-trips. It stops after 3 failed tool calls in a row.
- An action is only reported as done when Orbit checked the effect (a window closed, a track changed, a page changed).
- The name lives in `src/orbit/config.py` (`APP_NAME`), with the limits and model names.

## The desktop app

Orbit's face is `orbit-ui` (in `ui/`, see [ui/README.md](ui/README.md) for everything): a particle sphere drawn on the GPU. It
lives in the tray. When Orbit does something, a small orb floats up at the bottom right, above the taskbar, showing what it is
doing and how loud the voice is; a few seconds after things go quiet it fades away. The orb never takes focus and clicks pass
through it, so it never gets in your way.

```
cd ui
cargo run --release -- --spawn        # start Orbit (microphone, wake word, model) and keep it in the tray
cargo run --release -- --spawn-demo   # a scripted turn with a yes/no question, no microphone needed
orbit-ui --autostart on               # start with Windows (off to undo; status to look). Never set unless you ask.
```

Every state has its own motion: idle, wake, listening (the surface follows your voice), transcribing, thinking, confirming
(an amber pulse), speaking (waves with the voice level), follow-up, success, error and muted.

<table>
  <tr>
    <td align="center"><img src="docs/screenshots/sphere-listening.png" alt="Listening: the surface follows the microphone level" width="420"><br><sub><b>Listening</b>: the surface follows your voice</sub></td>
    <td align="center"><img src="docs/screenshots/sphere-thinking.png" alt="Thinking: tilted spin with travelling sparks" width="420"><br><sub><b>Thinking</b>: tilted spin, travelling sparks</sub></td>
  </tr>
</table>

### Risky actions ask first

When Orbit needs a yes or no, the question appears above the sphere. Answer by clicking, by voice, or with **Ctrl+Alt+Y** /
**Ctrl+Alt+N** (those two chords are only held while a question is open, so they never take the keys from another program the
rest of the time). Esc says no.

<p align="center">
  <img src="docs/screenshots/confirm.png" alt="The question 'Do you want me to close window Notepad?' with No and Yes buttons" width="760">
</p>

### Settings

Every change is saved the moment you make it, to `settings.json` in the project folder (git-ignored; `ORBIT_SETTINGS` moves it).
A hand-edited file Orbit cannot fully read does not stop anything: the bad entry is left out, the page lists what, and the first
save keeps the old file as `settings.json.bak`.

**Hotkeys.** Click a chord and press the new keys (Esc cancels). The orb, yes and no chords take effect at once; one Windows
will not give Orbit is refused with a reason and nothing changes. The hold-to-talk key is picked from a list and Orbit follows
it within a second, with no restart.

<p align="center">
  <img src="docs/screenshots/settings-hotkeys.png" alt="The Hotkeys settings page" width="760">
</p>

**Commands.** Say a phrase and Orbit runs a list of steps in order, without asking the language model. Say it exactly
(filler like "please" and a leading "run" are fine). A command that presses keys or types asks one yes or no first, listing
everything it will do. Steps that open a folder only open it: nothing is ever run, and the language model never sees these
commands and cannot press keys or type. Switch a command off without deleting it.

<table>
  <tr>
    <td align="center"><img src="docs/screenshots/settings-commands.png" alt="The Commands page: two commands, one switched off" width="400"><br><sub>Your commands</sub></td>
    <td align="center"><img src="docs/screenshots/settings-editor.png" alt="The command editor: name, phrases and steps" width="400"><br><sub>The editor: phrases, then steps</sub></td>
  </tr>
</table>

**Sounds.** A soft tone when the orb fades in and another when it fades away, with a switch and a volume slider (0 to 100).
Only a real show or hide makes a sound, and it costs nothing at rest.

<p align="center">
  <img src="docs/screenshots/settings-sounds.png" alt="The Sounds settings page" width="620">
</p>

### Tray, cost and safety of the link

- **Tray menu** (right-click): show or hide the orb, open the Orbit window, Settings, quit (which also stops the Orbit it started).
  Ctrl+Alt+O, or a click on the tray icon, pins the orb on screen. Windows 11 hides new tray icons in the "hidden icons"
  flyout: turn Orbit on in Settings, Personalization, Taskbar, Other system tray icons to keep it in view.
- **Cost at rest:** about 0% of one core and about 100 MB with the orb hidden; about 10% of one core while it animates.
- **Only this PC, only the window.** The link is a WebSocket on `127.0.0.1` (port 8765, `--ipc-port` or `ORBIT_IPC_PORT`) and
  needs a random token that the app hands Orbit itself. A connection from a web page is always refused (a browser always sends
  its page's origin), so no website can listen to your voice turns or answer a question for you. `ORBIT_IPC_ORIGINS` names pages
  you want to allow.
- **A face can send exactly one thing:** the yes or no for an open question. It cannot send requests or press keys. Audio never
  leaves Orbit: the face gets one loudness number about 20 times a second, only while a voice is being heard.
- **Clean shutdown.** The app keeps a pipe open to the Orbit it started, so closing or crashing it stops Orbit cleanly: there is
  never an Orbit listening with nobody to show it.
- **Run it by hand:** `python -m orbit --voice --ipc` prints the link to give `orbit-ui --url ...`. Without a microphone,
  `python -m orbit.ipc.demo` (add `--confirm` for the question) plays a scripted turn on the same link.

## Voice

Voice lives in `src/orbit/voice/` (test bench: `python -m orbit.voice.run --help`). Start it with `python -m orbit --voice`.

Start a turn by **holding Right Ctrl** (changeable in Settings) while you speak, or by saying **"hey orbit"** (`--no-wake` for
hold-to-talk only; `--mic "<name>"` picks the microphone). Orbit transcribes what you said, runs the same `agent.handle(text)`
as the text prompt, and speaks a short reply.

- **Short, natural spoken replies.** The agent's terse tool reports are rephrased for speech ("asked 'Spotify Premium' to close"
  is spoken as "Okay, closing Spotify."). When it did not understand, it says what it heard and asks again. One or two
  sentences, never URLs, code or lists; the full reply is always printed. `CONVERSATIONAL = False` in `voice/config.py` reads the
  reports out as they are.
- **No dead air.** If the agent takes longer than about 2.5 s, Orbit says "One moment." (and "Still working on it." after about
  8 s). `FILLERS = False` turns that off.
- **Questions get answers.** After Orbit asks you something, it listens for 6 s without the wake word. After any reply you have
  2 s for the next request without the wake word (`--no-follow-up` turns that off).
- **Risky actions ask by voice.** "Do you want me to close window Notepad? Say yes or no." Only a clear yes runs it; silence,
  "maybe" or anything unclear is a no.
- **Interrupt it by voice.** Say "hey orbit" while Orbit talks: it mutes the instant the wake word model fires and stops for good
  once a Whisper check agrees. Holding the key also interrupts at once. To avoid hearing itself, Orbit subtracts its own voice
  from the microphone (echo cancellation; earbuds need none). `BARGE_IN = False` switches this off. Run
  `python -m orbit.voice.run echotest` to measure whether it works with your speaker and microphone. Honest limits: it only
  removes sound Orbit *knows* (its own voice), not music or a TV; Bluetooth speakers delay the sound by an unknown amount
  (the filter covers about 0.5 s); some laptop microphones process the sound in a way a linear filter cannot undo. It was
  verified in simulation, not on the author's own speakers.
- **Not fooled by background sound.** The no-wake follow-up stays closed while music or a video is playing, filler "transcripts"
  ("Uh...") and Whisper's silence hallucinations ("Thank you.") are ignored, and a raw tool call is never read out loud.
- **Faster.** The language model is loaded when voice starts and kept loaded (Ollama otherwise unloads it after a few minutes and
  the next request waits about 20 s); transcription starts at a 0.55 s pause and is kept only if you really stopped.
- **Kill switch:** Ctrl+Alt+Esc stops the microphone instantly; Orbit carries on in text mode. If voice cannot start (no
  microphone, a missing model) it says why and falls back to the text prompt instead of crashing.
- **Where the time goes** (printed after each turn): on the author's PC, about 3.2 s from the end of speech to the first spoken
  word with local models (speech-to-text about 2.0 s dominates), about 2.8 s with hold-to-talk. A busy PC is slower. Cloud mode
  is the way to bring this down.

Dev bench without a microphone or a real agent: `python -m orbit.voice.loop --echo-agent --wav replay.wav` (a stand-in agent that
repeats your request; "close notepad" pretends to be risky so you can try the spoken yes / no).

### Cloud mode: Groq + Sarvam AI

Groq runs the language model; Sarvam does speech-to-text and the voice. Both are optional and independent.

1. Get keys: Groq at <https://console.groq.com/keys>, Sarvam at <https://dashboard.sarvam.ai>.
2. Copy `.env.example` to `.env` and paste them in (`.env` is git-ignored; a variable set in your terminal overrides the file):
   ```
   GROQ_API_KEY=gsk_...
   SARVAM_API_KEY=...
   ```
3. Check them: `python -m orbit.cloud_check` (one tiny request each, prints the seconds each took).
4. Run as usual. Each part switches to the cloud when its key exists (`ORBIT_LLM`, `ORBIT_STT`, `ORBIT_TTS` = `auto` | the cloud
   name | the local engine; `--llm ollama` forces the local model for one run). `ORBIT_LANGUAGE` picks the language you speak
   (`en-IN`, `hi-IN`, ...); the other settings are listed in `.env.example`.

Only the speech clip trimmed by the local silence detector is uploaded (never the always-on microphone stream); the wake word
and echo cancellation stay local, and Orbit saves no audio. If the internet or a service fails, Orbit falls back to Whisper /
Piper / Windows voice for the next minute instead of waiting on timeouts; the language model has no automatic fallback (use
`--llm ollama`).

### Wake word: "Hey Orbit"

openWakeWord ships no "Orbit" model, so `hey_orbit` is trained here, offline, in about 6 minutes:
`python -m orbit.voice.run train-wake` (needs the feature models: `fetch_models wakeword hey_mycroft`). The result is
`models/openwakeword/hey_orbit.npz` (not committed).

- **Train it on your own voice** for much better results: `python -m orbit.voice.run record-wake --device "Microphone Array"
  --count 25 --talk 60`, then `train-wake` again. These clips stay local (git-ignored).
- **Every candidate is double-checked** by a quick local Whisper pass (`tiny.en`) that must hear "orbit", so look-alikes are
  rejected. The wake-word classifier runs all the time at under 1% CPU; the check runs only on candidates.
- **Honest numbers (synthetic voices only):** on Windows David and Zira at three speeds, which were not used for training, 22 of
  24 "hey orbit" clips woke it and 0 of 84 other clips did. Real rooms, TV and your voice are a different test: measure false
  wakes yourself with `python -m orbit.voice.run wake`.
- Pre-trained stand-ins still work: `--word hey_mycroft` (or `alexa`, `hey_jarvis`).

## Friend mode

Orbit is also a friend you can talk to, not just a PC remote. Tell it about your day and it reacts and encourages you; ask for a
roast; ask a doubt ("what's the difference between a process and a thread?") and it explains in plain words with an example; say
you are stressed or low and it slows down and is kind. It remembers the last 8 exchanges (in memory only; `clear` forgets them).

- **Chat or command.** PC commands ("open spotify", "volume 30", "what's playing?") go through the rules and tools exactly as
  before. Anything else is chat, answered by a friend persona with no tools. Obvious cases are decided by simple word checks;
  only unclear ones cost one tiny extra model call. Turn it off with `--no-friend` or `ORBIT_FRIEND=0`.
- **Emotion.** Every chat reply starts with a hidden feeling tag (neutral, happy, excited, proud, caring, sad, teasing, calm,
  serious) that sets the voice's speed and expressiveness. Honest limit: Sarvam's voice only lets us change pace and how varied
  the delivery is, so most of the feeling comes from the words. Offline, Piper only changes speed.
- **Spoken whole.** Chat replies are spoken in full (up to 5 sentences), and the conversation stays open for 8 s after a reply.
- **Safety.** Roasts are playful and only about what you did (procrastination, snacks, sleep), never looks, family, religion,
  money or health. If you sound low it skips the jokes. If you say something that sounds like hurting yourself it drops the
  humour, answers seriously and encourages you to reach out to someone you trust or a local helpline. It is an AI model, not a
  therapist, and it cannot browse the internet, so double-check facts that matter.
- **Settings (`.env`).** `ORBIT_USER_NAME`, `ORBIT_FRIEND`, `ORBIT_GROQ_FALLBACK_MODEL` (answers when the big model hits the
  free-tier token limit).

## Spotify (API layer)

The router prefers the API layer over OS and browser. Login uses PKCE, so there is no client secret anywhere; the refresh token
lives in the Windows Credential Manager (`Orbit/spotify-refresh-token`), never in a file or the logs. Spotify tools only appear
when `SPOTIFY_CLIENT_ID` is set.

- **Play** a song, artist, album, playlist, or "my favourite music" (your liked songs). **Transfer** playback to a device by
  name, optionally with a volume ("this" = the Spotify app on this PC). "speaker", "phone" and "tv" work as device kinds and
  are never guessed when there are two of a kind. **Control** play, pause, next, previous on whichever device is playing, list
  devices, now playing.
- **Volume follows the sound.** When Spotify plays on a speaker or phone, "reduce the volume", "make it louder" and "volume 40"
  change THAT device (relative or absolute); when it plays on this PC or something else is playing, they change the PC volume.
  Name a device to be explicit ("echo pop volume 50", "set the volume of the speaker to 30").
- **Pause, play, next, previous** go through Spotify itself whenever it is the current media player (a speaker cannot be reached
  by the PC media keys); if a media key does nothing and Spotify is involved, Orbit asks Spotify directly. Right after a Spotify
  request, "switch to echo" also means Spotify.

## Browser layer

Two ways to use a browser, chosen with `--browser-mode` (or `ORBIT_BROWSER_MODE`):

- **`mine` (default): your own Brave window and profile.** "open my github", "open youtube" and "search cats" open the link in
  your **Personal** profile (change with `ORBIT_BRAVE_PROFILE`), where you are already logged in. Orbit can then **read and click
  inside that page**: "click the ishowspeed live", "what page am I on", "play some video", "fullscreen". This uses Windows UI
  Automation (what screen readers use), not remote debugging, which Chromium blocks for a profile in use and which sites that
  distrust automation (Google sign-in) do not object to.
  - **Navigation:** "go back", "go forward", "reload", "go to the home page" press the browser's own buttons or load the
    address in the **current tab** ("open ..." opens a new tab). "go back" means the browser, not the previous song.
  - **Tabs:** "list my tabs", "close this tab", "close all other youtube tabs", "switch to the github tab". Closing keeps the tab
    you are on, asks first when 4 or more tabs would close, and refuses to close every tab (that is the window: say "close
    brave").
  - **Typo-tolerant clicks:** "clcik theh traver walles viedo" finds "Trevor Wallace Making ...", but Orbit never invents a
    match: if nothing on the page is close, it says so and lists what it can see.
  - **Limits:** it sees links, buttons and fields by their names, not images or video frames; it works on the Brave window in
    front; a click is confirmed by the page, address or the control's own label changing, so a click with no visible effect
    is reported as failed; clicking something risky-looking (buy, send, delete, pay...) asks you first, and Orbit refuses to press
    a risky-looking control you did not name.
- **`orbit`: Orbit's own automated window** (also used for "play X on youtube" + fullscreen, which Orbit verifies from the video
  element). It has a separate profile in `.browser-profile/` and never touches your everyday profiles. Sites that distrust
  automated browsers refuse sign-in there (Google shows "Couldn't sign you in"); GitHub's own login works. Switch the browser
  with `--browser chrome`.

"open my github" opens `github.com/lennydany3` (change with `ORBIT_GITHUB_USER`); "open github" opens the site. Only http(s)
links are opened. Web search uses DuckDuckGo.

## Privacy

- **Local by default.** Microphone capture, wake word, voice-activity detection, echo cancellation and hotkeys always run on this
  PC. With no API keys, nothing leaves the PC. With keys, the speech clip, the reply text and your requests go to Sarvam and
  Groq; Orbit prints which mode it is in when voice starts.
- **Nothing is recorded while idle.** Audio is only kept in memory for the turn being processed and is never saved. The only
  recordings are the test clips you make yourself with `record` / `record-wake` (git-ignored).
- **Logs** go to `logs/` (git-ignored). Every state change is logged with a timestamp; what you said is only written if
  `LOG_TRANSCRIPTS = True` in `voice/config.py`.
- **Nothing is downloaded behind your back.** `fetch_models` is the only voice code that uses the network, and only when you run
  it. `.env`, `settings.json`, `models/` and `.browser-profile/` are git-ignored.

## Project layout

```
src/orbit/
  agent/          rules, router, safety gate, friend mode, rescue, custom commands, the loop
  tools/
    api_layer/    Spotify (PKCE login, Credential Manager)
    os_layer/     apps, windows, monitors, volume, media keys, UI Automation + OCR clicks, processes
    browser_layer/ your Brave profile (UI Automation) and Orbit's own automated window (Playwright)
  llm/            Ollama (local) and Groq (cloud)
  voice/          microphone, wake word, VAD, echo cancellation, speech-to-text, voice, the voice loop
  ipc/            the WebSocket link to the desktop app
ui/               orbit-ui: the tray app and sphere (Rust + Rune)
tests/            1172 Python tests, plus shared fixtures the Rust side is tested against
docs/screenshots/ the pictures in this file
```

The screenshots are real renders of the window, made headless with
`cargo run -- --snap out.png --size 1280x800 --scene <name>` from `ui/` (scenes are listed in `ui/src/main.rs`).

## Known limits (honest list)

**Spotify**
- A phone or speaker only shows up as a Spotify device while its Spotify app is open or was used recently (Echo speakers can be
  slow to wake and sometimes need a second try). Spotify's own "transfer" call sometimes answers HTTP 500 for this PC; Orbit then
  restarts the current song on the target device and says so.
- Premium is needed for play and transfer. In development mode only accounts listed under User Management (max 5) can log in.
- Spotify must be running on the PC for it to appear as a device; a multi-step request opens it first. Its volume is separate
  from Windows volume.
- Playlist playback can only be confirmed as "something is playing", not that it came from that playlist. "Favourite music" is
  your liked songs (the 50 most recent). Spotify's API sometimes does not report what context is playing, so artist and album
  playback is verified from the current track.

**Browser**
- YouTube "playing" is verified from the video element, so a pre-roll ad counts as playing. Cookie-consent pages (EU) must be
  accepted once by hand.
- A browser click cannot be retried automatically (a second click could undo the first); if nothing visibly changes it reports
  failure.
- Pages that block automation (captchas, logins with 2FA) cannot be driven in Orbit's own window.

**Windows**
- Media keys go to whatever Windows treats as the current media session (often the last app that played). `pause` and `play` are
  safe to repeat; `next` and `previous` are verified by track change, which cannot be confirmed for live streams or one-track
  playlists.
- Opening an app finds it by Start-menu name; if two apps match ("visual studio") it asks instead of guessing. Store apps can
  show their window under a generic host, so window matching may miss some.
- Windows may refuse to bring a window to the front; the tool then reports failure rather than pretending.
- "Works on every app" is not achievable. Games and custom-drawn UIs may not be controllable.
- **Administrator windows (Task Manager, anything started "as administrator") cannot be controlled by a normal program.**
  Windows refuses to let Orbit close, minimize or move them, and Orbit says so up front. To control such windows, start Orbit
  from a terminal opened with "Run as administrator"; that gives Orbit (and anything it runs) full administrator rights, so only
  do it when you need it.
- **Closing a window is not quitting the app.** Many apps (ChatGPT, Spotify, Discord ...) keep running in the tray after the
  window closes. "close chatgpt" closes the window and tells you it is still running; **"quit chatgpt"** (also "exit", "kill",
  "force close", "close chatgpt completely") ends all of the app's processes. It always asks first, because quitting can lose
  unsaved work. It never quits Windows itself, system services, the Ollama model server, or the program Orbit runs inside.
- "Close" asks the window to close like its X button (nothing is force-killed). If the app shows a "save changes?" box the window
  stays open and Orbit reports that. A window whose title looks unsaved (starts with `*`) asks you first. Orbit never closes its
  own terminal.

**Language model and memory**
- The local 7B model makes mistakes on vague or multi-step requests; expect 10-20 s for those (it once read "quieter, around 20
  percent" as 67%). Simple commands skip the model, and the cloud model is faster and more accurate.
- Memory is short-term: the model sees your last 6 requests (capped at about 5,000 characters) so "close it" works. It is
  forgotten when you quit; type `clear` to forget sooner.
- Ctrl+C during a model call takes effect when that call returns (at most the 120 s timeout).

**Desktop app**
- The floating orb is a separate overlay window, so the screenshots above show the main window only.
- The `.exe`'s own icon in Explorer is not set (that needs a Windows resource file at build time); the window, taskbar and
  tray icons are.

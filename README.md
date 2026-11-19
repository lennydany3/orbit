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

- **Everything is local.** Microphone capture, wake word, voice-activity detection, hotkeys, speech-to-text (Whisper `small.en` on the CPU) and the spoken reply (Piper) all run on this PC. No audio ever leaves it, and the voice code makes no network calls.
- **Nothing is recorded while idle.** Audio is only kept in memory for the turn being processed. No audio is saved by default: the only recordings are the test clips you make yourself with `python -m orbit.voice.run record` / `record-wake` (git-ignored, local only).
- **Models:** `python -m orbit.voice.fetch_models ...` is the only thing that downloads voice models, and only when you run it.

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
- "Works on every app" is not achievable. Games, custom-drawn UIs and admin (elevated) windows may not be controllable.
- A 7B local model makes mistakes on vague or multi-step requests; expect 10-20 s for those. Simple commands skip the model.
- Memory is short-term: the model sees your last 6 requests (capped at about 5,000 characters) so "close it" works. It is forgotten when you quit; type `clear` to forget sooner. Rule commands like "pause" are remembered but never need memory.
- "Close" asks the window to close like its X button (nothing is force-killed). If the app shows a "save changes?" box the window stays open and Orbit reports that. A window whose title looks unsaved (starts with `*`) asks you first. Orbit never closes its own terminal.
- Ctrl+C during a model call takes effect when that call returns (at most the 120 s timeout).

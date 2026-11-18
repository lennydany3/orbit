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

The router prefers the API layer over OS and browser. Login uses PKCE, so there is no client secret anywhere; the refresh token lives in the Windows Credential Manager (entry `Orbit/spotify-refresh-token`), never in a file or the logs. Tools: play (song, artist, album, playlist, or "my favourite music" = your liked songs), transfer playback (to a device by name, optionally with a volume; "this" = the Spotify app on this PC), set a device's volume, play/pause/next/previous on whichever device is playing, list devices, now playing. Right after a Spotify request, "pause", "next" and "switch to echo" mean Spotify (so they reach a speaker too); otherwise "pause" uses the PC media keys. Spotify tools only appear when `SPOTIFY_CLIENT_ID` is set.

## Browser layer

Orbit opens its own visible **Brave** window (or Chrome: `python -m orbit --browser chrome`, or set `ORBIT_BROWSER=chrome`) with a separate profile in `.browser-profile/` (gitignored). It stays open between requests and closes when Orbit exits. Your everyday browser window, tabs and logins are never touched: Chromium does not allow remote control of a profile that is already open, so Orbit cannot reuse your normal Brave profile. Log in to a site once in Orbit's window and the login is kept for next time. "open my github" opens your profile (`github.com/lennydany3`; change it with the `ORBIT_GITHUB_USER` environment variable); "open github" opens the site. Words like "in brave" are ignored ("open github in brave" opens github in Orbit's browser). Only http(s) links are opened. Clicking a button whose text looks risky (buy, send, delete, pay...) asks you first. Web search uses DuckDuckGo.

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

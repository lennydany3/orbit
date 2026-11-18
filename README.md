# Orbit

A local assistant that operates your Windows PC from plain-language text requests
("open Spotify", "volume 30", "open my GitHub"). Runs on a local model (Qwen2.5 7B via Ollama), no paid API.

**Status: Step 2 of 5 (OS layer done).** Open apps, windows, media keys, volume. Browser (Step 3) and Spotify API (Step 4) come next.

## Setup (Windows 11, Python 3.12)

1. Install [Ollama](https://ollama.com) and start it.
2. `ollama pull qwen2.5:7b` (about 4.7 GB).
3. In this folder: `python -m venv .venv`
4. `.venv\Scripts\activate`
5. `pip install -e ".[dev]"`
6. `python -m orbit` (add `--no-llm` for rules only; `python -m orbit "volume 30"` runs one request; `--fake-tools` adds the Step 1 test tools).
7. `pytest` to run the tests.

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

## Known limits (honest list)

- Media keys go to whatever Windows treats as the current media session (often the last app that played). `pause`/`play` are safe to repeat; `next`/`prev` are verified by track change, which can't be confirmed for live streams or one-track playlists.
- Opening an app finds it by Start-menu name; if two apps match ("visual studio") it asks instead of guessing. Store apps can show their window under a generic host, so window matching may miss some.
- Windows may refuse to bring a window to the front; the tool then reports failure rather than pretending.
- `open <name>` is a rule for apps only. Websites ("open github") come with the browser layer in Step 3.
- The 7B model sometimes misreads amounts ("quieter, around 20 percent" became 67%). Plain commands use rules, not the model.
- "Works on every app" is not achievable. Games, custom-drawn UIs and admin (elevated) windows may not be controllable.
- A 7B local model makes mistakes on vague or multi-step requests; expect 10-20 s for those. Simple commands skip the model.
- Each request is independent: no memory between requests yet.
- Ctrl+C during a model call takes effect when that call returns (at most the 120 s timeout).

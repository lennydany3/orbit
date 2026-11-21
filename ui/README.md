# orbit-ui

Orbit's face: the particle sphere from the design, drawn on the GPU with [Rune](https://github.com/anomalyco/rune), and its
link to Orbit. It lives in the Windows tray. When Orbit is doing something, a small orb floats up over your work (bottom
right, above the taskbar) showing what it is doing and how loud the voice is; when Orbit needs a yes or no for
something risky, the question appears above the orb. A few seconds after things go quiet the orb fades away.

```
cargo run --release                        # the window (the big sphere; keys 1-9, 0, M try each state), with the tray and orb ready
cargo run --release -- --spawn             # start Orbit (microphone, wake word, model); the window stays in the tray
cargo run --release -- --spawn-demo        # start a scripted turn with a yes/no question: no microphone needed
cargo run --release -- --url "ws://127.0.0.1:8765/?token=..."    # show an Orbit that is already running
cargo run --release -- --spawn --window    # as --spawn, and open the window too
cargo test                                 # the logic, and the link against Orbit's real demo (needs the project's .venv)
```

- **Ctrl+Alt+O**, or a click on the tray icon, pins the orb on screen (again to let it go).
- **Settings** (the button top right of the window, or Settings... in the tray menu): see below.
- **Tray menu** (right-click): show or hide the orb, open the Orbit window, Settings, quit. Quitting also stops the Orbit it started.
- **The orb never takes focus**, and clicks pass straight through it. Only the Yes and No buttons of a question take clicks.
  You can answer a question by clicking, by voice, or with **Ctrl+Alt+Y** (yes) and **Ctrl+Alt+N** (no): those two chords are
  held only while a question is open, so they never take those keys from another program the rest of the time. Closing the
  window hides it; the app keeps running in the tray.
- **Windows 11 hides new tray icons** in the "hidden icons" flyout. To keep Orbit's icon in view, turn it on in Settings,
  Personalization, Taskbar, Other system tray icons.
- **Start with Windows:** `orbit-ui --autostart on` adds one value to your own `Run` key so that `orbit-ui --spawn` starts when
  you sign in (through a hidden PowerShell, so no console window stays open); `--autostart off` removes it and `--autostart
  status` shows it. Nothing sets it unless you ask. It points at this exact `orbit-ui.exe`, so run `on` again if you move or
  rebuild it somewhere else.
- **Cost at rest:** with the orb hidden, about 0% of one core and about 100 MB (the orb's window and GPU surface are made at
  startup so it appears the moment Orbit wakes: about 0.2 s, against about 1 s if made on first use). While the orb animates,
  about 10% of one core.

## Settings

The Settings page (a Settings button on the window, and Settings... in the tray menu) controls Orbit from one place. Every
change is saved the moment it is made, to `settings.json` in the project folder (`ORBIT_SETTINGS` moves it; it is git-ignored).
The window writes it and Orbit reads it; they share the rules in `tests/fixtures`, which both sides are tested against.

- **Hotkeys.** Click a chord, press the new keys (Esc cancels). The chord for showing the orb, and the yes and no answers, take
  effect at once; one Windows will not give Orbit (another program has it) is refused with a reason and nothing changes. The
  hold-to-talk key is picked from a list, and Orbit follows it within a second, with no restart. Reset puts all four back.
- **Commands.** Say a phrase and Orbit runs a list of steps in order: open an app, a website or a folder; press a key combination
  (`win+shift+s`); type text; wait a moment. Switch a command off without deleting it. A command that presses keys or types
  asks "yes or no?" first, once for the whole command, listing exactly what it will do (so it can be answered by voice, by
  clicking, or with the answer chords). Steps that open a folder only open it: nothing is ever run, and the language model
  never sees these commands and cannot press keys or type. Orbit picks up a saved command at once.
- **Sounds.** A soft tone when the orb fades in and another when it fades away (`assets/sounds/pop-up.wav` and `pop-down.wav`,
  compiled into the program). A switch and a volume slider (0 to 100; saved as `"sounds": {"enabled": true, "volume": 40}`);
  changing either plays the pop-up tone so you can judge it. Only a real show or hide makes a sound, not a change of what the
  orb says, and a show and hide within 350 ms make one sound, not two. Playing costs nothing at rest: nothing starts until the
  first sound, then one thread sleeps on a channel and hands the file to Windows' `PlaySound`, which returns at once. (No new
  crate; the one `unsafe` call is that function, in `src/sound.rs`.) Preview scene: `--scene settings-sounds-on`.
- **Customization.** Where the orb floats and how big it is: drag it on a small screen or press one of nine spots, and pick
  Small, Medium or Large. Saved as `"orb": {"x": 1.0, "y": 1.0, "size": "medium"}` (x and y are 0 to 1 across the usable screen,
  so the orb never leaves it; the entry is only written once changed, and Orbit's Python side ignores it). The real orb is
  shown while the page is open. The window is placed with a Rune `Rect` placement worked out in `src/layout.rs`, which keeps the
  orb still when a question's card opens (above it, or below it when the orb is in the top half). Preview scenes:
  `--scene settings-customization` and `settings-customization-moved`.
- A hand-edited file Orbit cannot fully read does not stop anything: the bad entry is left out, the page lists what, and the
  first save keeps the old file as `settings.json.bak`.

More groups (the model and its keys, the voice, the orb) slot in as another `Section` in `src/editor.rs`, another nav entry
and function in `src/settings_view.rs`.

An Orbit that is already running is started with `python -m orbit --voice --ipc`, which prints the link to pass with
`--url`. Or let the app start one itself with `--spawn` (or `--spawn-demo`). Close the window, or have it crash, and the Orbit
it started stops by itself (see below).

Keys 1-9, 0 and M put the sphere in a state by hand. While the link is open Orbit drives instead, and Esc answers a question
with "no".

## The logo

The dot-sphere mark (the refined master) is `assets/logo/orbit-dot-sphere.svg`. `src/logo.rs` puts it in the window's title bar,
the taskbar and Alt+Tab (the app tile, `orbit-icon-256.png`: a dashed ring and the satellite on white), in the tray (the small
`orbit-tray-32.rgba`) and beside the title in the window (the primary mark, 70 dots, drawn from Rune elements). Following the
SVG's own note, the small sizes are simplified rather than miniature particles. `python assets/logo/make_icons.py` makes the two
icon files and `src/logo_dots.rs` (every dot of the primary mark) from the SVG; run it again if the SVG changes. The `.exe`
file's own icon in Explorer is not set (that needs a Windows resource file at build time).

## How the window and Orbit fit together

- **The link** is a WebSocket on `127.0.0.1` with a password in the address. The window refuses to connect anywhere else,
  because the address carries that password. Orbit tells the window its state, the loudness of the voice being heard, what was
  said and any open question; the window can send exactly one thing back, the yes or no to that question. The messages are
  listed at the top of `src/orbit/ipc/protocol.py` in the Orbit project.
- **Starting Orbit.** `--spawn` makes a one-off password and a free port, starts Orbit with them, and connects. It keeps the
  child's standard input open and never writes to it: when the window closes or crashes the pipe ends, and Orbit stops cleanly
  by itself, so there is never an Orbit listening with nobody to show it. Set `ORBIT_PYTHON` to use another Python than the
  project's `.venv`, and `ORBIT_ROOT` if the project is elsewhere.
- **Pacing.** Orbit reports what happened ("wake" then, a tenth of a second later, "listening"); the window owns how long each
  animation takes, so a flash (wake 0.4 s, success 0.75 s, error 0.7 s) always plays out before the next state shows. The kill
  switch never waits. See `src/pacer.rs`.
- **Reconnecting.** The link retries by itself (0.5 s, doubling, at most 8 s), so starting the window before Orbit, or
  restarting Orbit, just works. Losing Orbit returns the sphere to idle and clears the caption and any open question.

## Layout

- `src/orbit/` the sphere. `state` (the 11 states), `params` (what each does, blended on change), `controller` (the shared
  state, level and timers), `sim` (one sphere's clock and blend, produces the numbers for a frame), `geometry` (the points),
  `gpu` + `sphere.wgsl` (drawing), `compositor` (paints a sphere in every Rune `effect` slot).
- `src/link/` the connection: `message` (what Orbit sends, as types), `endpoint` (which addresses are allowed), and the thread
  that connects, reads, writes the one answer, and reconnects.
- `src/pacer.rs` flash timing. `src/spawn.rs` starting Orbit. `src/app.rs` app state and rules. `src/view.rs` the window.
- `src/autostart.rs` the sign-in entry. `src/face.rs` the orb's window and its question; `src/presence.rs` when the orb is showing; `src/shell.rs` the orb's overlay
  handle and the tray icon; `src/clock.rs` the one clock every window shares (each Rune window has its own, which restarts
  when a window is recreated).
- `tests/live.rs` the window's link against Orbit's real demo over a real socket: a whole turn, a question answered from the
  window, a wrong password, retrying until Orbit is up, and Orbit stopping when the window goes away.

## Building

Rune is pinned to one revision in `Cargo.toml` (`dev` is never rewritten). Cargo does not inherit a dependency's `[patch]`
table, so it is repeated there at the same revision: update both together.

Headless pictures, without a window (debug build): `cargo run -- --snap out.png --size 1280x800 --scene live-confirm --at 1`.
Scenes: the 11 states, `live-speaking`, `live-confirm`, `live-retrying`, `live-refused`.

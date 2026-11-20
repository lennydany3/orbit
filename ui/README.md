# orbit-ui

Orbit's face: the particle sphere from the design, drawn on the GPU with [Rune](https://github.com/anomalyco/rune), and its
link to Orbit. It lives in the Windows tray. When Orbit is doing something, a small orb floats up over your work (bottom
right, above the taskbar) showing what it is doing, your voice level, and what was said; when Orbit needs a yes or no for
something risky, the question appears above the orb. A few seconds after things go quiet the orb fades away.

```
cargo run --release                        # the review window (every state, a demo, a simulated voice), with the tray and orb ready
cargo run --release -- --spawn             # start Orbit (microphone, wake word, model); the window stays in the tray
cargo run --release -- --spawn-demo        # start a scripted turn with a yes/no question: no microphone needed
cargo run --release -- --url "ws://127.0.0.1:8765/?token=..."    # show an Orbit that is already running
cargo run --release -- --spawn --window    # as --spawn, and open the review window too
cargo test                                 # the logic, and the link against Orbit's real demo (needs the project's .venv)
```

- **Ctrl+Alt+O**, or a click on the tray icon, pins the orb on screen (again to let it go).
- **Tray menu** (right-click): show or hide the orb, open the Orbit window, quit. Quitting also stops the Orbit it started.
- **The orb never takes focus**, and clicks pass straight through it. Only the Yes and No buttons of a question take clicks;
  you can also answer by voice. Closing the review window hides it; the app keeps running in the tray.
- **Windows 11 hides new tray icons** in the "hidden icons" flyout. To keep Orbit's icon in view, turn it on in Settings,
  Personalization, Taskbar, Other system tray icons.
- **Cost at rest:** with the orb hidden the app uses about 0.2% of one core and 19 MB; while the orb animates, about 10%.

An Orbit that is already running is started with `python -m orbit --voice --ipc`, which prints the link to paste in the
panel's **Backend** box (or pass with `--url`). The window can also start one itself: **Start Orbit** and **Start demo** in
the same section. Close the window, or have it crash, and the Orbit it started stops by itself (see below).

Keys 1-9, 0 and M put the sphere in a state by hand. While the link is open Orbit drives instead, and Esc answers a question
with "no".

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
- `src/pacer.rs` flash timing. `src/spawn.rs` starting Orbit. `src/app.rs` app state and rules. `src/view.rs` the review window.
- `src/face.rs` the orb's window and its question; `src/presence.rs` when the orb is showing; `src/shell.rs` the orb's overlay
  handle and the tray icon; `src/clock.rs` the one clock every window shares (each Rune window has its own, which restarts
  when a window is recreated).
- `tests/live.rs` the window's link against Orbit's real demo over a real socket: a whole turn, a question answered from the
  window, a wrong password, retrying until Orbit is up, and Orbit stopping when the window goes away.

## Building

Rune is pinned to one revision in `Cargo.toml` (`dev` is never rewritten). Cargo does not inherit a dependency's `[patch]`
table, so it is repeated there at the same revision: update both together.

Headless pictures, without a window (debug build): `cargo run -- --snap out.png --size 1280x800 --scene live-confirm --at 1`.
Scenes: the 11 states, `live-speaking`, `live-confirm`, `live-retrying`, `live-refused`.

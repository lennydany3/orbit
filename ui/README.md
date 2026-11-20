# orbit-ui

Orbit's face: the particle sphere from the design, drawn on the GPU with [Rune](https://github.com/anomalyco/rune).
Step 1 of the GUI: the sphere, its eleven states, and a design-review window. No backend link yet.

```
cargo run --release                       # the window (keys 1-9, 0, M pick a state)
cargo test                                # state, timing and demo logic (no GPU needed)
cargo run -- --snap out.png --size 1280x800 --scene thinking --at 2     # headless picture (debug build)
cargo run -- --script s.json --snap out.png --at 1.0                    # drive clicks and keys, then picture
```

Rune is pinned to one revision in `Cargo.toml` (`dev` is never rewritten). Cargo does not inherit a
dependency's `[patch]` table, so it is repeated there at the same revision: update both together.

## Layout

- `src/orbit/` the sphere. `state` (the 11 states), `params` (what each does, blended on change),
  `controller` (the shared state, level and timers), `sim` (one sphere's clock and blend, produces the
  numbers for a frame), `geometry` (the points), `gpu` + `sphere.wgsl` (drawing), `compositor` (paints a
  sphere in every Rune `effect` slot).
- `src/app.rs` app state: dev-panel controls, the scripted demo turn, the simulated voice.
- `src/view.rs` the window, copied from the design.

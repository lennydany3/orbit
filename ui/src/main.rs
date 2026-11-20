//! Orbit's face. A particle sphere that shows what Orbit is doing, drawn on the GPU with Rune.
//!
//! `cargo run --release` opens the design-review window: the sphere, a panel to put it in each state,
//! a scripted demo turn and a simulated voice. Headless, for checking the look without a window:
//! `cargo run --release -- --snap out.png --size 1280x800 --scene thinking` (see the scenes below).

mod app;
mod orbit;
mod view;

use rune::{AppSpec, Key, KeyEvent};

use app::App;
use orbit::{OrbState, SphereCompositor};

/// Called at the start of every frame with the app clock: timers, the demo and the level.
fn poll(s: &mut App, t: f64, tx: &mut rune::Tx) -> bool {
    let changed = s.advance(t);
    if let Some(due) = s.next_due() {
        tx.wake_at(due);
    }
    changed
}

/// Keys 1 to 9, 0 and M put the sphere in a state (the panel lists them).
fn on_key(s: &mut App, ev: &KeyEvent, tx: &mut rune::Tx) -> bool {
    if ev.mods.cmd || ev.mods.alt || ev.mods.ctrl || ev.mods.logo {
        return false;
    }
    let Key::Text(text) = &ev.key else { return false };
    match OrbState::from_key(text) {
        Some(state) => {
            s.pick(state, tx.clock());
            true
        }
        None => false,
    }
}

// Named states for headless snapshots: `--scene <name>`.
macro_rules! scenes {
    ($($name:literal => $state:expr),+ $(,)?) => {
        &[$(($name, |s: &mut App| s.orbit.set_state($state, 0.0))),+]
    };
}

fn main() {
    let spec = AppSpec::<App> {
        min_size: Some((900.0, 560.0)),
        poll: Some(poll),
        on_key: Some(on_key),
        scenes: scenes![
            "idle" => OrbState::Idle,
            "wake" => OrbState::Wake,
            "listening" => OrbState::Listening,
            "transcribing" => OrbState::Transcribing,
            "thinking" => OrbState::Thinking,
            "confirming" => OrbState::Confirming,
            "speaking" => OrbState::Speaking,
            "follow_up" => OrbState::FollowUp,
            "success" => OrbState::Success,
            "error" => OrbState::Error,
            "muted" => OrbState::Muted,
        ],
        ..AppSpec::new("Orbit", (1280.0, 800.0), App::default, view::view)
    };
    rune::run_with(spec, SphereCompositor::default());
}

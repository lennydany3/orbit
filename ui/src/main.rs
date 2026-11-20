//! Orbit's face. A particle sphere that shows what Orbit is doing, drawn on the GPU with Rune.
//!
//! `cargo run --release` opens the design-review window: the sphere, a panel to put it in each state, a scripted demo turn
//! and a simulated voice. To show the real Orbit, connect it (the panel's Backend section) or start with:
//!
//!   --spawn          start Orbit (microphone, wake word, model) and connect to it
//!   --spawn-demo     start a scripted turn with a yes/no question, no microphone needed
//!   --url ws://127.0.0.1:PORT/?token=...   connect to an Orbit that is already running (`python -m orbit --voice --ipc`)
//!
//! Headless, for checking the look without a window:
//! `cargo run -- --snap out.png --size 1280x800 --scene thinking` (see the scenes below).

use orbit_ui::app::App;
use orbit_ui::link::{Event, Status};
use orbit_ui::orbit::{OrbState, SphereCompositor};
use orbit_ui::spawn::Kind;
use orbit_ui::view;
use rune::{AppSpec, Key, KeyEvent, Waker};

/// Called once at start with a waker the link thread uses when Orbit sends something, and the command line to act on.
fn setup(s: &mut App, waker: Waker) {
    s.set_waker(waker);
    if rune::is_headless() {
        return;                                    // a snapshot starts nothing and connects to nothing
    }
    let mut args = rune::extra_args().iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--spawn" => s.spawn(Kind::Orbit, 0.0),
            "--spawn-demo" => s.spawn(Kind::Demo, 0.0),
            "--url" => match args.next() {
                Some(url) => {
                    s.live.url.replace_text(url.clone());
                    s.connect(url, 0.0);
                }
                None => eprintln!("orbit-ui: --url needs a link"),
            },
            other => eprintln!("orbit-ui: ignoring unknown option {other}"),
        }
    }
}

/// Called at the start of every frame with the app clock: the link, timers, the demo and the level.
fn poll(s: &mut App, t: f64, tx: &mut rune::Tx) -> bool {
    let changed = s.advance(t);
    if let Some(due) = s.next_due() {
        tx.wake_at(due);
    }
    changed
}

/// Keys 1 to 9, 0 and M put the sphere in a state (the panel lists them). Esc answers a question with no.
fn on_key(s: &mut App, ev: &KeyEvent, tx: &mut rune::Tx) -> bool {
    if ev.mods.cmd || ev.mods.alt || ev.mods.ctrl || ev.mods.logo {
        return false;
    }
    if ev.key == Key::Escape {
        return s.answer_confirm(false);
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

/// A snapshot of Orbit driving: connected, in `state`, with these messages already received.
fn live(s: &mut App, state: &str, messages: &[&str]) {
    s.on_event(Event::Status(Status::Open), 0.0);
    s.on_event(Event::Message(format!(r#"{{"type":"hello","version":1,"state":"{state}","level":0.0}}"#)), 0.0);
    for m in messages {
        s.on_event(Event::Message((*m).to_owned()), 0.0);
    }
}

fn main() {
    let spec = AppSpec::<App> {
        min_size: Some((900.0, 560.0)),
        families: ("Inter", "Cascadia Mono, Consolas, Inter"),       // the design's monospace, for addresses and messages
        setup: Some(setup),
        poll: Some(poll),
        on_key: Some(on_key),
        // Named states for headless snapshots: `--scene <name>`.
        scenes: &[
            ("idle", |s| s.orbit.set_state(OrbState::Idle, 0.0)),
            ("wake", |s| s.orbit.set_state(OrbState::Wake, 0.0)),
            ("listening", |s| s.orbit.set_state(OrbState::Listening, 0.0)),
            ("transcribing", |s| s.orbit.set_state(OrbState::Transcribing, 0.0)),
            ("thinking", |s| s.orbit.set_state(OrbState::Thinking, 0.0)),
            ("confirming", |s| s.orbit.set_state(OrbState::Confirming, 0.0)),
            ("speaking", |s| s.orbit.set_state(OrbState::Speaking, 0.0)),
            ("follow_up", |s| s.orbit.set_state(OrbState::FollowUp, 0.0)),
            ("success", |s| s.orbit.set_state(OrbState::Success, 0.0)),
            ("error", |s| s.orbit.set_state(OrbState::Error, 0.0)),
            ("muted", |s| s.orbit.set_state(OrbState::Muted, 0.0)),
            ("live-speaking", |s| {
                live(s, "speaking", &[
                    r#"{"type":"transcript","who":"you","text":"Open Spotify and play some lofi."}"#,
                    r#"{"type":"reply","text":"Opened Spotify and started lofi on the Echo Pop.","spoken":"x","kind":"action","emotion":"neutral"}"#,
                ])
            }),
            ("live-confirm", |s| {
                live(s, "confirming", &[
                    r#"{"type":"transcript","who":"you","text":"Close Notepad."}"#,
                    r#"{"type":"confirm","id":1,"question":"os_close_window(...) is a risky action. Allow?","action":"close window Notepad"}"#,
                ])
            }),
            ("live-retrying", |s| s.on_event(Event::Status(Status::Closed), 0.0)),
            ("live-refused", |s| s.connect("ws://example.com:8765/?token=x", 0.0)),
        ],
        ..AppSpec::new("Orbit", (1280.0, 800.0), App::default, view::view)
    };
    rune::run_with(spec, SphereCompositor::default());
}

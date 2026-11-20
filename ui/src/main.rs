//! Orbit's face. A particle sphere that shows what Orbit is doing, drawn on the GPU with Rune.
//!
//! It lives in the tray. When Orbit is doing something a small orb floats up over your work (bottom right, above the
//! taskbar, never taking focus, clicks passing through) and goes away when things are quiet. Ctrl+Alt+O, or a click on the
//! tray icon, pins it on screen. The tray menu opens the full window: the design-review panel, with every state, a scripted
//! demo, a simulated voice and the connection to Orbit.
//!
//!   (no options)     open the review window, with the tray and the orb ready
//!   --spawn          start Orbit (microphone, wake word, model) and show it; the window stays in the tray
//!   --spawn-demo     the same with a scripted turn and a yes/no question, no microphone needed
//!   --url ws://127.0.0.1:PORT/?token=...   show an Orbit that is already running (`python -m orbit --voice --ipc`)
//!   --window         open the review window as well as the tray
//!
//! Headless, for checking the look without a window:
//! `cargo run -- --snap out.png --size 1280x800 --scene thinking` (see the scenes below).

use std::time::Duration;

use orbit_ui::app::App;
use orbit_ui::clock;
use orbit_ui::face::{self, Face};
use orbit_ui::link::{Event, Status};
use orbit_ui::orbit::{OrbState, SphereCompositor};
use orbit_ui::shell::{self, Shell};
use orbit_ui::spawn::Kind;
use orbit_ui::view;
use rune::{Align, AppSpec, Key, KeyEvent, OverlayInput, OverlayOptions, OverlaySpec, ScreenEdge, Waker, WindowSpec};

fn debug() -> bool {
    std::env::var_os("ORBIT_UI_DEBUG").is_some()
}

/// Called once at start with a waker the link thread uses when Orbit sends something, and the command line to act on.
fn setup(s: &mut App, waker: Waker) {
    if debug() {
        eprintln!("[hub] setup ran at {:.2}s", clock::now(0.0));
    }
    s.set_waker(waker);
    if rune::is_headless() {
        return;                                    // a snapshot starts nothing and connects to nothing
    }
    let mut args = rune::extra_args().iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--spawn" => s.spawn(Kind::Orbit, 0.0),
            "--spawn-demo" => s.spawn(Kind::Demo, 0.0),
            "--window" => {}
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

/// Called at the start of every frame with the app clock: the link, timers, the demo, the level and the orb.
fn poll(s: &mut App, t: f64, tx: &mut rune::Tx) -> bool {
    let now = clock::now(t);
    let changed = s.advance(now);
    s.sync_shell(now);
    if debug() && changed {
        eprintln!("[hub] {now:6.2}s state={:?} pinned={} caption={:?} ask={}", s.orbit.state, s.presence.pinned(), s.caption().map(|c| c.1), s.live.confirm.is_some());
    }
    if let Some(due) = s.next_wake(now) {
        tx.wake_after(Duration::from_secs_f64((due - now).max(0.0)));
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
            s.pick(state, clock::now(tx.clock()));
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

fn spec() -> AppSpec<App> {
    AppSpec::<App> {
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
    }
}

/// The floating orb: bottom right, above the taskbar, over every app, never focused. Clicks pass through everywhere except
/// the buttons of a question. It gives its GPU memory back when it has been hidden a while.
fn orb_overlay() -> rune::ConfiguredOverlay<Face> {
    let mut spec = OverlaySpec::new("orbit-orb", (face::WIDTH, face::HEIGHT), face::view);
    spec.edge = ScreenEdge::Bottom;
    spec.margin = 14.0;
    spec.align = Align::End;
    spec.inset = 20.0;
    spec.options(OverlayOptions { input: OverlayInput::Regions, ..Default::default() })
        .compositor(SphereCompositor::default())
        .release_gpu_after(Duration::from_secs(30))
}

fn main() {
    clock::start();
    if rune::is_headless() {
        rune::run_with(spec(), SphereCompositor::default());
        return;
    }

    // With no instructions there is nothing else to look at, so the window opens; with --spawn and friends it stays in the tray.
    let args = rune::extra_args();
    let starts_orbit = args.iter().any(|a| matches!(a.as_str(), "--spawn" | "--spawn-demo" | "--url"));
    let open_window = args.iter().any(|a| a == "--window") || !starts_orbit;

    let mut app = rune::App::new(spec()).windowless().compositor(SphereCompositor::default());
    let hub = app.handle();
    let orb = app.overlay(orb_overlay(), Face::new(Some(hub.clone())));
    let tray = shell::make_tray(hub.clone());
    let hub_in_state = hub.clone();
    let app = app
        .global_shortcut("ctrl+alt+o", |s, tx| s.toggle_pin(clock::now(tx.clock())))
        .init(move || App::default().with_shell(Shell::new(orb, hub_in_state, tray)));
    if open_window {
        hub.open();
    }
    rune::run_windows(move |_displays| vec![rune::AnyWindowSpec::from(WindowSpec::from_app("hub", app))]);
}

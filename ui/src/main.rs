//! Orbit's face. A particle sphere that shows what Orbit is doing, drawn on the GPU with Rune.
//!
//! It lives in the tray. When Orbit is doing something a small orb floats up over your work (bottom right, above the
//! taskbar, never taking focus, clicks passing through) and goes away when things are quiet. Ctrl+Alt+O, or a click on the
//! tray icon, pins it on screen. The tray menu opens the window: the big sphere, what state it is in and what was last said.
//!
//!   (no options)     open the window, with the tray and the orb ready (nothing is connected: use one of the options below)
//!   --spawn          start Orbit (microphone, wake word, model) and show it; the window stays in the tray
//!   --spawn-demo     the same with a scripted turn and a yes/no question, no microphone needed
//!   --url ws://127.0.0.1:PORT/?token=...   show an Orbit that is already running (`python -m orbit --voice --ipc`)
//!   --window         open the window as well as the tray
//!   --autostart on|off|status   start Orbit (--spawn) when you sign in to Windows; never done unless you ask
//!
//! Headless, for checking the look without a window:
//! `cargo run -- --snap out.png --size 1280x800 --scene thinking` (see the scenes below).

use std::time::Duration;

use orbit_ui::app::{App, Page};
use orbit_ui::autostart;
use orbit_ui::clock;
use orbit_ui::editor::{Section, SettingsState};
use orbit_ui::face::{self, Face};
use orbit_ui::link::{Event, Status};
use orbit_ui::orbit::{OrbState, SphereCompositor};
use orbit_ui::settings;
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
    s.sync_shell(clock::now(0.0));                 // the orb's hotkey, from the settings, from the very start
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

/// While a hotkey is being recorded every key is for it. Otherwise: on the Settings page Esc goes back; on the big sphere keys
/// 1 to 9, 0 and M put it in a state, to try them, and Esc answers a question with no.
fn on_key(s: &mut App, ev: &KeyEvent, tx: &mut rune::Tx) -> bool {
    if s.record_key(ev, tx.key_base()) {
        s.sync_shell(clock::now(tx.clock()));
        return true;
    }
    if s.page == Page::Settings {
        if ev.key == Key::Escape && s.prefs.draft.is_none() {
            s.close_settings();
            return true;
        }
        return false;
    }
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

/// The Settings page showing the example settings file, saved nowhere.
fn settings_scene(s: &mut App, section: Section) {
    s.prefs = SettingsState::from_json(include_str!("../../tests/fixtures/settings_example.json"));
    s.open_settings(section);
}

fn spec() -> AppSpec<App> {
    AppSpec::<App> {
        min_size: Some((520.0, 420.0)),
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
            ("settings-hotkeys", |s| settings_scene(s, Section::Hotkeys)),
            ("settings-recording", |s| {
                settings_scene(s, Section::Hotkeys);
                s.prefs.begin_record(orbit_ui::editor::Field::Yes);
            }),
            ("settings-orb-taken", |s| {
                settings_scene(s, Section::Hotkeys);
                s.prefs.orb_error = Some("Ctrl + Alt + O is already in use by another program, so Orbit cannot have it.".into());
                s.prefs.fail("Ctrl + Alt + Y is already used for \"Answer yes\".");
            }),
            ("settings-commands", |s| settings_scene(s, Section::Commands)),
            ("settings-commands-empty", |s| {
                settings_scene(s, Section::Commands);
                s.prefs = SettingsState::default();
                s.open_settings(Section::Commands);
            }),
            ("settings-problems", |s| {
                settings_scene(s, Section::Commands);
                s.prefs = SettingsState::from_json(r#"{"commands":[{"name":"Broken","phrases":["x"],"steps":[{"kind":"run","program":"calc.exe"}]}]}"#);
                s.open_settings(Section::Commands);
            }),
            ("settings-editor", |s| {
                settings_scene(s, Section::Commands);
                s.prefs.edit_command(0);
            }),
            ("settings-editor-new", |s| {
                settings_scene(s, Section::Commands);
                s.prefs.new_command();
                s.prefs.add_step(orbit_ui::editor::StepKind::Keys);
                let _ = s.prefs.save_draft();
            }),
            ("settings-delete", |s| {
                settings_scene(s, Section::Commands);
                s.prefs.ask_delete(1);
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
    spec.options(OverlayOptions { input: OverlayInput::Regions, prewarm: true, ..Default::default() })
        .compositor(SphereCompositor::default())
}

/// `orbit-ui --autostart on|off|status`: handled before anything starts, and the program ends. The exit code, if it was asked.
fn autostart_command() -> Option<i32> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let at = args.iter().position(|a| a == "--autostart")?;
    let exe = std::env::current_exe().ok()?;
    let done = |result: Result<(), String>, message: &str| match result {
        Ok(()) => {
            println!("{message}");
            0
        }
        Err(why) => {
            eprintln!("orbit-ui: {why}");
            1
        }
    };
    Some(match args.get(at + 1).map(String::as_str) {
        Some("on") => done(
            autostart::enable(autostart::NAME, &exe, &["--spawn"]),
            &format!("Orbit will start when you sign in: {}
Turn it off with: orbit-ui --autostart off", exe.display()),
        ),
        Some("off") => done(autostart::disable(autostart::NAME), "Orbit will no longer start when you sign in."),
        Some("status") => {
            match autostart::current(autostart::NAME) {
                Some(command) => println!("on: {command}"),
                None => println!("off"),
            }
            0
        }
        _ => {
            eprintln!("usage: orbit-ui --autostart on|off|status");
            2
        }
    })
}

fn main() {
    if let Some(code) = autostart_command() {
        std::process::exit(code);
    }
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
    let prefs_file = settings::path();
    let app = app.init(move || {
        App::default().with_shell(Shell::new(orb, hub_in_state, tray)).with_settings(SettingsState::open(prefs_file.clone()))
    });
    if open_window {
        hub.open();
    }
    rune::run_windows(move |_displays| vec![rune::AnyWindowSpec::from(WindowSpec::from_app("hub", app))]);
}

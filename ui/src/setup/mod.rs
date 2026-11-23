//! Orbit's own installer (`orbit-setup.exe`, published as `Orbit-Setup-x.y.z.exe`): one borderless window in Orbit's look, the
//! particle sphere inside Orbit's dashed-ring mark, and every screen of the install drawn here.
//!
//!   Welcome -> (Options) -> (Keys, first install only) -> Installing -> Done | Failed
//!
//! What actually installs Orbit is the Inno Setup installer carried inside this file (see `install.rs`), run very silently.
//! Headless pictures of every screen: `cargo run --bin orbit-setup -- --snap out.png --size 760x480 --scene installing`.

pub mod install;
pub mod ring;
pub mod view;

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rune::{AppHandle, AppSpec, TextEdit, WindowOptions};

use crate::clock;
use crate::orbit::{HasOrbit, OrbState, Orbit, SphereCompositor};
use install::{Choices, Launch, Mode, Phase, Progress};

/// The window, in points.
pub const SIZE: (f32, f32) = (760.0, 480.0);
/// The version being installed (the release workflow writes it into Cargo.toml).
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone, Debug, PartialEq)]
pub enum Page {
    Welcome,
    Options,
    Keys,
    Installing,
    Done,
    Failed,
}

pub struct Setup {
    pub orbit: Orbit,
    pub page: Page,
    pub mode: Mode,
    pub passthrough: Vec<String>,
    pub dir: TextEdit,
    pub startup: bool,
    pub desktop: bool,
    pub key: TextEdit,
    /// Orbit is installed already: this is an update (the folder and options stay as they were).
    pub installed: Option<String>,
    /// A keys file exists, so the keys screen is skipped.
    pub has_keys: bool,
    pub progress: Arc<Mutex<Progress>>,
    /// The fraction the ring shows, eased toward the real one so it never jumps.
    pub shown: f32,
    /// What the worker reported last (to notice a change).
    pub phase: Phase,
    pub t: f64,
    pub started_at: f64,
    pub done_at: Option<f64>,
    pub handle: Option<AppHandle<Setup>>,
    /// The note under the buttons (e.g. why Orbit could not be started).
    pub note: Option<String>,
}

impl HasOrbit for Setup {
    fn orbit(&self) -> &Orbit {
        &self.orbit
    }
}

impl Default for Setup {
    fn default() -> Self {
        Setup {
            orbit: Orbit::new(),
            page: Page::Welcome,
            mode: Mode::Interactive,
            passthrough: Vec::new(),
            dir: TextEdit::new(install::default_dir().display().to_string()),
            startup: true,
            desktop: false,
            key: TextEdit::new(""),
            installed: None,
            has_keys: false,
            progress: Arc::new(Mutex::new(Progress::default())),
            shown: 0.0,
            phase: Phase::Unpacking,
            t: 0.0,
            started_at: 0.0,
            done_at: None,
            handle: None,
            note: None,
        }
    }
}

impl Setup {
    /// The real starting state: what is installed already, and how the program was started.
    pub fn for_this_pc(launch: Launch) -> Setup {
        let mut s = Setup { mode: launch.mode, passthrough: launch.passthrough, ..Setup::default() };
        if let Some(dir) = install::installed_dir() {
            s.dir = TextEdit::new(dir.display().to_string());
            s.installed = Some(install::installed_version().unwrap_or_default());
        }
        s.has_keys = install::keys_file().is_some_and(|f| f.exists());
        s
    }

    pub fn is_update(&self) -> bool {
        self.installed.is_some()
    }

    /// Shown as an update: Orbit is installed already, or Orbit itself started this (silently) to update.
    pub fn updating(&self) -> bool {
        self.is_update() || self.mode != Mode::Interactive
    }

    pub fn progress(&self) -> Progress {
        self.progress.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn install_dir(&self) -> PathBuf {
        PathBuf::from(self.dir.text.trim())
    }

    /// "Install" on the welcome or options screen: ask for the key first on a first install, otherwise start.
    pub fn next_from_welcome(&mut self) {
        if !self.is_update() && !self.has_keys && self.page != Page::Keys {
            self.page = Page::Keys;
        } else {
            self.start();
        }
    }

    /// Starts the install on a worker thread.
    pub fn start(&mut self) {
        if self.page == Page::Installing {
            return;
        }
        if self.install_dir().as_os_str().is_empty() {
            self.note = Some("Choose a folder to install Orbit in.".into());
            self.page = Page::Options;
            return;
        }
        self.note = None;
        self.page = Page::Installing;
        self.shown = 0.0;
        self.started_at = self.t;
        self.done_at = None;
        *self.progress.lock().unwrap_or_else(|e| e.into_inner()) = Progress::default();
        self.phase = Phase::Unpacking;
        self.orbit.set_state(OrbState::Thinking, self.t);
        // A silent run is an update started by Orbit itself: keep the folder and options chosen last time.
        let choices = (self.mode == Mode::Interactive).then(|| Choices {
            dir: self.install_dir(),
            startup: self.startup,
            desktop: self.desktop,
            groq_key: self.key.text.to_owned(),
        });
        let shared = Arc::clone(&self.progress);
        let passthrough = self.passthrough.clone();
        let handle = self.handle.clone();
        std::thread::Builder::new()
            .name("orbit-setup".into())
            .spawn(move || {
                let wake = move || {
                    if let Some(h) = &handle {
                        h.wake();
                    }
                };
                install::run(choices, &passthrough, &shared, &wake);
            })
            .expect("a thread for the install");
    }

    pub fn launch(&mut self) {
        match install::launch_orbit(&self.install_dir()) {
            Ok(()) => self.quit(),
            Err(e) => self.note = Some(format!("Orbit could not be started ({e}). Start it from the Start menu.")),
        }
    }

    pub fn quit(&self) {
        if let Some(h) = &self.handle {
            h.quit();
        }
    }

    pub fn minimize(&self) {
        if let Some(h) = &self.handle {
            h.minimize();
        }
    }

    /// Opens the kept log in Notepad.
    pub fn open_log(&self) {
        if let Some(log) = self.progress().log {
            let _ = std::process::Command::new("notepad.exe").arg(log).spawn();
        }
    }

    /// Can the window be closed now? Not while files are being copied: half an install is worse than either.
    pub fn can_close(&self) -> bool {
        self.page != Page::Installing
    }

    /// One frame: follow the worker, ease the ring, move the sphere. True when something on screen changed.
    pub fn advance(&mut self, now: f64) -> bool {
        let dt = (now - self.t).clamp(0.0, 0.25) as f32;
        self.t = now;
        self.orbit.tick(now);
        if self.page == Page::Installing {
            let p = self.progress();
            if p.phase != self.phase {
                self.phase = p.phase;
                match p.phase {
                    Phase::Done => {
                        self.page = Page::Done;
                        self.done_at = Some(now);
                        self.orbit.set_state(OrbState::Success, now);
                    }
                    Phase::Failed => {
                        self.page = Page::Failed;
                        self.orbit.set_state(OrbState::Error, now);
                    }
                    _ => {}
                }
            }
            // Unknown total (a developer run): creep toward 90% so the ring still moves.
            let target = if p.files_total > 0 { p.fraction } else { 0.9 * (1.0 - (-(now - self.started_at) as f32 / 20.0).exp()) };
            self.shown += (target - self.shown) * (dt * 5.0).min(1.0);
        } else if self.page == Page::Done {
            self.shown += (1.0 - self.shown) * (dt * 6.0).min(1.0);
            // An update closes itself: Orbit is started again by the installer.
            if self.mode != Mode::Interactive && self.done_at.is_some_and(|d| now - d > 1.6) {
                self.quit();
            }
        }
        true                                       // the satellite is always moving
    }
}

fn setup(s: &mut Setup, _waker: rune::Waker) {
    if s.mode != Mode::Interactive && !rune::is_headless() {
        s.start();                                 // an update starts at once
    }
}

fn poll(s: &mut Setup, t: f64, tx: &mut rune::Tx) -> bool {
    let changed = s.advance(clock::now(t));
    tx.wake_after(Duration::from_millis(33));
    changed
}

fn scene_progress(s: &mut Setup, phase: Phase, fraction: f32) {
    s.page = Page::Installing;
    s.phase = phase;
    s.shown = fraction;
    *s.progress.lock().unwrap() = Progress { phase, fraction, files_done: (1960.0 * fraction) as u64, files_total: 1960, error: None, log: None };
    s.orbit.set_state(OrbState::Thinking, 0.0);
}

pub fn spec() -> AppSpec<Setup> {
    AppSpec::<Setup> {
        min_size: Some(SIZE),
        setup: Some(setup),
        poll: Some(poll),
        scenes: &[
            ("welcome", |_| {}),
            ("update", |s| s.installed = Some("1.0.2".into())),
            ("options", |s| s.page = Page::Options),
            ("keys", |s| s.page = Page::Keys),
            ("installing", |s| scene_progress(s, Phase::Copying, 0.42)),
            ("updating", |s| {
                s.installed = Some("1.0.2".into());
                s.mode = Mode::Silent;
                scene_progress(s, Phase::Copying, 0.66);
            }),
            ("finishing", |s| scene_progress(s, Phase::Finishing, 1.0)),
            ("done", |s| {
                s.page = Page::Done;
                s.shown = 1.0;
            }),
            ("failed", |s| {
                s.page = Page::Failed;
                *s.progress.lock().unwrap() = Progress {
                    phase: Phase::Failed,
                    error: Some("a file is in use; close Orbit and try again".into()),
                    log: Some(PathBuf::from("orbit-setup.log")),
                    ..Progress::default()
                };
                s.orbit.set_state(OrbState::Error, 0.0);
            }),
        ],
        ..AppSpec::new("Orbit Setup", SIZE, Setup::default, view::view)
    }
}

/// `orbit-setup.exe`. Returns the process exit code.
pub fn main() -> i32 {
    clock::start();
    crate::logo::install();
    let launch = Launch::from_env();
    if rune::is_headless() {
        rune::run_with(spec(), SphereCompositor::default());
        return 0;
    }
    if launch.mode == Mode::VerySilent {
        // No window at all: install, and say how it went with the exit code (what Inno Setup's own codes would say).
        let shared = Arc::new(Mutex::new(Progress::default()));
        install::run(None, &launch.passthrough, &shared, &|| {});
        let p = shared.lock().unwrap_or_else(|e| e.into_inner()).clone();
        if let Some(why) = &p.error {
            eprintln!("orbit-setup: {why}");
        }
        return if p.phase == Phase::Done { 0 } else { 1 };
    }
    let options = WindowOptions { borderless: true, resizable: false, ..WindowOptions::default() };
    let app = rune::App::new(spec()).compositor(SphereCompositor::default()).window_options(options);
    let handle = app.handle();
    let app = app.init(move || {
        let mut s = Setup::for_this_pc(launch.clone());
        s.handle = Some(handle.clone());
        s
    });
    app.run();
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh() -> Setup {
        Setup { has_keys: false, installed: None, ..Setup::default() }
    }

    #[test]
    fn a_first_install_asks_for_the_key_and_an_update_does_not() {
        let mut s = fresh();
        s.page = Page::Welcome;
        s.next_from_welcome();
        assert_eq!(s.page, Page::Keys);
        let mut u = Setup { installed: Some("1.0.2".into()), ..fresh() };
        u.dir = TextEdit::new("");                 // with no folder it stops at the options instead of starting a real install
        u.next_from_welcome();
        assert_eq!(u.page, Page::Options);
        assert!(u.note.is_some());
    }

    #[test]
    fn the_ring_follows_the_worker_and_the_sphere_reacts() {
        let mut s = fresh();
        scene_progress(&mut s, Phase::Copying, 0.0);
        s.progress.lock().unwrap().fraction = 0.5;
        for i in 0..60 {
            s.advance(i as f64 * 0.033);
        }
        assert!((s.shown - 0.5).abs() < 0.02, "{}", s.shown);
        s.progress.lock().unwrap().phase = Phase::Done;
        s.advance(2.1);
        assert_eq!(s.page, Page::Done);
        assert_eq!(s.orbit.state, OrbState::Success);
        s.page = Page::Installing;
        s.progress.lock().unwrap().phase = Phase::Failed;
        s.advance(2.2);
        assert_eq!(s.page, Page::Failed);
    }

    #[test]
    fn the_window_cannot_be_closed_half_way() {
        let mut s = fresh();
        assert!(s.can_close());
        s.page = Page::Installing;
        assert!(!s.can_close());
    }
}

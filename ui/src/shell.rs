//! The window's connection to the desktop: the floating orb, and the tray icon. Everything here talks to Rune's handles, which
//! work from any thread; the decisions (when the orb shows, what it says) are made elsewhere and only carried out here.

use std::time::{Duration, Instant};

use rune::{AppHandle, OverlayHandle, Shortcut, ShortcutError, ShortcutId, Tray, TrayEvent, TrayIcon, TrayItem, TraySpec};

use crate::app::App;
use crate::face::{Face, FaceKey};
use crate::settings::Sounds;
use crate::sound::{Chime, Cue, Player};

/// How the orb leaves: a quick fade (Windows and macOS; elsewhere it just goes).
const FADE_OUT: Duration = Duration::from_millis(260);
const FADE_IN: Duration = Duration::from_millis(160);

#[derive(Default)]
pub struct Shell {
    orb: Option<OverlayHandle<Face>>,
    pub hub: Option<AppHandle<App>>,
    /// Kept alive: dropping it takes the icon out of the tray.
    tray: Option<Tray>,
    shown: bool,
    last_key: Option<FaceKey>,
    last_size: Option<(f32, f32)>,
    /// The answer chords, held only while a question is open.
    answer_keys: Option<(ShortcutId, ShortcutId)>,
    /// The chord that shows or hides the orb, as registered with Windows, and the one that was refused (so it is not retried
    /// on every frame).
    orb_chord: Option<(String, ShortcutId)>,
    orb_refused: Option<(String, String)>,
    /// When a show or hide gets a sound, and how it is played. (Quiet unless made with `new`: only the real window speaks.)
    chime: Chime,
    pub player: Player,
}

impl Shell {
    pub fn new(orb: OverlayHandle<Face>, hub: AppHandle<App>, tray: Option<Tray>) -> Shell {
        Shell { orb: Some(orb), hub: Some(hub), tray, shown: false, last_key: None, last_size: None, answer_keys: None, orb_chord: None, orb_refused: None, chime: Chime::default(), player: Player::speaking() }
    }

    pub fn has_tray(&self) -> bool {
        self.tray.is_some()
    }

    /// Plays the pop-up tone at the volume in `sounds`, for the Settings page to let you hear a change (nothing if it is off).
    pub fn preview(&mut self, sounds: Sounds) {
        if sounds.audible() {
            self.player.play(Cue::Up, sounds.volume);
        }
    }

    /// Brings the orb up to date: new content if it changed, a new size if the question came or went, and a fade in or out
    /// (with its sound: only a real show or hide has one, never a change of what the orb says).
    pub fn apply(&mut self, mut face: Face, visible: bool, sounds: Sounds) {
        self.hold_answer_keys(face.wants_answer_keys(), &face.yes, &face.no);
        face.keys = self.answer_keys.is_some();
        let Some(orb) = &self.orb else { return };
        let (key, size) = (face.key(), face.size());
        if self.last_size != Some(size) {
            orb.resize(size);
            self.last_size = Some(size);
        }
        if self.last_key.as_ref() != Some(&key) {
            self.last_key = Some(key);
            orb.update(move |state, _| *state = face);
        }
        if visible != self.shown {
            self.shown = visible;
            if visible {
                orb.fade_in(FADE_IN);
            } else {
                orb.fade_out(FADE_OUT);
            }
            if let Some(cue) = self.chime.on_change(visible, sounds, Instant::now()) {
                self.player.play(cue, sounds.volume);
            }
        }
    }
}

impl Shell {
    /// Ctrl+Alt+Y and Ctrl+Alt+N answer the question, from anywhere, without the orb ever taking focus. They are held only
    /// while a question is open, so they never take those keys from another app the rest of the time. If another program
    /// already has a chord, that one simply is not offered.
    fn hold_answer_keys(&mut self, wanted: bool, yes_chord: &str, no_chord: &str) {
        let Some(hub) = self.hub.clone() else { return };
        match (wanted, self.answer_keys) {
            (true, None) => {
                let answer = |yes: bool| {
                    move |app: &mut App, tx: &mut rune::Tx| {
                        app.answer_confirm(yes);
                        app.sync_shell(crate::clock::now(tx.clock()));
                    }
                };
                let yes = yes_chord.parse().ok().and_then(|chord| hub.register_shortcut(chord, answer(true)).ok());
                let no = no_chord.parse().ok().and_then(|chord| hub.register_shortcut(chord, answer(false)).ok());
                match (yes, no) {
                    (Some(y), Some(n)) => self.answer_keys = Some((y, n)),
                    (y, n) => {
                        for id in [y, n].into_iter().flatten() {
                            let _ = hub.unregister_shortcut(id);
                        }
                    }
                }
            }
            (false, Some((yes, no))) => {
                let _ = hub.unregister_shortcut(yes);
                let _ = hub.unregister_shortcut(no);
                self.answer_keys = None;
            }
            _ => {}
        }
    }
}

impl Shell {
    /// Keeps the chord that shows or hides the orb registered: `wanted` is the one to have (None = none, for a moment). Changing
    /// it registers the new one before letting the old one go, so a refused chord leaves the working one in place. An error
    /// says why a chord was refused; it is reported once, not on every frame.
    pub fn sync_orb_chord(&mut self, wanted: Option<&str>) -> Result<(), String> {
        let Some(hub) = self.hub.clone() else { return Ok(()) };
        let current = self.orb_chord.as_ref().map(|(c, _)| c.as_str());
        if current == wanted {
            return Ok(());
        }
        let Some(wanted) = wanted else {
            if let Some((_, id)) = self.orb_chord.take() {
                let _ = hub.unregister_shortcut(id);
            }
            return Ok(());
        };
        if let Some((chord, why)) = &self.orb_refused {
            if chord == wanted {
                return Err(why.clone());
            }
        }
        let registered = wanted.parse::<Shortcut>().map_err(|e| (refusal(wanted, &e), e)).and_then(|chord| {
            hub.register_shortcut(chord, |app: &mut App, tx: &mut rune::Tx| app.toggle_pin(crate::clock::now(tx.clock()))).map_err(|e| (refusal(wanted, &e), e))
        });
        match registered {
            // The desktop is not up yet (the very first frame): not a refusal, so it is simply tried again next frame.
            Err((_, ShortcutError::NotRunning)) => Ok(()),
            Ok(id) => {
                if let Some((_, old)) = self.orb_chord.replace((wanted.to_owned(), id)) {
                    let _ = hub.unregister_shortcut(old);
                }
                self.orb_refused = None;
                Ok(())
            }
            Err((why, _)) => {
                self.orb_refused = Some((wanted.to_owned(), why.clone()));
                Err(why)
            }
        }
    }

    /// Can Windows give Orbit this chord right now? Takes it and lets it go again. (With no desktop to ask, yes.)
    pub fn probe(&self, chord: &str) -> Result<(), String> {
        let Some(hub) = &self.hub else { return Ok(()) };
        let shortcut = chord.parse::<Shortcut>().map_err(|e| refusal(chord, &e))?;
        let id = hub.register_shortcut(shortcut, |_: &mut App, _: &mut rune::Tx| {}).map_err(|e| refusal(chord, &e))?;
        let _ = hub.unregister_shortcut(id);
        Ok(())
    }
}

/// Why Windows would not hand over a chord, in words.
fn refusal(chord: &str, why: &ShortcutError) -> String {
    let name = crate::settings::pretty(chord);
    match why {
        ShortcutError::Conflict => format!("{name} is already in use by another program, so Orbit cannot have it."),
        other => format!("{name} could not be used: {other}."),
    }
}

/// The tray icon: Orbit's small mark, a ring and a dot on a white tile (see `logo`).
pub fn tray_icon() -> TrayIcon {
    TrayIcon::rgba(crate::logo::TRAY_SIZE, crate::logo::TRAY_SIZE, crate::logo::tray_rgba())
}

/// The tray icon and its menu. Every action goes through the hub, on the app's own thread; the tray's thread only forwards.
pub fn make_tray(hub: AppHandle<App>) -> Option<Tray> {
    let spec = TraySpec::new("orbit.face", "Orbit", tray_icon()).tooltip("Orbit").menu(vec![
        TrayItem::action("orb", "Show or hide the orb"),
        TrayItem::action("window", "Open the Orbit window"),
        TrayItem::action("settings", "Settings\u{2026}"),
        TrayItem::Separator,
        TrayItem::action("quit", "Quit Orbit"),
    ]);
    let tray = Tray::new(spec, move |event| match event {
        TrayEvent::Activate { .. } => hub.update(|app, tx| app.toggle_pin(crate::clock::now(tx.clock()))),
        TrayEvent::Menu(id) => match id.as_str() {
            "orb" => hub.update(|app, tx| app.toggle_pin(crate::clock::now(tx.clock()))),
            "window" => hub.open(),
            "settings" => {
                hub.update(|app, _| app.open_settings(crate::editor::Section::Hotkeys));
                hub.open();
            }
            "quit" => hub.quit(),
            _ => {}
        },
        _ => {}
    });
    match tray {
        Ok(tray) => Some(tray),
        Err(why) => {
            eprintln!("orbit-ui: no tray icon ({why})");
            None
        }
    }
}

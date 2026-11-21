//! The app's state and the rules that move it: the shared orbit, the dev panel's controls, the scripted demo turn, the
//! simulated voice, and the live link to Orbit. No drawing and no windowing, so all of it is tested.
//!
//! Two things can drive the sphere. By hand (the panel's buttons, the demo, the simulated voice), or Orbit itself over the
//! link. While the link is open Orbit drives: its states are paced (see `pacer`), its level replaces the simulated one, and the
//! sphere's own hand-over timers are off.

use std::path::Path;
use std::sync::Arc;

use rune::{KeyEvent, PointerButton, TextEdit, Waker};

use crate::datetime::Stamp;
use crate::devices::{self, Devices, Scan};
use crate::editor::{self, Field, Recorded, Section, SettingsState, TalkRecorded};
use crate::face::Face;
use crate::layout;
use crate::link::message::{self, Incoming};
use crate::link::{Event, Link, Status};
use crate::music::MusicState;
use crate::notes::NotesState;
use crate::orbit::{HasOrbit, OrbState, Orbit};
use crate::pacer::Pacer;
use crate::presence::Presence;
use crate::reminders::RemindersState;
use crate::settings;
use crate::shell::Shell;
use crate::spawn::{Kind, Spawned};
use crate::userdata;
use crate::watcher::{self, Watcher};

pub struct DemoStep {
    pub state: OrbState,
    pub ms: f64,
    /// The mic or voice level is simulated during this step.
    pub voice: bool,
    pub who: &'static str,
    pub cap: &'static str,
    /// The chip lit in the panel while the step runs; empty for the unlabelled pause at the end.
    pub chip: &'static str,
}

const fn step(state: OrbState, ms: f64, voice: bool, who: &'static str, cap: &'static str, chip: &'static str) -> DemoStep {
    DemoStep { state, ms, voice, who, cap, chip }
}

/// One full turn, looping: wake word, speech, transcription, thinking, a click, the reply, follow-up.
pub const DEMO: [DemoStep; 9] = [
    step(OrbState::Idle, 1500.0, false, "", "", "Idle"),
    step(OrbState::Wake, 420.0, false, "You", "Hey Orbit", "Wake"),
    step(OrbState::Listening, 2600.0, true, "You", "click the first video", "Listening"),
    step(OrbState::Transcribing, 1000.0, false, "You", "Click the first video.", "Transcribing"),
    step(OrbState::Thinking, 1800.0, false, "Orbit", "Finding the first video on the page", "Thinking"),
    step(OrbState::Success, 800.0, false, "Orbit", "Clicked the first video", "Click"),
    step(OrbState::Speaking, 2300.0, true, "Orbit", "Playing the first video.", "Speaking"),
    step(OrbState::FollowUp, 2000.0, false, "Orbit", "Listening for a follow-up", "Follow-up"),
    step(OrbState::Idle, 2000.0, false, "", "", ""),
];

/// A speech-like level between 0 and 1: syllables, phrases, pauses and a little jitter.
pub fn voice_level(t: f64) -> f32 {
    let s = 0.5 + 0.5 * (t * 15.0 + (t * 2.7).sin() * 3.0).sin();
    let w = 0.5 + 0.5 * (t * 4.1 + (t * 1.3).sin() * 2.0).sin();
    let gate = if (t * 1.3 + (t * 0.7).sin()).sin() > -0.55 { 1.0 } else { 0.15 };
    // A cheap deterministic hash of the time stands in for random jitter.
    let jitter = ((t * 91.7).sin() * 43758.5453).fract().abs() - 0.5;
    ((0.1 + 0.85 * s.powf(1.6) * (0.35 + 0.65 * w)) * gate + jitter * 0.06).clamp(0.0, 1.0) as f32
}

#[derive(Clone, Copy, Debug)]
pub struct DemoRun {
    pub step: usize,
    due: f64,
}

/// How long what was said stays under the sphere once nothing is being said.
const CAPTION_SECONDS: f64 = 6.0;
/// The longest line shown under the sphere; the full text goes to the console, the face shows a glance.
const CAPTION_CHARS: usize = 220;

/// What was said, shown under the sphere.
#[derive(Clone, Debug, PartialEq)]
pub struct Caption {
    pub who: String,
    pub text: String,
    shown_at: f64,
}

/// A risky action Orbit is asking about.
#[derive(Clone, Debug, PartialEq)]
pub struct Ask {
    pub id: i64,
    /// "close window Notepad".
    pub action: String,
    pub question: String,
    /// The answer given from here, while waiting for Orbit to confirm it took it.
    pub sent: Option<bool>,
}

/// A reminder, timer or alarm that is ringing: it rings until it is stopped (by voice, the talk key, or the Stop button).
#[derive(Clone, Debug, PartialEq)]
pub struct Ring {
    pub id: String,
    pub title: String,
    /// "reminder", "timer" or "alarm".
    pub kind: String,
    /// What Orbit said it is: "Your timer is up."
    pub text: String,
    /// Stop was pressed here, and Orbit has not yet said it stopped.
    pub sent: bool,
}

impl Ring {
    /// "Timer", "Alarm" or "Reminder": what to call it above its text.
    pub fn heading(&self) -> &'static str {
        match self.kind.as_str() {
            "timer" => "Timer",
            "alarm" => "Alarm",
            _ => "Reminder",
        }
    }
}

fn clip(text: &str, chars: usize) -> String {
    let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if text.chars().count() <= chars {
        return text;
    }
    let mut cut: String = text.chars().take(chars.saturating_sub(1)).collect();
    cut.truncate(cut.trim_end().len());
    cut.push('\u{2026}');
    cut
}

fn quiet_waker() -> Waker {
    Arc::new(|| {})
}

/// Everything about the link to Orbit.
pub struct Live {
    link: Option<Link>,
    waker: Waker,
    pub status: Status,
    /// The address box.
    pub url: TextEdit,
    /// The last thing Orbit sent, as text (the design shows the same under the address).
    pub last_message: String,
    /// What went wrong, if anything: a bad address, Orbit refusing, Orbit stopping.
    pub notice: Option<String>,
    pacer: Pacer,
    level: f32,
    pub caption: Option<Caption>,
    pub confirm: Option<Ask>,
    /// The reminder, timer or alarm that is ringing now.
    pub ring: Option<Ring>,
    /// How the last chat reply felt (`proud`, `caring` ...), for the face to use.
    pub feeling: String,
    child: Option<Spawned>,
}

impl Default for Live {
    fn default() -> Self {
        Live {
            link: None,
            waker: quiet_waker(),
            status: Status::Off,
            url: TextEdit::new("ws://127.0.0.1:8765/?token="),
            last_message: String::new(),
            notice: None,
            pacer: Pacer::new(),
            level: 0.0,
            caption: None,
            confirm: None,
            ring: None,
            feeling: String::new(),
            child: None,
        }
    }
}

/// What the window shows: one page at a time, chosen in the sidebar.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Page {
    /// The big sphere.
    #[default]
    Home,
    /// Your own commands: a phrase that runs a list of steps.
    Commands,
    Reminders,
    Notes,
    Music,
    Settings,
}

impl Page {
    pub fn label(self) -> &'static str {
        match self {
            Page::Home => "Home",
            Page::Commands => "Commands",
            Page::Reminders => "Reminders",
            Page::Notes => "Notes",
            Page::Music => "Music",
            Page::Settings => "Settings",
        }
    }
}

pub struct App {
    pub orbit: Orbit,
    /// The slider's own value, used when nothing is simulating the level.
    pub manual: f32,
    pub voice: bool,
    pub demo: Option<DemoRun>,
    /// The level the panel shows (updated about 14 times a second, not every frame).
    pub shown_level: f32,
    last_push: f64,
    pub live: Live,
    /// When the floating orb is on screen.
    pub presence: Presence,
    /// The orb's window and the tray icon (empty until the app has made them).
    pub shell: Shell,
    pub page: Page,
    /// The Settings page and the Commands page: the hotkeys, the audio and voice choices, and the user's own commands.
    pub prefs: SettingsState,
    /// Reminders, notes and tasks, and saved playlists: each its own file next to the settings.
    pub reminders: RemindersState,
    pub notes: NotesState,
    pub music: MusicState,
    /// The microphones and speakers the Audio card offers, and where the asking stands.
    pub devices: Devices,
    pub scan: Scan,
    /// Notices when the other side (Orbit) changes one of those files.
    watcher: Option<Watcher>,
    /// What Orbit last said about a reminder, to show a moment.
    pub reminder_said: Option<String>,
}

impl Default for App {
    fn default() -> Self {
        App {
            orbit: Orbit::new(),
            manual: 0.35,
            voice: false,
            demo: None,
            shown_level: 0.35,
            last_push: -1.0,
            live: Live::default(),
            presence: Presence::new(),
            shell: Shell::default(),
            page: Page::Home,
            prefs: SettingsState::default(),
            reminders: RemindersState::default(),
            notes: NotesState::default(),
            music: MusicState::default(),
            devices: Devices::default(),
            scan: Scan::Idle,
            watcher: None,
            reminder_said: None,
        }
    }
}

impl HasOrbit for App {
    fn orbit(&self) -> &Orbit {
        &self.orbit
    }
}

impl App {
    /// The app with the orb's window and the tray it will talk to.
    pub fn with_shell(mut self, shell: Shell) -> App {
        self.shell = shell;
        self
    }

    /// The app with its settings read from (and saved to) a file.
    pub fn with_settings(mut self, prefs: SettingsState) -> App {
        self.prefs = prefs;
        self
    }

    /// The app with reminders, notes and playlists read from (and saved to) the files that sit next to the settings file.
    pub fn with_data(mut self, settings_file: &Path) -> App {
        self.reminders = RemindersState::open(userdata::beside(settings_file, userdata::REMINDERS_FILE));
        self.notes = NotesState::open(userdata::beside(settings_file, userdata::NOTES_FILE));
        self.music = MusicState::open(userdata::beside(settings_file, userdata::MUSIC_FILE));
        self
    }

    /// Starts what runs beside the window: a look at the data files for changes Orbit makes, and the first look at the audio devices.
    pub fn start_background(&mut self) {
        let files: Vec<(u32, std::path::PathBuf)> = [(watcher::REMINDERS, &self.reminders.path), (watcher::NOTES, &self.notes.path), (watcher::MUSIC, &self.music.path)]
            .into_iter()
            .filter_map(|(bit, path)| path.clone().map(|p| (bit, p)))
            .collect();
        self.watcher = Some(Watcher::start(files, self.live.waker.clone(), watcher::EVERY));
        self.scan_devices();
    }

    /// Uses this watcher (a test's, whose changes it marks itself) instead of starting one.
    pub fn with_watcher(mut self, watcher: Watcher) -> App {
        self.watcher = Some(watcher);
        self
    }

    // ---- pages -------------------------------------------------------------------------------------------------------------

    /// Goes to a page (saving what was being typed on the one it leaves).
    pub fn go(&mut self, page: Page) {
        if self.page == Page::Notes && page != Page::Notes {
            self.notes.flush();
        }
        if self.page == Page::Settings || self.page == Page::Commands {
            self.prefs.cancel_record();
            if page != Page::Commands {
                self.prefs.cancel_draft();
            }
        }
        self.page = page;
        match page {
            Page::Reminders => {
                self.reminders.mark_seen(&Stamp::now());
            }
            Page::Settings if self.devices.inputs.is_empty() && self.devices.outputs.is_empty() => self.scan_devices(),
            _ => {}
        }
    }

    pub fn open_settings(&mut self, section: Section) {
        self.go(Page::Settings);
        self.prefs.select(section);
    }

    /// Back to the sphere.
    pub fn close_settings(&mut self) {
        self.go(Page::Home);
    }

    /// How many reminders have gone off that you have not looked at yet: the number on the bell.
    pub fn unread(&self) -> usize {
        self.reminders.data.unread()
    }

    // ---- the audio devices ---------------------------------------------------------------------------------------------------

    /// Asks Orbit's Python for the microphones and speakers, in the background (the lists fill in when it answers).
    pub fn scan_devices(&mut self) {
        if matches!(self.scan, Scan::Scanning(_)) || rune::is_headless() {
            return;
        }
        self.scan = Scan::Scanning(devices::scan_in_background(self.live.waker.clone()));
    }

    pub fn scanning(&self) -> bool {
        matches!(self.scan, Scan::Scanning(_))
    }

    /// What came back from the background: changed files read again, a finished device scan taken. True if anything changed.
    fn drain_background(&mut self) -> bool {
        let mut changed = false;
        if let Some(bits) = self.watcher.as_ref().map(Watcher::take).filter(|b| *b != 0) {
            changed |= bits & watcher::REMINDERS != 0 && self.reminders.reload_if_changed();
            changed |= bits & watcher::NOTES != 0 && self.notes.reload_if_changed();
            changed |= bits & watcher::MUSIC != 0 && self.music.reload_if_changed();
        }
        if let Scan::Scanning(rx) = &self.scan {
            match rx.try_recv() {
                Ok(Ok(found)) => {
                    self.devices = found;
                    self.scan = Scan::Idle;
                    changed = true;
                }
                Ok(Err(why)) => {
                    self.scan = Scan::Failed(why);
                    changed = true;
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => {}
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    self.scan = Scan::Failed("the device scan stopped".into());
                    changed = true;
                }
            }
        }
        changed
    }

    // ---- music ---------------------------------------------------------------------------------------------------------------

    /// The Play button: asks Orbit to play one of the saved playlists. False (with the reason on the page) if it cannot.
    pub fn play_playlist(&mut self, id: &str) -> bool {
        let Some(title) = self.music.get(id).map(|p| p.title()) else { return false };
        let sent = self.live_open() && self.live.link.as_ref().is_some_and(|link| link.send(message::play_request(id)));
        if sent {
            self.music.good(format!("Asked Orbit to play \u{201c}{title}\u{201d}."));
        } else {
            self.music.bad("Orbit is not running, so it cannot play that. Start it from the tray, or run orbit-ui --spawn.");
        }
        sent
    }

    /// Opens a saved playlist in the Spotify app (or the browser, if the app is not there).
    pub fn open_in_spotify(&mut self, id: &str) -> bool {
        let Some(uri) = self.music.get(id).map(|p| p.link().uri()) else { return false };
        match crate::openers::open_uri(&uri) {
            Ok(()) => true,
            Err(why) => {
                self.music.bad(why);
                false
            }
        }
    }

    // ---- the settings page ------------------------------------------------------------------------------------------

    /// While the hold-to-talk key is being recorded a mouse button pressed on the page is it. True if this press was used.
    pub fn record_button(&mut self, button: PointerButton) -> bool {
        if !self.prefs.recording_talk {
            return false;
        }
        match editor::talk_from_button(button) {
            Some(name) => {
                self.prefs.set_talk(name);
            }
            None => self.prefs.fail("Orbit cannot use that mouse button. Use mouse 1 to 5, or a key."),
        }
        true
    }

    /// While a hotkey is being recorded every key is for it. True if this key was used.
    pub fn record_key(&mut self, ev: &KeyEvent, base: Option<char>) -> bool {
        if self.prefs.recording_talk {
            match editor::talk_from_event(ev) {
                TalkRecorded::Ignore => {}
                TalkRecorded::Cancel => self.prefs.cancel_record(),
                TalkRecorded::Refused(why) => self.prefs.fail(why),
                TalkRecorded::Key(name) => {
                    self.prefs.set_talk(&name);
                }
            }
            return true;
        }
        let Some(field) = self.prefs.recording else { return false };
        match editor::chord_from_event(ev, base) {
            Recorded::Ignore => {}
            Recorded::Cancel => self.prefs.cancel_record(),
            Recorded::Refused(why) => self.prefs.fail(why),
            Recorded::Chord(chord) => {
                // A chord that does not work (taken, or the same as another) leaves recording on, to try another.
                if self.change_hotkey(field, &chord) {
                    self.prefs.cancel_record();
                }
            }
        }
        true
    }

    /// Makes `text` the hotkey for `field`, if it is a good one and Windows will give it to Orbit.
    pub fn change_hotkey(&mut self, field: Field, text: &str) -> bool {
        let chord = match self.prefs.check_hotkey(field, text) {
            Ok(chord) => chord,
            Err(why) => {
                self.prefs.fail(why);
                return false;
            }
        };
        if chord != self.prefs.hotkey(field) {
            if let Err(why) = self.shell.probe(&chord) {
                self.prefs.fail(why);
                return false;
            }
        }
        self.prefs.apply_hotkey(field, chord)
    }

    /// All the hotkeys back to the defaults (the orb's one only if Windows will give it back).
    pub fn reset_hotkeys(&mut self) {
        let orb = settings::DEFAULT_ORB;
        if self.prefs.hotkey(Field::Orb) != orb {
            if let Err(why) = self.shell.probe(orb) {
                return self.prefs.fail(why);
            }
        }
        self.prefs.reset_hotkeys();
    }

    // ---- the orb's sounds -------------------------------------------------------------------------------------------

    /// The switch on the Settings page. Switching on lets you hear it.
    pub fn set_sounds_enabled(&mut self, on: bool) {
        if self.prefs.set_sounds_enabled(on) && on {
            self.hear_sounds();
        }
    }

    /// The volume slider was let go (`keep`) or abandoned.
    pub fn finish_volume(&mut self, keep: bool) {
        if self.prefs.finish_volume(keep) {
            self.hear_sounds();
        }
    }

    pub fn step_volume(&mut self, direction: i8) {
        if self.prefs.step_volume(direction) {
            self.hear_sounds();
        }
    }

    pub fn reset_sounds(&mut self) {
        if self.prefs.reset_sounds() {
            self.hear_sounds();
        }
    }

    /// Plays the orb's pop-up tone at the volume now set, so a change can be judged by ear.
    fn hear_sounds(&mut self) {
        self.shell.preview(self.prefs.settings.sounds);
    }

    // ---- by hand ----------------------------------------------------------------------------------------------------

    /// Picking a state by hand stops the demo.
    pub fn pick(&mut self, s: OrbState, t: f64) {
        self.demo = None;
        self.orbit.set_state(s, t);
    }

    /// Starts the scripted turn. Not while Orbit is driving: two storytellers would fight.
    pub fn start_demo(&mut self, t: f64) {
        if self.live_open() {
            return;
        }
        self.voice = false;
        self.run_step(0, t);
    }

    pub fn stop_demo(&mut self) {
        self.demo = None;
    }

    fn run_step(&mut self, i: usize, t: f64) {
        let i = i % DEMO.len();
        let st = &DEMO[i];
        if self.orbit.state != st.state {
            self.orbit.set_state(st.state, t);
        }
        self.demo = Some(DemoRun { step: i, due: t + st.ms / 1000.0 });
    }

    /// The demo step on screen, if the demo is running.
    pub fn demo_step(&self) -> Option<&'static DemoStep> {
        self.demo.map(|d| &DEMO[d.step])
    }

    pub fn voice_active(&self) -> bool {
        self.voice || self.demo_step().is_some_and(|d| d.voice)
    }

    /// The slider moved: that value is the level, and nothing else drives it.
    pub fn set_manual_level(&mut self, v: f32) {
        self.manual = v.clamp(0.0, 1.0);
        self.voice = false;
        self.demo = None;
        self.orbit.set_level(self.manual);
        self.shown_level = self.orbit.level;
    }

    pub fn set_reduced(&mut self, on: bool, t: f64) {
        self.orbit.set_reduced(on, t);
    }

    // ---- the link to Orbit ------------------------------------------------------------------------------------------

    /// Remembers what wakes the UI when the link thread has news. Set once, before connecting.
    pub fn set_waker(&mut self, waker: Waker) {
        self.live.waker = waker;
    }

    /// Is Orbit connected, and so driving the sphere?
    pub fn live_open(&self) -> bool {
        self.live.status == Status::Open
    }

    /// Starts connecting to `url` (any link already in use is closed first).
    pub fn connect(&mut self, url: &str, t: f64) {
        self.drop_link(t);
        match Link::connect(url, self.live.waker.clone()) {
            Ok(link) => {
                self.live.link = Some(link);
                self.live.status = Status::Connecting;
                self.live.notice = None;
            }
            Err(why) => {
                self.live.status = Status::Error;
                self.live.notice = Some(why);
            }
        }
    }

    /// Closes the link. An Orbit this window started keeps running.
    pub fn disconnect(&mut self, t: f64) {
        self.drop_link(t);
        self.live.status = Status::Off;
        self.live.notice = None;
    }

    fn drop_link(&mut self, t: f64) {
        self.live.link = None;
        if self.live_open() {
            self.go_dark(t);
        }
        self.live.status = Status::Off;
    }

    /// Starts Orbit (or its scripted demo) and connects to it.
    pub fn spawn(&mut self, kind: Kind, t: f64) {
        match Spawned::start(kind) {
            Ok(child) => {
                let url = child.url.clone();
                self.live.child = Some(child);
                self.live.url.replace_text(url.clone());
                self.connect(&url, t);
            }
            Err(why) => {
                self.live.status = Status::Error;
                self.live.notice = Some(why);
            }
        }
    }

    fn go_live(&mut self, t: f64) {
        self.orbit.auto_return = false;
        self.demo = None;
        self.voice = false;
        self.live.pacer.clear();
        self.live.notice = None;
        let _ = t;
    }

    fn go_dark(&mut self, t: f64) {
        self.orbit.auto_return = true;
        self.orbit.set_state(OrbState::Idle, t);
        self.live.pacer.clear();
        self.live.level = 0.0;
        self.live.confirm = None;
        self.live.ring = None;                                  // nothing can stop it from here, and Orbit's ringing stops with Orbit
        self.live.caption = None;
    }

    fn drain_link(&mut self, t: f64) -> bool {
        let events = self.live.link.as_ref().map(Link::poll).unwrap_or_default();
        let mut changed = !events.is_empty();
        for event in events {
            self.on_event(event, t);
        }
        if let Some(child) = self.live.child.as_mut()
            && let Some(why) = child.exited()
        {
            self.live.child = None;
            self.live.notice = Some(why);
            changed = true;
        }
        changed
    }

    /// One thing the link thread reported.
    pub fn on_event(&mut self, event: Event, t: f64) {
        match event {
            Event::Status(status) => {
                let was_open = self.live_open();
                self.live.status = status;
                match status {
                    Status::Open => self.go_live(t),
                    _ if was_open => self.go_dark(t),
                    _ => {}
                }
            }
            Event::Message(raw) => {
                self.live.last_message = clip(&raw, 140);
                for message in message::parse(&raw) {
                    self.apply(message, t);
                }
            }
        }
    }

    /// One message from Orbit.
    pub fn apply(&mut self, message: Incoming, t: f64) {
        match message {
            Incoming::Hello { state, level } => {
                self.live.pacer.clear();
                self.orbit.set_state(state, t);
                self.live.level = level;
            }
            Incoming::State { state, seconds, .. } => self.live.pacer.push(state, seconds, t),
            Incoming::Level(v) => self.live.level = v,
            Incoming::Transcript { text, .. } => self.say("You", &text, t),
            Incoming::Reply { text, emotion, .. } => {
                self.live.feeling = emotion;
                self.say("Orbit", &text, t);
            }
            Incoming::Reminder { id, title, text, kind, ringing } => {
                self.reminders.reload_if_changed();                    // the list has moved on: show it at once
                if ringing {
                    self.live.ring = Some(Ring { id, title, kind, text: text.clone(), sent: false });
                }
                self.reminder_said = Some(text);
            }
            Incoming::AlarmStopped { id } => {
                if self.live.ring.as_ref().is_some_and(|ring| ring.id == id) {
                    self.live.ring = None;
                }
            }
            Incoming::Confirm { id, question, action } => self.live.confirm = Some(Ask { id, action, question, sent: None }),
            Incoming::ConfirmDone { id, .. } => {
                if self.live.confirm.as_ref().is_some_and(|ask| ask.id == id) {
                    self.live.confirm = None;
                }
            }
            Incoming::Error(why) => self.live.notice = Some(why),
        }
    }

    fn say(&mut self, who: &str, text: &str, t: f64) {
        self.live.caption = Some(Caption { who: who.to_owned(), text: clip(text, CAPTION_CHARS), shown_at: t });
    }

    /// What is shown under the sphere: what Orbit last heard or said, else the demo's line.
    pub fn caption(&self) -> Option<(String, String)> {
        match (&self.live.caption, self.demo_step()) {
            (Some(c), _) => Some((c.who.clone(), c.text.clone())),
            (None, Some(d)) if !d.cap.is_empty() => Some((d.who.to_owned(), d.cap.to_owned())),
            _ => None,
        }
    }

    /// Answers the open question. The message to send, if there is a question still waiting for an answer.
    pub fn take_answer(&mut self, yes: bool) -> Option<String> {
        let ask = self.live.confirm.as_mut().filter(|a| a.sent.is_none())?;
        ask.sent = Some(yes);
        Some(message::confirm_answer(ask.id, yes))
    }

    /// The Stop button: the message that silences the ringing reminder, if one is ringing and Stop was not already pressed.
    pub fn take_dismiss(&mut self) -> Option<String> {
        let ring = self.live.ring.as_mut().filter(|r| !r.sent)?;
        ring.sent = true;
        Some(message::dismiss_request(&ring.id))
    }

    /// Stops the ringing over the link. False if nothing is ringing, or the link is gone (then Stop can be pressed again).
    pub fn stop_alarm(&mut self) -> bool {
        let Some(text) = self.take_dismiss() else { return false };
        let sent = self.live.link.as_ref().is_some_and(|link| link.send(text));
        if !sent && let Some(ring) = self.live.ring.as_mut() {
            ring.sent = false;
        }
        sent
    }

    /// Answers the open question over the link. False if there is none, or the link is gone.
    pub fn answer_confirm(&mut self, yes: bool) -> bool {
        let Some(text) = self.take_answer(yes) else { return false };
        let sent = self.live.link.as_ref().is_some_and(|link| link.send(text));
        if !sent && let Some(ask) = self.live.confirm.as_mut() {
            ask.sent = None;
        }
        sent
    }

    // ---- the floating orb --------------------------------------------------------------------------------------------

    /// Is Orbit doing anything the orb should be there for? (What was said is not shown on the orb, so it never keeps it up.)
    pub fn active(&self) -> bool {
        !matches!(self.orbit.state, OrbState::Idle | OrbState::Muted) || self.live.confirm.is_some() || self.live.ring.is_some()
    }

    /// What the orb's window shows now.
    pub fn face(&self) -> Face {
        let keys = self.prefs.hotkeys();
        let place = layout::place(self.prefs.look(), layout::screen(), self.live.confirm.is_some() || self.live.ring.is_some());
        Face { orbit: self.orbit.clone(), ask: self.live.confirm.clone(), ring: self.live.ring.clone(), keys: false, yes: keys.yes.clone(), no: keys.no.clone(), hub: self.shell.hub.clone(), place }
    }

    /// Is the Customization page open? The orb is on screen for as long as it is, so moving it can be seen where it will be.
    pub fn previewing(&self) -> bool {
        self.page == Page::Settings && self.prefs.section == Section::Customization
    }

    /// Brings the orb's window up to date: its content, whether it is showing, and its size.
    pub fn sync_shell(&mut self, t: f64) {
        // The orb's chord is let go while a new one is being recorded, so pressing it reaches the page instead of toggling the orb.
        let wanted = self.prefs.recording.is_none().then(|| self.prefs.hotkeys().orb.clone());
        self.prefs.orb_error = self.shell.sync_orb_chord(wanted.as_deref()).err();
        if let (Some(why), true) = (&self.prefs.orb_error, std::env::var_os("ORBIT_UI_DEBUG").is_some()) {
            eprintln!("[hub] orb chord: {why}");
        }
        let visible = self.presence.visible(self.active() || self.previewing(), t);
        let face = self.face();
        self.shell.apply(face, visible, self.prefs.settings.sounds);
    }

    /// Pins the orb on screen, or lets it go again (the hotkey and the tray icon).
    pub fn toggle_pin(&mut self, t: f64) {
        self.presence.toggle_pin();
        if std::env::var_os("ORBIT_UI_DEBUG").is_some() {
            eprintln!("[hub] pin toggled at {t:.2}s: pinned={}", self.presence.pinned());
        }
        self.sync_shell(t);
    }

    /// When the app next needs to wake by itself: something timed, or the orb due to leave.
    pub fn next_wake(&self, now: f64) -> Option<f64> {
        let saving = (self.page == Page::Notes).then(|| self.notes.save_due()).flatten();
        [self.next_due(), self.presence.leaves_at(now), saving].into_iter().flatten().reduce(f64::min)
    }

    // ---- time -------------------------------------------------------------------------------------------------------

    /// When something timed is next due: a demo step, a sphere hand-over, a paced state, or a caption leaving.
    pub fn next_due(&self) -> Option<f64> {
        [self.demo.map(|d| d.due), self.orbit.next_due(), self.live.pacer.next_due(), self.caption_expires()]
            .into_iter()
            .flatten()
            .reduce(f64::min)
    }

    fn caption_expires(&self) -> Option<f64> {
        self.live.caption.as_ref().map(|c| c.shown_at + CAPTION_SECONDS)
    }

    /// Runs everything due by `t`, in order, then sets the level for this frame. True when something
    /// the panel shows changed.
    pub fn advance(&mut self, t: f64) -> bool {
        let mut changed = self.drain_link(t) | self.drain_background();
        if self.page == Page::Notes {
            changed |= self.notes.save_if_due(t);                   // what is typed is saved a moment after the typing stops
        }
        for _ in 0..64 {
            let Some(due) = self.next_due().filter(|d| *d <= t) else { break };
            if self.demo.map(|d| d.due) == Some(due) {
                let next = self.demo.map_or(0, |d| d.step + 1);
                self.run_step(next, due);
            } else if self.live.pacer.next_due() == Some(due) {
                for shown in self.live.pacer.drain(due) {
                    if let Some(window) = shown.seconds.filter(|_| shown.state == OrbState::FollowUp) {
                        self.orbit.follow_window = window;
                    }
                    self.orbit.set_state(shown.state, shown.at);
                }
            } else if self.orbit.next_due() == Some(due) {
                self.orbit.tick(due);
            } else {
                // Only a caption was due: it has been up long enough, unless Orbit is still speaking it.
                self.live.caption = None;
            }
            changed = true;
        }
        if self.orbit.state == OrbState::Speaking
            && let Some(c) = self.live.caption.as_mut()
        {
            c.shown_at = t;                                       // a long reply stays up while it is being spoken
        }

        let level = if self.live_open() {
            self.live.level
        } else if self.voice_active() {
            voice_level(t)
        } else if self.demo.is_some() {
            0.0
        } else {
            self.manual
        };
        self.orbit.set_level(level);
        if t - self.last_push > 0.07 && (self.orbit.level - self.shown_level).abs() > 0.004 {
            self.shown_level = self.orbit.level;
            self.last_push = t;
            changed = true;
        }
        changed
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_voice_stays_in_range_and_actually_varies() {
        let (mut lo, mut hi) = (1.0f32, 0.0f32);
        for i in 0..4000 {
            let v = voice_level(i as f64 * 0.01);
            assert!((0.0..=1.0).contains(&v));
            lo = lo.min(v);
            hi = hi.max(v);
        }
        assert!(lo < 0.2 && hi > 0.6, "pauses and loud syllables: {lo}..{hi}");
    }

    #[test]
    fn the_demo_plays_one_full_turn_in_order_and_loops() {
        let mut app = App::default();
        app.start_demo(10.0);
        assert_eq!(app.demo_step().unwrap().chip, "Idle");
        let mut seen = vec![app.orbit.state];
        let mut t = 10.0;
        while t < 10.0 + 16.2 {
            t += 0.05;
            app.advance(t);
            if *seen.last().unwrap() != app.orbit.state {
                seen.push(app.orbit.state);
            }
        }
        use OrbState::*;
        assert_eq!(&seen[..9], &[Idle, Wake, Listening, Transcribing, Thinking, Success, Idle, Speaking, FollowUp]);
        assert_eq!(seen[9], Idle, "the follow-up hands over to idle");
        assert!(app.demo.is_some(), "still running");
    }

    #[test]
    fn the_whole_turn_lasts_fourteen_point_four_two_seconds() {
        let total: f64 = DEMO.iter().map(|s| s.ms).sum();
        assert_eq!(total, 14_420.0);
    }

    #[test]
    fn a_late_frame_catches_up_every_step_it_missed() {
        let mut app = App::default();
        app.start_demo(0.0);
        assert!(app.advance(4.0));
        assert_eq!(app.demo_step().unwrap().chip, "Listening", "1.5 + 0.42 + 2.6 > 4, so it is in the listening step");
        assert_eq!(app.orbit.state, OrbState::Listening);
    }

    #[test]
    fn picking_a_state_stops_the_demo_and_the_slider_takes_back_the_level() {
        let mut app = App::default();
        app.start_demo(0.0);
        app.pick(OrbState::Thinking, 1.0);
        assert!(app.demo.is_none());
        assert_eq!(app.orbit.state, OrbState::Thinking);
        app.voice = true;
        app.set_manual_level(0.8);
        assert!(!app.voice);
        app.advance(2.0);
        assert_eq!(app.orbit.level, 0.8);
        assert_eq!(app.shown_level, 0.8);
    }

    #[test]
    fn the_simulated_voice_drives_the_level_and_the_panel_reads_it_throttled() {
        let mut app = App { voice: true, ..App::default() };
        let mut pushes = 0;
        for i in 0..120 {
            if app.advance(i as f64 / 60.0) {
                pushes += 1;
            }
        }
        assert!(pushes > 3 && pushes <= 30, "about 14 a second over 2 s: {pushes}");
    }

    #[test]
    fn next_due_is_the_earliest_of_the_demo_and_the_hand_over() {
        let mut app = App::default();
        assert_eq!(app.next_due(), None);
        app.start_demo(5.0);
        assert_eq!(app.next_due(), Some(6.5), "the idle step lasts 1.5 s");
        app.orbit.set_state(OrbState::Wake, 5.2);
        let due = app.next_due().unwrap();
        assert!((due - 5.6).abs() < 1e-9, "the wake hand-over comes sooner than the next step: {due}");
    }

    // ------------------------------------------------------------------ driven by Orbit
    fn live() -> App {
        let mut app = App::default();
        app.on_event(Event::Status(Status::Open), 0.0);
        app
    }

    fn msg(app: &mut App, raw: &str, t: f64) {
        app.on_event(Event::Message(raw.to_owned()), t);
    }

    #[test]
    fn connecting_hands_the_sphere_to_orbit_and_stops_the_hand_made_things() {
        let mut app = App::default();
        app.start_demo(0.0);
        app.voice = true;
        app.on_event(Event::Status(Status::Open), 1.0);
        assert!(app.live_open() && !app.orbit.auto_return);
        assert!(app.demo.is_none() && !app.voice);
        app.start_demo(2.0);
        assert!(app.demo.is_none(), "the demo cannot start while Orbit drives");
    }

    #[test]
    fn a_new_face_is_told_where_orbit_is_and_shows_it_at_once() {
        let mut app = live();
        msg(&mut app, r#"{"type":"hello","version":1,"state":"thinking","level":0.5}"#, 3.0);
        app.advance(3.0);
        assert_eq!(app.orbit.state, OrbState::Thinking);
        assert_eq!(app.orbit.level, 0.5);
    }

    #[test]
    fn orbits_states_arrive_paced_and_its_level_replaces_the_simulated_one() {
        let mut app = live();
        msg(&mut app, r#"{"state":"wake"}"#, 10.0);
        msg(&mut app, r#"{"state":"listening"}"#, 10.1);
        msg(&mut app, r#"{"level":0.7}"#, 10.1);
        app.advance(10.1);
        assert_eq!(app.orbit.state, OrbState::Wake, "the wake flash plays first");
        assert_eq!(app.orbit.level, 0.7);
        assert_eq!(app.next_due(), Some(10.4));
        app.advance(10.4);
        assert_eq!(app.orbit.state, OrbState::Listening);
        assert_eq!(app.orbit.entered_at, 10.4, "stamped with the time it was due");
    }

    #[test]
    fn nothing_hands_over_on_its_own_while_orbit_drives() {
        let mut app = live();
        msg(&mut app, r#"{"state":"success"}"#, 1.0);
        app.advance(1.0);
        app.advance(50.0);
        assert_eq!(app.orbit.state, OrbState::Success, "only Orbit moves it on");
    }

    #[test]
    fn the_follow_up_arc_follows_the_window_orbit_announces() {
        let mut app = live();
        msg(&mut app, r#"{"state":"follow_up","seconds":8.0}"#, 2.0);
        app.advance(2.0);
        assert_eq!(app.orbit.follow_window, 8.0);
        assert_eq!(app.orbit.state, OrbState::FollowUp);
    }

    #[test]
    fn what_was_said_is_shown_and_leaves_after_six_seconds_of_quiet() {
        let mut app = live();
        msg(&mut app, r#"{"type":"transcript","who":"you","text":"open  spotify"}"#, 1.0);
        assert_eq!(app.caption(), Some(("You".into(), "open spotify".into())));
        assert_eq!(app.next_due(), Some(7.0));
        app.advance(6.9);
        assert!(app.caption().is_some());
        assert!(app.advance(7.0));
        assert_eq!(app.caption(), None);
    }

    #[test]
    fn the_orb_does_not_wait_for_a_caption_it_does_not_show() {
        let mut app = live();
        msg(&mut app, r#"{"type":"reply","text":"Gotcha.","spoken":"Gotcha.","kind":"chat","emotion":"neutral"}"#, 1.0);
        assert!(app.live.caption.is_some() && app.caption().is_some(), "the window still has it");
        assert!(!app.active(), "but an idle Orbit with something to read is not a reason to show the orb");
        msg(&mut app, r#"{"state":"speaking"}"#, 1.0);
        app.advance(1.0);
        assert!(app.active());
        msg(&mut app, r#"{"state":"idle"}"#, 3.0);
        app.advance(3.0);
        assert!(!app.active(), "idle again: the orb's linger runs from here, not from the caption's six seconds");
    }

    #[test]
    fn a_reply_stays_up_for_as_long_as_it_is_being_spoken() {
        let mut app = live();
        msg(&mut app, r#"{"state":"speaking"}"#, 1.0);
        msg(&mut app, r#"{"type":"reply","text":"A long answer.","spoken":"A long answer.","kind":"chat","emotion":"proud"}"#, 1.0);
        for i in 0..400 {
            app.advance(1.0 + i as f64 * 0.1);                    // forty seconds of speech
        }
        assert_eq!(app.caption(), Some(("Orbit".into(), "A long answer.".into())));
        assert_eq!(app.live.feeling, "proud");
        msg(&mut app, r#"{"state":"idle"}"#, 41.0);
        app.advance(41.0);
        app.advance(48.0);
        assert_eq!(app.caption(), None, "gone six seconds after it stopped");
    }

    #[test]
    fn a_very_long_reply_is_cut_for_the_glance_not_the_screen_filled() {
        let mut app = live();
        let long = "word ".repeat(300);
        msg(&mut app, &format!(r#"{{"type":"reply","text":"{long}","spoken":"","kind":"action","emotion":"neutral"}}"#), 1.0);
        let (_, text) = app.caption().unwrap();
        assert!(text.chars().count() <= CAPTION_CHARS && text.ends_with('\u{2026}'));
    }

    #[test]
    fn a_risky_action_opens_a_question_and_the_answer_goes_back_once() {
        let mut app = live();
        msg(&mut app, r#"{"type":"confirm","id":4,"question":"q","action":"close window Notepad"}"#, 1.0);
        assert_eq!(app.live.confirm.as_ref().map(|a| a.action.as_str()), Some("close window Notepad"));
        let sent = app.take_answer(true).expect("an answer to send");
        assert_eq!(serde_json::from_str::<serde_json::Value>(&sent).unwrap(), serde_json::json!({"type": "confirm", "id": 4, "yes": true}));
        assert_eq!(app.take_answer(false), None, "the first answer stands");
        assert_eq!(app.live.confirm.as_ref().unwrap().sent, Some(true));
        msg(&mut app, r#"{"type":"confirm_done","id":4,"answer":true,"by":"ui"}"#, 1.5);
        assert!(app.live.confirm.is_none());
    }

    #[test]
    fn a_settled_question_for_another_id_leaves_this_one_open() {
        let mut app = live();
        msg(&mut app, r#"{"type":"confirm","id":4,"question":"q","action":"a"}"#, 1.0);
        msg(&mut app, r#"{"type":"confirm_done","id":3,"answer":false,"by":"voice"}"#, 1.1);
        assert!(app.live.confirm.is_some());
        msg(&mut app, r#"{"type":"confirm_done","id":4,"answer":false,"by":"voice"}"#, 1.2);
        assert!(app.live.confirm.is_none(), "answered by voice: the question closes on screen too");
    }

    #[test]
    fn with_no_link_an_answer_cannot_be_sent_and_the_question_stays_open() {
        let mut app = live();
        msg(&mut app, r#"{"type":"confirm","id":4,"question":"q","action":"a"}"#, 1.0);
        assert!(!app.answer_confirm(true));
        assert_eq!(app.live.confirm.as_ref().unwrap().sent, None, "so the buttons come back");
    }

    #[test]
    fn losing_orbit_returns_the_sphere_to_idle_and_clears_what_belonged_to_the_turn() {
        let mut app = live();
        msg(&mut app, r#"{"state":"listening"}"#, 1.0);
        msg(&mut app, r#"{"type":"transcript","who":"you","text":"hi"}"#, 1.0);
        msg(&mut app, r#"{"type":"confirm","id":1,"question":"q","action":"a"}"#, 1.0);
        msg(&mut app, r#"{"level":0.9}"#, 1.0);
        app.advance(1.0);
        app.on_event(Event::Status(Status::Closed), 2.0);
        assert_eq!(app.live.status, Status::Closed);
        assert_eq!(app.orbit.state, OrbState::Idle);
        assert!(app.orbit.auto_return, "the panel's own timers work again");
        assert!(app.live.caption.is_none() && app.live.confirm.is_none());
        app.advance(2.1);
        assert_eq!(app.orbit.level, 0.35, "back to the slider");
    }

    #[test]
    fn a_ringing_timer_shows_a_stop_button_that_asks_orbit_once_and_goes_when_orbit_says_it_stopped() {
        let mut app = live();
        msg(&mut app, r#"{"type":"reminder","id":"r-2","title":"Pasta","text":"Your timer is up.","kind":"timer","ringing":true}"#, 1.0);
        let ring = app.live.ring.clone().expect("it is ringing");
        assert_eq!((ring.id.as_str(), ring.heading(), ring.text.as_str(), ring.sent), ("r-2", "Timer", "Your timer is up.", false));
        assert!(app.active(), "the orb comes up for it even if Orbit is idle");

        let sent = app.take_dismiss().expect("a stop to send");
        assert_eq!(serde_json::from_str::<serde_json::Value>(&sent).unwrap(), serde_json::json!({"type": "dismiss", "id": "r-2"}));
        assert_eq!(app.take_dismiss(), None, "pressed once: it is asked once");
        assert!(app.live.ring.as_ref().unwrap().sent);

        msg(&mut app, r#"{"type":"alarm_stopped","id":"r-9","by":"voice"}"#, 1.5);
        assert!(app.live.ring.is_some(), "another reminder's stop does not end this ring");
        msg(&mut app, r#"{"type":"alarm_stopped","id":"r-2","by":"voice"}"#, 1.6);
        assert!(app.live.ring.is_none() && !app.active());
    }

    #[test]
    fn a_reminder_that_only_came_due_does_not_ring_and_each_kind_has_its_name() {
        let mut app = live();
        msg(&mut app, r#"{"type":"reminder","id":"r-1","title":"Call mom","text":"Reminder: Call mom."}"#, 1.0);
        assert!(app.live.ring.is_none(), "a late one is only said");
        for (kind, name) in [("reminder", "Reminder"), ("alarm", "Alarm"), ("timer", "Timer")] {
            msg(&mut app, &format!(r#"{{"type":"reminder","id":"r-{kind}","title":"x","text":"t","kind":"{kind}","ringing":true}}"#), 1.0);
            assert_eq!(app.live.ring.as_ref().unwrap().heading(), name);
        }
    }

    #[test]
    fn with_no_link_stop_cannot_be_sent_and_can_be_pressed_again() {
        let mut app = live();
        msg(&mut app, r#"{"type":"reminder","id":"r-2","title":"Pasta","text":"Your timer is up.","kind":"timer","ringing":true}"#, 1.0);
        assert!(!app.stop_alarm());
        assert!(!app.live.ring.as_ref().unwrap().sent, "so the button comes back");
        assert!(!App::default().stop_alarm(), "nothing ringing, nothing to stop");
    }

    #[test]
    fn the_face_carries_the_ringing_card_and_losing_orbit_ends_the_ring() {
        let mut app = live();
        msg(&mut app, r#"{"type":"reminder","id":"r-2","title":"Pasta","text":"Your timer is up.","kind":"timer","ringing":true}"#, 1.0);
        assert_eq!(app.face().ring.as_ref().map(|r| r.id.as_str()), Some("r-2"));
        let face = app.face();
        assert_eq!(face.size().1, face.place.diameter + layout::SPARE + layout::ASK_EXTRA, "the orb's window grows to hold the card");
        app.on_event(Event::Status(Status::Closed), 2.0);
        assert!(app.live.ring.is_none(), "Orbit stopping stops its ringing, and nothing could answer a Stop now");
    }

    #[test]
    fn the_kill_switch_shows_at_once_even_behind_a_flash() {
        let mut app = live();
        msg(&mut app, r#"{"state":"success"}"#, 1.0);
        msg(&mut app, r#"{"state":"speaking"}"#, 1.0);
        app.advance(1.0);
        msg(&mut app, r#"{"state":"muted","reason":"kill switch"}"#, 1.1);
        app.advance(1.1);
        assert_eq!(app.orbit.state, OrbState::Muted);
    }

    #[test]
    fn messages_that_are_not_understood_change_nothing_and_orbits_errors_are_shown() {
        let mut app = live();
        for raw in ["", "garbage", "[]", r#"{"state":"dancing"}"#, r#"{"type":"future_thing","x":1}"#, r#"{"type":"transcript"}"#] {
            msg(&mut app, raw, 1.0);
        }
        app.advance(1.0);
        assert_eq!(app.orbit.state, OrbState::Idle);
        assert!(app.live.caption.is_none() && app.live.notice.is_none());
        msg(&mut app, r#"{"type":"error","message":"no such confirmation is open"}"#, 2.0);
        assert_eq!(app.live.notice.as_deref(), Some("no such confirmation is open"));
    }

    #[test]
    fn a_link_to_somewhere_else_is_an_error_not_a_connection() {
        let mut app = App::default();
        app.connect("ws://example.com:8765/?token=x", 0.0);
        assert_eq!(app.live.status, Status::Error);
        assert!(app.live.notice.as_deref().unwrap().contains("this PC"));
        assert!(!app.live_open());
    }

    #[test]
    fn the_last_message_is_kept_for_the_panel_but_clipped() {
        let mut app = live();
        msg(&mut app, &format!(r#"{{"type":"error","message":"{}"}}"#, "x".repeat(500)), 1.0);
        assert!(app.live.last_message.chars().count() <= 140);
    }
}

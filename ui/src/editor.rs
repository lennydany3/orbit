//! The Settings page's state and the rules that move it: recording a hotkey, the audio and voice choices, editing a command, saving. No
//! drawing and no windowing, so all of it is tested; `settings_view` and `commands_view` only draw it.
//!
//! Every change is saved the moment it is made (a hotkey picked, a command saved, switched on or off, deleted), as one whole
//! file written in one go. If the save fails the change is not made, and the page says why.

use std::path::PathBuf;

use rune::{Key, KeyEvent, Modifier, PointerButton, TextEdit};

use crate::settings::{self, Command, Hotkeys, MAX_COMMANDS, MAX_NAME, MAX_PHRASE, MAX_PHRASES, MAX_STEPS, OrbLook, OrbSize, Settings, Sounds, Step};

/// The tabs of the Settings page. (Commands is a page of its own, in the sidebar.)
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Section {
    General,
    Hotkeys,
    Sounds,
    Customization,
}

impl Section {
    pub const ALL: [Section; 4] = [Section::General, Section::Hotkeys, Section::Sounds, Section::Customization];

    pub fn label(self) -> &'static str {
        match self {
            Section::General => "General",
            Section::Hotkeys => "Shortcuts",
            Section::Sounds => "Sounds",
            Section::Customization => "Orb",
        }
    }
}

/// How far the volume's + and - buttons move it.
pub const VOLUME_STEP: u8 = 10;

/// The three hotkeys recorded by pressing them. (The talk key is picked from a list: it is a single key, held.)
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Field {
    Orb,
    Yes,
    No,
}

impl Field {
    pub const ALL: [Field; 3] = [Field::Orb, Field::Yes, Field::No];

    pub fn name(self) -> &'static str {
        match self {
            Field::Orb => "orb",
            Field::Yes => "yes",
            Field::No => "no",
        }
    }

    pub fn default_chord(self) -> &'static str {
        match self {
            Field::Orb => settings::DEFAULT_ORB,
            Field::Yes => settings::DEFAULT_YES,
            Field::No => settings::DEFAULT_NO,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StepKind {
    Open,
    Keys,
    Type,
    Wait,
}

impl StepKind {
    pub const ALL: [StepKind; 4] = [StepKind::Open, StepKind::Keys, StepKind::Type, StepKind::Wait];

    pub fn label(self) -> &'static str {
        match self {
            StepKind::Open => "Open",
            StepKind::Keys => "Press keys",
            StepKind::Type => "Type text",
            StepKind::Wait => "Wait",
        }
    }

    /// What goes in the box, shown as the box's grey example.
    pub fn example(self) -> &'static str {
        match self {
            StepKind::Open => "Spotify, github.com or C:\\Users\\you\\Downloads",
            StepKind::Keys => "ctrl+shift+s",
            StepKind::Type => "Text to type into the window in front",
            StepKind::Wait => "1.5",
        }
    }

    pub fn hint(self) -> &'static str {
        match self {
            StepKind::Open => "One app, website or folder per step, as if you said \"open ...\" (Spotify, my github, github.com). Folders only open; nothing is ever run.",
            StepKind::Keys => "Modifiers ctrl, alt, shift, win plus one key, like win+d or alt+tab.",
            StepKind::Type => "Typed into whichever window is in front when this step runs.",
            StepKind::Wait => "Seconds to wait before the next step, up to 30.",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Notice {
    pub text: String,
    pub good: bool,
}

impl Notice {
    fn good(text: impl Into<String>) -> Option<Notice> {
        Some(Notice { text: text.into(), good: true })
    }

    fn bad(text: impl Into<String>) -> Option<Notice> {
        Some(Notice { text: text.into(), good: false })
    }
}

#[derive(Clone)]
pub struct StepDraft {
    pub kind: StepKind,
    pub edit: TextEdit,
}

impl StepDraft {
    fn new(kind: StepKind, text: &str) -> StepDraft {
        StepDraft { kind, edit: TextEdit::new(text) }
    }

    fn from_step(step: &Step) -> StepDraft {
        match step {
            Step::Open(t) => StepDraft::new(StepKind::Open, t),
            Step::Keys(k) => StepDraft::new(StepKind::Keys, k),
            Step::Type(t) => StepDraft::new(StepKind::Type, t),
            Step::Wait(s) => StepDraft::new(StepKind::Wait, &format!("{s}")),
        }
    }

    fn step(&self) -> Result<Step, String> {
        let text = self.edit.text.as_str();
        match self.kind {
            StepKind::Open => Step::Open(text.to_owned()).checked(),
            StepKind::Keys => Step::Keys(text.to_owned()).checked(),
            StepKind::Type => Step::Type(text.to_owned()).checked(),
            StepKind::Wait => match text.trim().replace(',', ".").parse::<f64>() {
                Ok(s) => Step::Wait(s).checked(),
                Err(_) => Err(format!("a wait is a number of seconds, like 1.5 (up to {})", settings::MAX_WAIT_S)),
            },
        }
    }
}

/// A command being made or changed, before it is saved.
#[derive(Clone)]
pub struct Draft {
    /// Which command this edits; `None` for a new one.
    pub index: Option<usize>,
    pub name: TextEdit,
    pub phrases: Vec<TextEdit>,
    pub steps: Vec<StepDraft>,
    /// Why the last Save did not go through.
    pub errors: Vec<String>,
    base: Option<Command>,
}

impl Draft {
    fn new_command() -> Draft {
        Draft { index: None, name: TextEdit::new(""), phrases: vec![TextEdit::new("")], steps: vec![StepDraft::new(StepKind::Open, "")], errors: Vec::new(), base: None }
    }

    fn of(index: usize, command: &Command) -> Draft {
        Draft {
            index: Some(index),
            name: TextEdit::new(command.name.as_str()),
            phrases: command.phrases.iter().map(|p| TextEdit::new(p.as_str())).collect(),
            steps: command.steps.iter().map(StepDraft::from_step).collect(),
            errors: Vec::new(),
            base: Some(command.clone()),
        }
    }

    /// Does anything in it press keys or type? (The page says Orbit will ask first.)
    pub fn sends_keys(&self) -> bool {
        self.steps.iter().any(|s| matches!(s.kind, StepKind::Keys | StepKind::Type))
    }

    /// The finished command, or everything that is wrong with it, in words.
    pub fn build(&self, all: &[Command]) -> Result<Command, Vec<String>> {
        let mut errors = Vec::new();
        let name: String = self.name.text.split_whitespace().collect::<Vec<_>>().join(" ");
        if name.is_empty() {
            errors.push("Give the command a name.".to_owned());
        } else if name.chars().count() > MAX_NAME {
            errors.push(format!("The name is longer than {MAX_NAME} characters."));
        }

        let mut phrases: Vec<String> = Vec::new();
        for edit in &self.phrases {
            let phrase = settings::normalize_phrase(&edit.text);
            if phrase.is_empty() || phrases.contains(&phrase) {
                continue;
            }
            if phrase.chars().count() > MAX_PHRASE {
                errors.push(format!("\"{phrase}\" is longer than {MAX_PHRASE} characters."));
            } else if let Some(other) = all.iter().enumerate().find(|(i, c)| Some(*i) != self.index && c.enabled && c.phrases.contains(&phrase)) {
                errors.push(format!("\"{phrase}\" already starts \"{}\".", other.1.name));
            } else {
                phrases.push(phrase);
            }
        }
        if phrases.is_empty() && !errors.iter().any(|e| e.contains("already starts") || e.contains("longer than")) {
            errors.push("Add at least one thing to say to start it.".to_owned());
        }
        if phrases.len() > MAX_PHRASES {
            errors.push(format!("At most {MAX_PHRASES} phrases."));
        }

        let mut steps = Vec::new();
        for (i, draft) in self.steps.iter().enumerate() {
            match draft.step() {
                Ok(step) => steps.push(step),
                Err(why) => errors.push(format!("Step {}: {why}.", i + 1)),
            }
        }
        if self.steps.is_empty() {
            errors.push("Add at least one step.".to_owned());
        } else if self.steps.len() > MAX_STEPS {
            errors.push(format!("At most {MAX_STEPS} steps."));
        }
        if !errors.is_empty() {
            return Err(errors);
        }
        Ok(match &self.base {
            Some(base) => base.with_parts(name, phrases, steps),
            None => {
                let mut id = settings::slug(&name);
                let mut n = 2;
                while all.iter().any(|c| c.id == id) {
                    id = format!("{}-{n}", settings::slug(&name));
                    n += 1;
                }
                Command::new(id, name, phrases, steps)
            }
        })
    }
}

/// What pressing a key while recording the hold-to-talk key means.
#[derive(Clone, Debug, PartialEq)]
pub enum TalkRecorded {
    /// Something not worth a reaction (a key going up, Fn): keep listening.
    Ignore,
    /// Esc.
    Cancel,
    /// The key, by the name Orbit uses.
    Key(String),
    /// A key that cannot be the talk key, and why.
    Refused(String),
}

/// A key press as the hold-to-talk key. Any single key held down works (Right Ctrl, F9, Pause...), but one that types something is
/// refused: holding a letter to talk would type it into every app.
pub fn talk_from_event(ev: &KeyEvent) -> TalkRecorded {
    let refused = |what: &str| TalkRecorded::Refused(format!("{what} would be typed into every app while you talk. Use Right Ctrl, a function key or a mouse button."));
    let name = match &ev.key {
        Key::Modifier { pressed: false, .. } => return TalkRecorded::Ignore,
        Key::Modifier { modifier, .. } => match modifier {
            Modifier::ControlRight => "ctrl_r",
            Modifier::ControlLeft => "ctrl_l",
            Modifier::AltRight => "alt_r",
            Modifier::AltLeft => "alt_l",
            Modifier::ShiftRight => "shift_r",
            Modifier::ShiftLeft => "shift_l",
            Modifier::SuperLeft | Modifier::SuperRight => return TalkRecorded::Refused("The Windows key opens the Start menu, so it cannot be used to talk.".to_owned()),
            _ => return TalkRecorded::Ignore,
        }
        .to_owned(),
        Key::Escape => return TalkRecorded::Cancel,
        Key::Function(n) if (1..=24).contains(n) => format!("f{n}"),
        Key::Insert => "insert".to_owned(),
        Key::Home => "home".to_owned(),
        Key::End => "end".to_owned(),
        Key::PageUp => "page_up".to_owned(),
        Key::PageDown => "page_down".to_owned(),
        Key::Pause => "pause".to_owned(),
        Key::ScrollLock => "scroll_lock".to_owned(),
        Key::PrintScreen => "print_screen".to_owned(),
        Key::ContextMenu => "menu".to_owned(),
        Key::Text(_) | Key::Command(_) => return refused("A letter, a number or a symbol"),
        Key::Enter | Key::Backspace | Key::Delete | Key::Tab => return refused("That key"),
        Key::Left | Key::Right | Key::Up | Key::Down => return refused("An arrow key"),
        _ => return TalkRecorded::Ignore,
    };
    TalkRecorded::Key(name)
}

/// A mouse button as the hold-to-talk key, by the name Orbit uses (the left button is mouse1, the right mouse2, the wheel mouse3, the
/// two on the side mouse4, back, and mouse5, forward).
pub fn talk_from_button(button: PointerButton) -> Option<&'static str> {
    match button {
        PointerButton::Primary => Some("mouse1"),
        PointerButton::Secondary => Some("mouse2"),
        PointerButton::Middle => Some("mouse3"),
        PointerButton::Back => Some("mouse4"),
        PointerButton::Forward => Some("mouse5"),
        _ => None,
    }
}

/// What pressing a key while recording a hotkey means.
#[derive(Clone, Debug, PartialEq)]
pub enum Recorded {
    /// A modifier on its own, or something else not worth a reaction: keep listening.
    Ignore,
    /// Esc by itself.
    Cancel,
    Chord(String),
    /// A key that cannot be part of a hotkey.
    Refused(String),
}

/// A key press as the chord it makes: modifiers held plus the key, in the file's spelling.
/// `base` is what the key types with no modifiers on this keyboard layout: with Ctrl and Alt held, many layouts (AltGr) type a
/// different character (Ctrl+Alt+Y types an n with a tilde), but the hotkey is still the key marked Y.
pub fn chord_from_event(ev: &KeyEvent, base: Option<char>) -> Recorded {
    let typed = match &ev.key {
        Key::Command(c) => Some(*c),
        Key::Text(t) if t.chars().count() == 1 => t.chars().next(),
        _ => None,
    };
    let any_mod = ev.mods.ctrl || ev.mods.alt || ev.mods.logo || ev.mods.cmd || ev.mods.shift;
    let key: String = match &ev.key {
        Key::Modifier { .. } => return Recorded::Ignore,
        Key::Escape if !any_mod => return Recorded::Cancel,
        Key::Escape => "esc".into(),
        _ if typed.is_some() => {
            let c = base.filter(|b| b.is_ascii_graphic() || *b == ' ').or(typed).unwrap_or(' ');
            match key_name_for(c) {
                Some(name) => name,
                None => return Recorded::Refused(format!("{c} cannot be used in a hotkey.")),
            }
        }
        Key::Function(n) => format!("f{n}"),
        Key::Enter => "enter".into(),
        Key::Tab => "tab".into(),
        Key::Backspace => "backspace".into(),
        Key::Delete => "delete".into(),
        Key::Insert => "insert".into(),
        Key::Home => "home".into(),
        Key::End => "end".into(),
        Key::PageUp => "pageup".into(),
        Key::PageDown => "pagedown".into(),
        Key::Up => "up".into(),
        Key::Down => "down".into(),
        Key::Left => "left".into(),
        Key::Right => "right".into(),
        Key::PrintScreen => "printscreen".into(),
        _ => return Recorded::Ignore,
    };
    let mut parts: Vec<&str> = Vec::new();
    if ev.mods.ctrl || ev.mods.cmd {
        parts.push("ctrl");
    }
    if ev.mods.alt {
        parts.push("alt");
    }
    if ev.mods.shift {
        parts.push("shift");
    }
    if ev.mods.logo {
        parts.push("win");
    }
    parts.push(&key);
    match settings::parse_hotkey(&parts.join("+")) {
        Ok(chord) => Recorded::Chord(chord),
        Err(why) => Recorded::Refused(why),
    }
}

/// The name of the key that types `c` (a shifted character is the key it is shifted from, on a US keyboard).
fn key_name_for(c: char) -> Option<String> {
    let c = c.to_lowercase().next().unwrap_or(c);
    let shifted = "~!@#$%^&*()_+{}|:\"<>?";
    let plain = "`1234567890-=[]\\;',./";
    let c = shifted.chars().position(|s| s == c).and_then(|i| plain.chars().nth(i)).unwrap_or(c);
    Some(
        match c {
            'a'..='z' | '0'..='9' => return Some(c.to_string()),
            ' ' => "space",
            '-' => "minus",
            '=' => "equals",
            ',' => "comma",
            '.' => "period",
            '/' => "slash",
            ';' => "semicolon",
            '\'' => "quote",
            '[' => "lbracket",
            ']' => "rbracket",
            '\\' => "backslash",
            '`' => "backtick",
            _ => return None,
        }
        .to_owned(),
    )
}

/// Everything the Settings page shows and edits.
pub struct SettingsState {
    /// Where it is saved. `None` saves nothing (headless scenes, tests).
    pub path: Option<PathBuf>,
    pub settings: Settings,
    /// What was left out when the file was read.
    pub problems: Vec<String>,
    existed: bool,
    backed_up: bool,
    pub section: Section,
    pub draft: Option<Draft>,
    pub recording: Option<Field>,
    /// Is the hold-to-talk key being recorded? (Every key and every mouse button is for it, until one is taken.)
    pub recording_talk: bool,
    pub notice: Option<Notice>,
    /// The command waiting for "are you sure" before it is deleted.
    pub confirm_delete: Option<usize>,
    /// Why Windows would not give Orbit the orb's chord, if it would not: it does not work until it is changed.
    pub orb_error: Option<String>,
    /// The volume while its slider is being dragged: shown at once, saved when the drag ends.
    volume_live: Option<u8>,
    /// The same for the slider of Orbit's own voice.
    speaking_live: Option<u8>,
    /// Where the orb is while it is being dragged: shown at once, saved when the drag ends.
    orb_live: Option<(f32, f32)>,
    /// Handed out when an edit box's command has gone, so a late keystroke has somewhere harmless to go.
    scratch: TextEdit,
}

impl Default for SettingsState {
    fn default() -> Self {
        SettingsState {
            path: None,
            settings: Settings::default(),
            problems: Vec::new(),
            existed: false,
            backed_up: false,
            section: Section::General,
            draft: None,
            recording: None,
            recording_talk: false,
            notice: None,
            confirm_delete: None,
            orb_error: None,
            volume_live: None,
            speaking_live: None,
            orb_live: None,
            scratch: TextEdit::new(""),
        }
    }
}

impl SettingsState {
    /// Settings from JSON text, saved nowhere (for the headless scenes and tests).
    pub fn from_json(json: &str) -> SettingsState {
        let (settings, problems) = match serde_json::from_str(json) {
            Ok(data) => Settings::parse(&data),
            Err(e) => (Settings::default(), vec![format!("not valid JSON ({e})")]),
        };
        SettingsState { settings, problems, ..Default::default() }
    }

    /// The settings in `path`, which is also where changes are saved.
    pub fn open(path: PathBuf) -> SettingsState {
        let loaded = settings::load(&path);
        SettingsState { settings: loaded.settings, problems: loaded.problems, existed: loaded.existed, path: Some(path), ..Default::default() }
    }

    pub fn hotkeys(&self) -> &Hotkeys {
        &self.settings.hotkeys
    }

    pub fn hotkey(&self, field: Field) -> &str {
        match field {
            Field::Orb => &self.settings.hotkeys.orb,
            Field::Yes => &self.settings.hotkeys.yes,
            Field::No => &self.settings.hotkeys.no,
        }
    }

    /// Says something went wrong.
    pub fn fail(&mut self, text: impl Into<String>) {
        self.notice = Notice::bad(text);
    }

    pub fn select(&mut self, section: Section) {
        self.section = section;
        self.recording = None;
        self.recording_talk = false;
        self.notice = None;
        self.confirm_delete = None;
        self.volume_live = None;
        self.speaking_live = None;
        self.orb_live = None;
    }

    /// Writes `next` (and, the first time, keeps a copy of a file that had entries Orbit could not read), then makes it current.
    /// Says whether it kept that copy.
    fn commit(&mut self, next: Settings) -> Result<bool, String> {
        let mut kept_copy = false;
        if let Some(path) = &self.path {
            if self.existed && !self.problems.is_empty() && !self.backed_up {
                let copy = path.with_extension("json.bak");
                std::fs::copy(path, &copy).map_err(|e| format!("could not keep a copy of {} first: {e}", path.display()))?;
                self.backed_up = true;
                kept_copy = true;
            }
            next.save(path)?;
            self.existed = true;
        }
        self.settings = next;
        if kept_copy {
            self.problems.clear();
        }
        Ok(kept_copy)
    }

    /// Turns the outcome of a save into what the page says.
    fn saved(&mut self, change: Result<bool, String>, message: &str) -> bool {
        match change {
            Ok(kept_copy) => {
                let extra = if kept_copy { " The file had entries Orbit could not read; the old one is kept as settings.json.bak." } else { "" };
                self.notice = Notice::good(format!("{message}{extra}"));
                true
            }
            Err(why) => {
                self.notice = Notice::bad(why);
                false
            }
        }
    }

    // ---- hotkeys ----------------------------------------------------------------------------------------------------

    pub fn begin_record(&mut self, field: Field) {
        self.recording = Some(field);
        self.recording_talk = false;
        self.notice = None;
    }

    /// Starts recording the hold-to-talk key: the next key, or mouse button, pressed on the page is it.
    pub fn begin_record_talk(&mut self) {
        self.recording_talk = true;
        self.recording = None;
        self.notice = None;
    }

    pub fn cancel_record(&mut self) {
        self.recording = None;
        self.recording_talk = false;
    }

    /// `text` as a chord for `field`, or why not: valid, and not the same as another of the three.
    pub fn check_hotkey(&self, field: Field, text: &str) -> Result<String, String> {
        let chord = settings::parse_hotkey(text)?;
        for other in Field::ALL.into_iter().filter(|f| *f != field) {
            if self.hotkey(other) == chord {
                return Err(format!("{} is already used for \"{}\".", settings::pretty(&chord), describe_field(other)));
            }
        }
        Ok(chord)
    }

    /// Saves `chord` (already checked) as `field`'s hotkey.
    pub fn apply_hotkey(&mut self, field: Field, chord: String) -> bool {
        let mut next = self.settings.clone();
        match field {
            Field::Orb => next.hotkeys.orb = chord.clone(),
            Field::Yes => next.hotkeys.yes = chord.clone(),
            Field::No => next.hotkeys.no = chord.clone(),
        }
        let done = self.commit(next);
        self.saved(done, &format!("{} is now {}.", describe_field(field), settings::pretty(&chord)))
    }

    pub fn set_talk(&mut self, name: &str) -> bool {
        if !settings::valid_talk_key(name) {
            self.notice = Notice::bad(format!("{name} cannot be used to talk."));
            return false;
        }
        let mut next = self.settings.clone();
        next.hotkeys.talk = name.to_owned();
        let done = self.commit(next);
        self.recording_talk = false;
        self.saved(done, &format!("Hold {} to talk. Orbit uses it straight away.", settings::talk_label(name)))
    }

    /// All four back to what they were out of the box.
    pub fn reset_hotkeys(&mut self) -> bool {
        let mut next = self.settings.clone();
        next.hotkeys = Hotkeys::default();
        let done = self.commit(next);
        self.recording = None;
        self.recording_talk = false;
        self.saved(done, "The hotkeys are back to the way they came.")
    }

    // ---- commands ---------------------------------------------------------------------------------------------------

    pub fn new_command(&mut self) {
        if self.settings.commands.len() >= MAX_COMMANDS {
            self.notice = Notice::bad(format!("That is as many commands as Orbit keeps ({MAX_COMMANDS}). Delete one first."));
            return;
        }
        self.notice = None;
        self.draft = Some(Draft::new_command());
    }

    pub fn edit_command(&mut self, index: usize) {
        if let Some(command) = self.settings.commands.get(index) {
            self.notice = None;
            self.draft = Some(Draft::of(index, command));
        }
    }

    pub fn cancel_draft(&mut self) {
        self.draft = None;
    }

    pub fn add_phrase(&mut self) {
        if let Some(d) = self.draft.as_mut().filter(|d| d.phrases.len() < MAX_PHRASES) {
            d.phrases.push(TextEdit::new(""));
        }
    }

    pub fn remove_phrase(&mut self, i: usize) {
        if let Some(d) = &mut self.draft {
            if d.phrases.len() > 1 && i < d.phrases.len() {
                d.phrases.remove(i);
            }
        }
    }

    pub fn add_step(&mut self, kind: StepKind) {
        if let Some(d) = self.draft.as_mut().filter(|d| d.steps.len() < MAX_STEPS) {
            d.steps.push(StepDraft::new(kind, if kind == StepKind::Wait { "1" } else { "" }));
        }
    }

    pub fn remove_step(&mut self, i: usize) {
        if let Some(d) = &mut self.draft {
            if d.steps.len() > 1 && i < d.steps.len() {
                d.steps.remove(i);
            }
        }
    }

    /// Moves a step up (`-1`) or down (`1`).
    pub fn move_step(&mut self, i: usize, by: isize) {
        if let Some(d) = &mut self.draft {
            let to = i as isize + by;
            if i < d.steps.len() && to >= 0 && (to as usize) < d.steps.len() {
                d.steps.swap(i, to as usize);
            }
        }
    }

    /// Changes what kind of step it is. The text stays (it is still what you typed), except a wait, which needs a number.
    pub fn set_step_kind(&mut self, i: usize, kind: StepKind) {
        if let Some(step) = self.draft.as_mut().and_then(|d| d.steps.get_mut(i)) {
            step.kind = kind;
            if kind == StepKind::Wait && step.edit.text.trim().parse::<f64>().is_err() {
                step.edit.replace_text("1");
            }
        }
    }

    pub fn name_edit(&mut self) -> &mut TextEdit {
        match &mut self.draft {
            Some(d) => &mut d.name,
            None => &mut self.scratch,
        }
    }

    pub fn phrase_edit(&mut self, i: usize) -> &mut TextEdit {
        match self.draft.as_mut().and_then(|d| d.phrases.get_mut(i)) {
            Some(e) => e,
            None => &mut self.scratch,
        }
    }

    pub fn step_edit(&mut self, i: usize) -> &mut TextEdit {
        match self.draft.as_mut().and_then(|d| d.steps.get_mut(i)) {
            Some(s) => &mut s.edit,
            None => &mut self.scratch,
        }
    }

    /// Save the command being edited. False (and the reasons on the draft) if it is not complete.
    pub fn save_draft(&mut self) -> bool {
        let Some(draft) = &self.draft else { return false };
        let built = match draft.build(&self.settings.commands) {
            Ok(c) => c,
            Err(errors) => {
                if let Some(d) = &mut self.draft {
                    d.errors = errors;
                }
                return false;
            }
        };
        let index = draft.index;
        let name = built.name.clone();
        let mut next = self.settings.clone();
        match index {
            Some(i) if i < next.commands.len() => next.commands[i] = built,
            _ => next.commands.push(built),
        }
        match self.commit(next) {
            Ok(kept_copy) => {
                self.draft = None;
                self.saved(Ok(kept_copy), &format!("\"{name}\" is saved. Say one of its phrases to run it."));
                true
            }
            Err(why) => {
                if let Some(d) = &mut self.draft {
                    d.errors = vec![why];
                }
                false
            }
        }
    }

    /// Switches a command on or off. A command cannot be switched on if another one that is on already has its phrase.
    pub fn toggle(&mut self, index: usize) -> bool {
        let Some(command) = self.settings.commands.get(index) else { return false };
        let mut next = self.settings.clone();
        let turning_on = !command.enabled;
        if turning_on {
            if let Some((phrase, owner)) = command.phrases.iter().find_map(|p| {
                self.settings.commands.iter().enumerate().find(|(i, c)| *i != index && c.enabled && c.phrases.contains(p)).map(|(_, c)| (p, c.name.clone()))
            }) {
                self.notice = Notice::bad(format!("\"{phrase}\" already starts \"{owner}\". Change one of them first."));
                return false;
            }
        }
        next.commands[index].enabled = turning_on;
        let name = next.commands[index].name.clone();
        let done = self.commit(next);
        self.saved(done, &format!("\"{name}\" is {}.", if turning_on { "on" } else { "off" }))
    }

    pub fn ask_delete(&mut self, index: usize) {
        self.confirm_delete = Some(index).filter(|i| *i < self.settings.commands.len());
    }

    pub fn delete(&mut self, index: usize) -> bool {
        self.confirm_delete = None;
        if index >= self.settings.commands.len() {
            return false;
        }
        let mut next = self.settings.clone();
        let gone = next.commands.remove(index);
        let done = self.commit(next);
        self.saved(done, &format!("\"{}\" is deleted.", gone.name))
    }
}

// ---- sounds -----------------------------------------------------------------------------------------------------------

impl SettingsState {
    /// The sound settings as the page shows them: the volume follows the slider while it is being dragged.
    pub fn sounds(&self) -> Sounds {
        Sounds { volume: self.volume_live.unwrap_or(self.settings.sounds.volume), ..self.settings.sounds }
    }

    pub fn set_sounds_enabled(&mut self, on: bool) -> bool {
        let mut next = self.settings.clone();
        next.sounds.enabled = on;
        let done = self.commit(next);
        self.saved(done, if on { "Sounds are on." } else { "Sounds are off." })
    }

    /// The slider is at `percent` (0 to 100): shown at once, not yet saved.
    pub fn drag_volume(&mut self, percent: u8) {
        self.volume_live = Some(percent.min(100));
    }

    /// The drag ended. `keep` saves where it was left; otherwise the slider goes back to what was saved. True if a new volume
    /// was saved.
    pub fn finish_volume(&mut self, keep: bool) -> bool {
        let Some(volume) = self.volume_live.take() else { return false };
        if !keep || volume == self.settings.sounds.volume {
            return false;
        }
        self.save_volume(volume)
    }

    /// Moves the volume up (`1`) or down (`-1`) by a step, stopping at 0 and 100.
    pub fn step_volume(&mut self, direction: i8) -> bool {
        let now = self.sounds().volume;
        let next = if direction < 0 { now.saturating_sub(VOLUME_STEP) } else { now.saturating_add(VOLUME_STEP).min(100) };
        self.volume_live = None;
        if next == self.settings.sounds.volume {
            return false;
        }
        self.save_volume(next)
    }

    fn save_volume(&mut self, volume: u8) -> bool {
        let mut next = self.settings.clone();
        next.sounds.volume = volume;
        let done = self.commit(next);
        self.saved(done, &format!("The volume is {volume}%."))
    }

    /// Both sound settings back to what they were out of the box.
    pub fn reset_sounds(&mut self) -> bool {
        let mut next = self.settings.clone();
        next.sounds = Sounds::default();
        self.volume_live = None;
        let done = self.commit(next);
        self.saved(done, "The sounds are back to the way they came.")
    }
}

// ---- audio and voice --------------------------------------------------------------------------------------------------

impl SettingsState {
    /// How loud Orbit's voice is as the page shows it: it follows the slider while it is being dragged.
    pub fn speaking(&self) -> u8 {
        self.speaking_live.unwrap_or(self.settings.audio.volume)
    }

    /// The slider is at `percent` (0 to 100): shown at once, not yet saved.
    pub fn drag_speaking(&mut self, percent: u8) {
        self.speaking_live = Some(percent.min(100));
    }

    /// The drag ended. `keep` saves where it was left; otherwise the slider goes back. True if a new volume was saved.
    pub fn finish_speaking(&mut self, keep: bool) -> bool {
        let Some(volume) = self.speaking_live.take() else { return false };
        if !keep || volume == self.settings.audio.volume {
            return false;
        }
        self.save_speaking(volume)
    }

    /// Moves it up (`1`) or down (`-1`) by a step, stopping at 0 and 100.
    pub fn step_speaking(&mut self, direction: i8) -> bool {
        let now = self.speaking();
        let next = if direction < 0 { now.saturating_sub(VOLUME_STEP) } else { now.saturating_add(VOLUME_STEP).min(100) };
        self.speaking_live = None;
        if next == self.settings.audio.volume {
            return false;
        }
        self.save_speaking(next)
    }

    fn save_speaking(&mut self, volume: u8) -> bool {
        let mut next = self.settings.clone();
        next.audio.volume = volume;
        let done = self.commit(next);
        self.saved(done, &format!("Orbit speaks at {volume}%."))
    }

    /// Picks the microphone by its name ("" = the Windows default).
    pub fn set_mic(&mut self, name: &str) -> bool {
        self.set_device(name, true)
    }

    /// Picks the speaker by its name ("" = the Windows default).
    pub fn set_speaker(&mut self, name: &str) -> bool {
        self.set_device(name, false)
    }

    fn set_device(&mut self, name: &str, mic: bool) -> bool {
        let Some(device) = settings::parse_device(&serde_json::Value::String(name.to_owned())) else {
            self.notice = Notice::bad("That is not a device name Orbit can use.");
            return false;
        };
        let mut next = self.settings.clone();
        let (field, what) = if mic { (&mut next.audio.mic, "microphone") } else { (&mut next.audio.speaker, "speaker") };
        if *field == device {
            return false;
        }
        field.clone_from(&device);
        let done = self.commit(next);
        let shown = if device.is_empty() { "the Windows default".to_owned() } else { device };
        self.saved(done, &format!("Orbit uses {shown} as its {what}. It switches between turns, with no restart."))
    }

    pub fn set_wake_word(&mut self, on: bool) -> bool {
        let mut next = self.settings.clone();
        next.voice.wake_word = on;
        let done = self.commit(next);
        self.saved(done, if on { "Orbit listens for \"Hey Orbit\"." } else { "The wake word is off: hold the talk key to speak to Orbit." })
    }

    pub fn set_keep_listening(&mut self, on: bool) -> bool {
        let mut next = self.settings.clone();
        next.voice.keep_listening = on;
        let done = self.commit(next);
        self.saved(done, if on { "Orbit stays ready for a moment after it replies." } else { "Orbit goes back to waiting after each reply." })
    }

    /// The audio and voice choices back to what they were out of the box.
    pub fn reset_general(&mut self) -> bool {
        let mut next = self.settings.clone();
        next.audio = settings::Audio::default();
        next.voice = settings::VoiceOptions::default();
        self.speaking_live = None;
        let done = self.commit(next);
        self.saved(done, "The audio and voice choices are back to the way they came.")
    }
}

// ---- customization ----------------------------------------------------------------------------------------------------

/// The nine spots the page offers by name: rows from the top, columns from the left.
pub const SPOTS: [[&str; 3]; 3] = [["Top left", "Top", "Top right"], ["Left", "Center", "Right"], ["Bottom left", "Bottom", "Bottom right"]];

/// Where a spot is, as fractions: column 0, 1, 2 -> 0, 0.5, 1.
pub fn spot_at(row: usize, col: usize) -> (f32, f32) {
    (col.min(2) as f32 / 2.0, row.min(2) as f32 / 2.0)
}

impl SettingsState {
    /// The orb as the page and the screen show it: where the pointer has it while it is being dragged.
    pub fn look(&self) -> OrbLook {
        match self.orb_live {
            Some((x, y)) => self.settings.orb.at(x, y),
            None => self.settings.orb,
        }
    }

    /// The orb's name for a place, if it is on one of the nine spots ("Bottom right"), else `None`.
    pub fn spot_name(&self) -> Option<&'static str> {
        let look = self.look();
        (0..3).flat_map(|r| (0..3).map(move |c| (r, c))).find(|&(r, c)| {
            let (x, y) = spot_at(r, c);
            (look.x - x).abs() < 0.0005 && (look.y - y).abs() < 0.0005
        }).map(|(r, c)| SPOTS[r][c])
    }

    /// The orb is at (`x`, `y`) (0 to 1) under the pointer: shown at once, not yet saved.
    pub fn drag_orb(&mut self, x: f32, y: f32) {
        self.orb_live = Some((x.clamp(0.0, 1.0), y.clamp(0.0, 1.0)));
    }

    /// The drag ended. `keep` saves where it was left; otherwise the orb goes back to where it was. True if a new place was saved.
    pub fn finish_orb(&mut self, keep: bool) -> bool {
        let Some((x, y)) = self.orb_live.take() else { return false };
        let next = self.settings.orb.at(x, y);
        if !keep || next == self.settings.orb {
            return false;
        }
        self.save_orb(next, "The orb will appear there.")
    }

    /// One of the nine spots, or any place at all, saved at once.
    pub fn place_orb(&mut self, x: f32, y: f32) -> bool {
        self.orb_live = None;
        let next = self.settings.orb.at(x, y);
        if next == self.settings.orb {
            return false;
        }
        let message = match SPOTS.iter().enumerate().find_map(|(r, row)| row.iter().enumerate().find(|&(c, _)| spot_at(r, c) == (next.x, next.y)).map(|(_, name)| *name)) {
            Some(name) => format!("The orb will appear at the {}.", name.to_lowercase()),
            None => "The orb will appear there.".to_owned(),
        };
        self.save_orb(next, &message)
    }

    pub fn set_orb_size(&mut self, size: OrbSize) -> bool {
        if size == self.settings.orb.size {
            return false;
        }
        self.save_orb(OrbLook { size, ..self.settings.orb }, &format!("The orb is {}.", size.label().to_lowercase()))
    }

    /// Back to the corner at the middle size.
    pub fn reset_orb(&mut self) -> bool {
        self.orb_live = None;
        self.save_orb(OrbLook::default(), "The orb is back in the bottom right corner, at the middle size.")
    }

    fn save_orb(&mut self, look: OrbLook, message: &str) -> bool {
        let mut next = self.settings.clone();
        next.orb = look;
        let done = self.commit(next);
        self.saved(done, message)
    }
}

pub fn describe_field(field: Field) -> &'static str {
    match field {
        Field::Orb => "Show or hide the orb",
        Field::Yes => "Answer yes",
        Field::No => "Answer no",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::tests::tempdir;
    use rune::Mods;

    const EXAMPLE: &str = include_str!("../../tests/fixtures/settings_example.json");

    fn mods(ctrl: bool, alt: bool, shift: bool, logo: bool) -> Mods {
        Mods { ctrl, cmd: ctrl, alt, shift, logo }
    }

    fn press(key: Key, m: Mods) -> Recorded {
        chord_from_event(&KeyEvent { key, mods: m }, None)
    }

    /// A page that saves into a fresh folder, holding the example file.
    fn page() -> (SettingsState, PathBuf) {
        let dir = tempdir("editor");
        let file = dir.join("settings.json");
        std::fs::write(&file, EXAMPLE).unwrap();
        (SettingsState::open(file.clone()), file)
    }

    fn on_disk(file: &PathBuf) -> Settings {
        settings::load(file).settings
    }

    fn fill(d: &mut Draft, name: &str, phrases: &[&str], steps: &[(StepKind, &str)]) {
        d.name = TextEdit::new(name);
        d.phrases = phrases.iter().map(|p| TextEdit::new(*p)).collect();
        d.steps = steps.iter().map(|(k, t)| StepDraft::new(*k, t)).collect();
    }

    // ---- recording a hotkey ----

    #[test]
    fn pressing_ctrl_alt_and_a_letter_records_that_chord() {
        assert_eq!(press(Key::Command('p'), mods(true, true, false, false)), Recorded::Chord("ctrl+alt+p".into()));
        assert_eq!(press(Key::Text("P".into()), mods(true, true, false, false)), Recorded::Chord("ctrl+alt+p".into()));
        assert_eq!(press(Key::Command('t'), mods(true, false, true, true)), Recorded::Chord("ctrl+shift+win+t".into()));
    }

    #[test]
    fn a_layout_that_types_something_else_with_alt_held_still_records_the_key_marked_on_the_cap() {
        let altgr = Key::Text("\u{f1}".into());
        let m = mods(true, true, false, false);
        assert_eq!(chord_from_event(&KeyEvent { key: altgr.clone(), mods: m }, Some('y')), Recorded::Chord("ctrl+alt+y".into()));
        assert!(matches!(chord_from_event(&KeyEvent { key: altgr, mods: m }, None), Recorded::Refused(_)), "without the layout's answer there is nothing to go on");
        assert_eq!(chord_from_event(&KeyEvent { key: Key::Function(9), mods: Mods::default() }, Some('y')), Recorded::Chord("f9".into()), "named keys ignore it");
    }

    #[test]
    fn function_keys_and_named_keys_work_with_or_without_modifiers() {
        assert_eq!(press(Key::Function(9), Mods::default()), Recorded::Chord("f9".into()));
        assert_eq!(press(Key::Function(2), mods(true, false, false, false)), Recorded::Chord("ctrl+f2".into()));
        assert_eq!(press(Key::Left, mods(false, true, false, false)), Recorded::Chord("alt+left".into()));
        assert_eq!(press(Key::PageDown, mods(true, true, false, false)), Recorded::Chord("ctrl+alt+pagedown".into()));
        assert_eq!(press(Key::Escape, mods(true, false, false, false)), Recorded::Chord("ctrl+esc".into()));
    }

    #[test]
    fn punctuation_is_recorded_as_the_key_it_is_on() {
        assert_eq!(press(Key::Command('-'), mods(true, true, false, false)), Recorded::Chord("ctrl+alt+minus".into()));
        assert_eq!(press(Key::Text("{".into()), mods(true, true, true, false)), press(Key::Command('['), mods(true, true, true, false)), "shifted is the same key");
        assert!(matches!(press(Key::Command('['), mods(true, true, false, false)), Recorded::Refused(why) if why.contains("cannot use")), "a key Windows will not take as a hotkey");
        assert_eq!(press(Key::Text("!".into()), mods(true, true, true, false)), Recorded::Chord("ctrl+alt+shift+1".into()));
    }

    #[test]
    fn a_modifier_on_its_own_is_ignored_and_escape_alone_cancels() {
        let m = Key::Modifier { modifier: rune::Modifier::ControlLeft, pressed: true };
        assert_eq!(press(m, mods(true, false, false, false)), Recorded::Ignore);
        assert_eq!(press(Key::Escape, Mods::default()), Recorded::Cancel);
    }

    #[test]
    fn a_key_with_no_ctrl_alt_or_win_is_refused_with_the_reason() {
        let Recorded::Refused(why) = press(Key::Command('p'), mods(false, false, true, false)) else { panic!() };
        assert!(why.contains("Ctrl, Alt or Win"), "{why}");
        assert!(matches!(press(Key::Text("o".into()), Mods::default()), Recorded::Refused(_)));
        assert!(matches!(press(Key::Text("\u{e9}".into()), mods(true, true, false, false)), Recorded::Refused(_)));
    }

    // ---- saving hotkeys ----

    #[test]
    fn a_new_hotkey_is_saved_to_the_file_at_once() {
        let (mut page, file) = page();
        let chord = page.check_hotkey(Field::Orb, "Ctrl+Alt+P").unwrap();
        assert!(page.apply_hotkey(Field::Orb, chord));
        assert_eq!(on_disk(&file).hotkeys.orb, "ctrl+alt+p");
        assert_eq!(page.hotkey(Field::Orb), "ctrl+alt+p");
        assert!(page.notice.as_ref().unwrap().good && page.notice.as_ref().unwrap().text.contains("Ctrl + Alt + P"));
        assert_eq!(on_disk(&file).commands.len(), 2, "the commands were not touched");
    }

    #[test]
    fn a_hotkey_already_used_for_something_else_is_refused_and_nothing_changes() {
        let (page, _) = page();
        let why = page.check_hotkey(Field::Orb, "ctrl+alt+y").unwrap_err();
        assert!(why.contains("Ctrl + Alt + Y") && why.contains("Answer yes"), "{why}");
        assert!(page.check_hotkey(Field::Orb, "ctrl+alt+o").is_ok(), "its own chord is fine");
        assert!(page.check_hotkey(Field::No, "shift+n").is_err());
    }

    #[test]
    fn the_talk_key_is_saved_and_only_a_real_key_is_accepted() {
        let (mut page, file) = page();
        assert!(page.set_talk("f10"));
        assert_eq!(on_disk(&file).hotkeys.talk, "f10");
        assert!(page.notice.as_ref().unwrap().text.contains("F10"));
        assert!(!page.set_talk("banana"));
        assert_eq!(on_disk(&file).hotkeys.talk, "f10");
        assert!(!page.notice.as_ref().unwrap().good);
    }

    #[test]
    fn resetting_puts_all_four_back() {
        let (mut page, file) = page();
        assert!(page.reset_hotkeys());
        assert_eq!(on_disk(&file).hotkeys, Hotkeys::default());
        assert_eq!(on_disk(&file).commands.len(), 2);
    }

    #[test]
    fn a_save_that_fails_changes_nothing_and_says_why() {
        let (mut page, file) = page();
        let before = page.settings.clone();
        page.path = Some(file.parent().unwrap().join("gone").join("settings.json"));
        assert!(!page.set_talk("f10"));
        assert_eq!(page.settings, before, "the page still shows what is really saved");
        assert!(page.notice.as_ref().unwrap().text.contains("could not write"));
    }

    // ---- commands ----

    #[test]
    fn a_new_command_is_built_saved_and_given_an_id() {
        let (mut page, file) = page();
        page.new_command();
        fill(page.draft.as_mut().unwrap(), "Movie night", &["Movie night!", "  "], &[(StepKind::Open, "Netflix"), (StepKind::Wait, "2,5"), (StepKind::Keys, "Win+D")]);
        assert!(page.save_draft());
        assert!(page.draft.is_none());
        let saved = on_disk(&file);
        let c = saved.commands.last().unwrap();
        assert_eq!((c.id.as_str(), c.name.as_str(), c.enabled), ("movie-night", "Movie night", true));
        assert_eq!(c.phrases, ["movie night"]);
        assert_eq!(c.steps, [Step::Open("Netflix".into()), Step::Wait(2.5), Step::Keys("win+d".into())]);
        assert!(page.notice.as_ref().unwrap().text.contains("is saved"));
    }

    #[test]
    fn two_commands_with_the_same_name_get_different_ids() {
        let (mut page, file) = page();
        for _ in 0..2 {
            page.new_command();
            fill(page.draft.as_mut().unwrap(), "Work mode", &[&format!("w{}", page.settings.commands.len())], &[(StepKind::Open, "x")]);
            assert!(page.save_draft());
        }
        let ids: Vec<String> = on_disk(&file).commands.iter().map(|c| c.id.clone()).collect();
        assert_eq!(ids, ["work-mode", "greet", "work-mode-2", "work-mode-3"]);
    }

    #[test]
    fn editing_keeps_the_id_and_whether_it_is_on_and_replaces_the_rest() {
        let (mut page, file) = page();
        page.edit_command(1);
        fill(page.draft.as_mut().unwrap(), "Say goodbye", &["say bye"], &[(StepKind::Type, "Bye!")]);
        assert!(page.save_draft());
        let c = &on_disk(&file).commands[1];
        assert_eq!((c.id.as_str(), c.name.as_str(), c.enabled, c.phrases.clone()), ("greet", "Say goodbye", false, vec!["say bye".to_owned()]));
        assert_eq!(on_disk(&file).commands.len(), 2);
    }

    #[test]
    fn an_incomplete_command_is_not_saved_and_every_problem_is_listed() {
        let (mut page, file) = page();
        page.new_command();
        fill(page.draft.as_mut().unwrap(), " ", &["", "!!"], &[(StepKind::Keys, "ctrl+banana"), (StepKind::Wait, "soon"), (StepKind::Open, "Spotify")]);
        assert!(!page.save_draft());
        let errors = page.draft.as_ref().unwrap().errors.clone();
        assert_eq!(errors.len(), 4, "{errors:?}");
        assert!(errors[0].contains("name") && errors[1].contains("thing to say") && errors[2].starts_with("Step 1:") && errors[3].starts_with("Step 2:"), "{errors:?}");
        assert_eq!(on_disk(&file).commands.len(), 2, "nothing was written");
    }

    #[test]
    fn a_phrase_another_command_already_uses_is_refused_by_name() {
        let (mut page, _) = page();
        page.new_command();
        fill(page.draft.as_mut().unwrap(), "Other", &["Work mode"], &[(StepKind::Open, "x")]);
        assert!(!page.save_draft());
        assert_eq!(page.draft.as_ref().unwrap().errors, ["\"work mode\" already starts \"Work mode\"."]);
    }

    #[test]
    fn a_switched_off_commands_phrase_can_be_used_again() {
        let (mut page, _) = page();
        page.new_command();
        fill(page.draft.as_mut().unwrap(), "Another hello", &["Say hello"], &[(StepKind::Open, "x")]);
        assert!(page.save_draft(), "the original 'say hello' is switched off");
        assert!(!page.toggle(1), "...but it cannot be switched back on while the new one has the phrase");
        assert!(page.notice.as_ref().unwrap().text.contains("already starts \"Another hello\""));
    }

    #[test]
    fn a_command_can_be_switched_on_and_off() {
        let (mut page, file) = page();
        assert!(page.toggle(0));
        assert!(!on_disk(&file).commands[0].enabled);
        assert!(page.notice.as_ref().unwrap().text.contains("is off"));
        assert!(page.toggle(0));
        assert!(on_disk(&file).commands[0].enabled);
        assert!(!page.toggle(9), "no such command");
    }

    #[test]
    fn deleting_asks_first_and_then_removes_only_that_command() {
        let (mut page, file) = page();
        page.ask_delete(0);
        assert_eq!(page.confirm_delete, Some(0));
        assert_eq!(on_disk(&file).commands.len(), 2, "asking changes nothing");
        assert!(page.delete(0));
        assert_eq!(page.confirm_delete, None);
        let left = on_disk(&file).commands;
        assert_eq!(left.len(), 1);
        assert_eq!(left[0].name, "Say hello");
        page.ask_delete(5);
        assert_eq!(page.confirm_delete, None);
        assert!(!page.delete(5));
    }

    #[test]
    fn steps_and_phrases_can_be_added_removed_and_moved_within_limits() {
        let (mut page, _) = page();
        page.new_command();
        page.add_step(StepKind::Wait);
        page.add_step(StepKind::Type);
        let kinds = |p: &SettingsState| p.draft.as_ref().unwrap().steps.iter().map(|s| s.kind).collect::<Vec<_>>();
        assert_eq!(kinds(&page), [StepKind::Open, StepKind::Wait, StepKind::Type]);
        assert_eq!(page.draft.as_ref().unwrap().steps[1].edit.text, "1", "a new wait starts at one second");
        page.move_step(2, -1);
        assert_eq!(kinds(&page), [StepKind::Open, StepKind::Type, StepKind::Wait]);
        page.move_step(0, -1);
        page.move_step(2, 1);
        assert_eq!(kinds(&page), [StepKind::Open, StepKind::Type, StepKind::Wait], "the ends stay put");
        page.remove_step(0);
        page.remove_step(0);
        page.remove_step(0);
        assert_eq!(page.draft.as_ref().unwrap().steps.len(), 1, "the last step cannot be removed");
        for _ in 0..40 {
            page.add_step(StepKind::Open);
            page.add_phrase();
        }
        let d = page.draft.as_ref().unwrap();
        assert_eq!((d.steps.len(), d.phrases.len()), (MAX_STEPS, MAX_PHRASES));
        page.remove_phrase(0);
        assert_eq!(page.draft.as_ref().unwrap().phrases.len(), MAX_PHRASES - 1);
    }

    #[test]
    fn changing_a_step_to_wait_swaps_text_for_a_number() {
        let (mut page, _) = page();
        page.new_command();
        page.step_edit(0).replace_text("Spotify");
        page.set_step_kind(0, StepKind::Wait);
        assert_eq!(page.step_edit(0).text, "1");
        page.step_edit(0).replace_text("2.5");
        page.set_step_kind(0, StepKind::Type);
        assert_eq!(page.step_edit(0).text, "2.5", "text stays when the kind changes to a text one");
    }

    #[test]
    fn a_late_keystroke_for_a_box_that_is_gone_goes_nowhere() {
        let (mut page, _) = page();
        page.step_edit(7).replace_text("lost");
        page.phrase_edit(3).replace_text("lost");
        page.name_edit().replace_text("lost");
        assert!(page.draft.is_none());
    }

    #[test]
    fn a_draft_knows_when_it_will_press_keys() {
        let (mut page, _) = page();
        page.new_command();
        assert!(!page.draft.as_ref().unwrap().sends_keys());
        page.add_step(StepKind::Keys);
        assert!(page.draft.as_ref().unwrap().sends_keys());
    }

    #[test]
    fn no_more_than_the_limit_of_commands() {
        let (mut page, _) = page();
        for i in 0..MAX_COMMANDS {
            page.settings.commands.push(Command::new(format!("c{i}"), format!("C{i}"), vec![format!("p{i}")], vec![Step::Wait(1.0)]));
        }
        page.new_command();
        assert!(page.draft.is_none() && !page.notice.as_ref().unwrap().good);
    }

    // ---- sounds ----

    #[test]
    fn the_sounds_switch_is_saved_at_once_and_leaves_the_rest_alone() {
        let (mut page, file) = page();
        assert_eq!(page.sounds(), Sounds { enabled: false, volume: 65 }, "as the example file has it");
        assert!(page.set_sounds_enabled(true));
        assert_eq!(on_disk(&file).sounds, Sounds { enabled: true, volume: 65 });
        assert!(page.notice.as_ref().unwrap().good && page.notice.as_ref().unwrap().text.contains("on"));
        assert!(page.set_sounds_enabled(false));
        assert!(!on_disk(&file).sounds.enabled && page.notice.as_ref().unwrap().text.contains("off"));
        assert_eq!((on_disk(&file).commands.len(), on_disk(&file).hotkeys.talk.as_str()), (2, "f9"));
    }

    #[test]
    fn dragging_the_volume_shows_it_at_once_but_saves_only_where_the_drag_ends() {
        let (mut page, file) = page();
        page.drag_volume(10);
        page.drag_volume(30);
        page.drag_volume(80);
        assert_eq!(page.sounds().volume, 80, "the slider follows the pointer");
        assert_eq!(on_disk(&file).sounds.volume, 65, "but nothing was written during the drag");
        assert!(page.finish_volume(true));
        assert_eq!((on_disk(&file).sounds.volume, page.sounds().volume), (80, 80));
        assert!(page.notice.as_ref().unwrap().text.contains("80%"));
    }

    #[test]
    fn a_cancelled_drag_puts_the_volume_back_and_writes_nothing() {
        let (mut page, file) = page();
        page.drag_volume(5);
        assert!(!page.finish_volume(false));
        assert_eq!((page.sounds().volume, on_disk(&file).sounds.volume), (65, 65));
        assert!(page.notice.is_none());
    }

    #[test]
    fn letting_go_where_it_started_is_not_a_change() {
        let (mut page, _) = page();
        page.drag_volume(65);
        assert!(!page.finish_volume(true));
        assert!(page.notice.is_none(), "no 'saved' for nothing");
        assert!(!page.finish_volume(true), "and with no drag going there is nothing to finish");
    }

    #[test]
    fn the_volume_buttons_move_it_by_ten_and_stop_at_the_ends() {
        let (mut page, file) = page();
        assert!(page.step_volume(1));
        assert_eq!(on_disk(&file).sounds.volume, 75);
        for _ in 0..5 {
            page.step_volume(1);
        }
        assert_eq!(on_disk(&file).sounds.volume, 100);
        assert!(!page.step_volume(1), "already as loud as it goes");
        for _ in 0..12 {
            page.step_volume(-1);
        }
        assert_eq!(on_disk(&file).sounds.volume, 0);
        assert!(!page.step_volume(-1));
    }

    #[test]
    fn a_volume_that_is_not_a_multiple_of_ten_steps_to_the_next_one_either_way() {
        let (mut page, _) = page();
        page.drag_volume(43);
        assert!(page.finish_volume(true));
        assert!(page.step_volume(1) && page.sounds().volume == 53);
        assert!(page.step_volume(-1) && page.sounds().volume == 43);
    }

    #[test]
    fn resetting_the_sounds_puts_back_on_and_quiet_and_keeps_the_hotkeys() {
        let (mut page, file) = page();
        assert!(page.reset_sounds());
        assert_eq!(on_disk(&file).sounds, Sounds::default());
        assert_eq!(on_disk(&file).hotkeys.talk, "f9");
    }

    #[test]
    fn a_sound_change_that_cannot_be_saved_changes_nothing_and_says_why() {
        let (mut page, file) = page();
        let before = page.settings.clone();
        page.path = Some(file.parent().unwrap().join("gone").join("settings.json"));
        assert!(!page.set_sounds_enabled(true));
        assert!(!page.step_volume(1));
        page.drag_volume(90);
        assert!(!page.finish_volume(true));
        assert_eq!(page.settings, before);
        assert_eq!(page.sounds().volume, 65, "the slider is back at what is really saved");
        assert!(page.notice.as_ref().unwrap().text.contains("could not write"));
    }

    #[test]
    fn leaving_the_sounds_section_drops_a_drag_in_progress() {
        let mut page = SettingsState::default();
        page.select(Section::Sounds);
        page.drag_volume(5);
        page.select(Section::Hotkeys);
        assert_eq!(page.sounds().volume, 40);
    }

    // ---- a file with things Orbit cannot read ----

    #[test]
    fn the_first_save_keeps_a_copy_of_a_file_that_had_entries_orbit_could_not_read() {
        let dir = tempdir("backup");
        let file = dir.join("settings.json");
        let original = r#"{"commands":[{"name":"Broken","phrases":["x"],"steps":[{"kind":"run","program":"calc.exe"}]}]}"#;
        std::fs::write(&file, original).unwrap();
        let mut page = SettingsState::open(file.clone());
        assert_eq!(page.problems.len(), 1);
        assert!(page.set_talk("f9"));
        assert_eq!(std::fs::read_to_string(dir.join("settings.json.bak")).unwrap(), original, "nothing the user wrote is lost");
        assert!(page.problems.is_empty() && page.notice.as_ref().unwrap().text.contains("settings.json.bak"));
        assert!(page.set_talk("f10"));
        assert_eq!(std::fs::read_to_string(dir.join("settings.json.bak")).unwrap(), original, "the copy is made once, not overwritten");
    }

    #[test]
    fn a_page_with_no_file_saves_by_creating_it() {
        let dir = tempdir("fresh");
        let file = dir.join("settings.json");
        let mut page = SettingsState::open(file.clone());
        assert!(!file.exists());
        assert!(page.set_talk("f9"));
        assert_eq!(on_disk(&file).hotkeys.talk, "f9");
    }

    #[test]
    fn a_page_with_no_path_changes_only_what_it_shows() {
        let mut page = SettingsState::default();
        assert!(page.set_talk("f9") && page.reset_hotkeys());
        assert_eq!(page.hotkeys().talk, "ctrl_r");
    }

    #[test]
    fn switching_sections_drops_a_half_finished_recording() {
        let mut page = SettingsState::default();
        page.begin_record(Field::Yes);
        page.begin_record_talk();
        assert_eq!((page.recording, page.recording_talk), (None, true), "one thing is recorded at a time");
        page.begin_record(Field::Yes);
        assert_eq!((page.recording, page.recording_talk), (Some(Field::Yes), false));
        page.begin_record_talk();
        page.select(Section::Sounds);
        assert_eq!((page.recording, page.recording_talk, page.section), (None, false, Section::Sounds));
    }

    // ---- the hold-to-talk key: a key, or a mouse button ----

    fn talk(key: Key) -> TalkRecorded {
        talk_from_event(&KeyEvent { key, mods: Mods::default() })
    }

    fn modifier(modifier: rune::Modifier, pressed: bool) -> TalkRecorded {
        talk(Key::Modifier { modifier, pressed })
    }

    #[test]
    fn a_single_key_held_down_can_be_the_talk_key() {
        assert_eq!(modifier(rune::Modifier::ControlRight, true), TalkRecorded::Key("ctrl_r".into()));
        assert_eq!(modifier(rune::Modifier::ControlLeft, true), TalkRecorded::Key("ctrl_l".into()));
        assert_eq!(modifier(rune::Modifier::AltRight, true), TalkRecorded::Key("alt_r".into()));
        assert_eq!(modifier(rune::Modifier::ShiftRight, true), TalkRecorded::Key("shift_r".into()));
        assert_eq!(talk(Key::Function(9)), TalkRecorded::Key("f9".into()));
        assert_eq!(talk(Key::Function(24)), TalkRecorded::Key("f24".into()));
        for (key, name) in [(Key::Insert, "insert"), (Key::Pause, "pause"), (Key::ScrollLock, "scroll_lock"), (Key::PageUp, "page_up"), (Key::PrintScreen, "print_screen"), (Key::ContextMenu, "menu")] {
            assert_eq!(talk(key), TalkRecorded::Key(name.into()));
        }
    }

    #[test]
    fn every_key_the_recorder_gives_is_one_orbit_accepts() {
        for key in [Key::Function(1), Key::Function(12), Key::Insert, Key::Home, Key::End, Key::PageUp, Key::PageDown, Key::Pause, Key::ScrollLock, Key::PrintScreen, Key::ContextMenu] {
            let TalkRecorded::Key(name) = talk(key.clone()) else { panic!("{key:?}") };
            assert!(settings::valid_talk_key(&name), "{name}");
        }
        for m in [rune::Modifier::ControlRight, rune::Modifier::ControlLeft, rune::Modifier::AltRight, rune::Modifier::AltLeft, rune::Modifier::ShiftRight, rune::Modifier::ShiftLeft] {
            let TalkRecorded::Key(name) = modifier(m, true) else { panic!("{m:?}") };
            assert!(settings::valid_talk_key(&name), "{name}");
        }
        assert_eq!(talk(Key::Function(25)), TalkRecorded::Ignore, "no such function key");
    }

    #[test]
    fn a_key_that_types_something_is_refused_with_the_reason() {
        for key in [Key::Text("a".into()), Key::Command('k'), Key::Enter, Key::Backspace, Key::Tab, Key::Left, Key::Down] {
            let TalkRecorded::Refused(why) = talk(key.clone()) else { panic!("{key:?}") };
            assert!(why.contains("typed into every app") && why.contains("mouse button"), "{why}");
        }
        let TalkRecorded::Refused(why) = modifier(rune::Modifier::SuperLeft, true) else { panic!() };
        assert!(why.contains("Start menu"));
    }

    #[test]
    fn a_key_going_up_and_escape_do_not_record() {
        assert_eq!(modifier(rune::Modifier::ControlRight, false), TalkRecorded::Ignore);
        assert_eq!(modifier(rune::Modifier::Fn, true), TalkRecorded::Ignore);
        assert_eq!(talk(Key::Escape), TalkRecorded::Cancel);
    }

    #[test]
    fn the_five_mouse_buttons_are_mouse1_to_mouse5() {
        let named: Vec<_> = [PointerButton::Primary, PointerButton::Secondary, PointerButton::Middle, PointerButton::Back, PointerButton::Forward].into_iter().map(talk_from_button).collect();
        assert_eq!(named, [Some("mouse1"), Some("mouse2"), Some("mouse3"), Some("mouse4"), Some("mouse5")]);
        assert_eq!(talk_from_button(PointerButton::Other(7)), None);
        assert!(named.iter().flatten().all(|n| settings::valid_talk_key(n) && settings::is_mouse_talk(n)));
    }

    #[test]
    fn a_mouse_button_is_saved_as_the_talk_key_and_the_file_orbit_reads_has_it() {
        let (mut page, file) = page();
        page.begin_record_talk();
        assert!(page.recording_talk);
        assert!(page.set_talk(talk_from_button(PointerButton::Forward).unwrap()));
        assert_eq!((on_disk(&file).hotkeys.talk.as_str(), page.recording_talk), ("mouse5", false), "done once it took");
        assert!(page.notice.as_ref().unwrap().text.contains("Mouse5 (forward)"), "{:?}", page.notice);
        assert_eq!(on_disk(&file).commands.len(), 2, "the commands were not touched");
    }

    // ---- audio and voice ----

    #[test]
    fn the_speaking_volume_follows_the_slider_and_is_saved_where_it_is_let_go() {
        let (mut page, file) = page();
        assert_eq!(page.speaking(), 89, "as the example file has it");
        page.drag_speaking(10);
        page.drag_speaking(55);
        assert_eq!((page.speaking(), on_disk(&file).audio.volume), (55, 89), "shown at once, nothing written during the drag");
        assert!(page.finish_speaking(true));
        assert_eq!((on_disk(&file).audio.volume, page.speaking()), (55, 55));
        assert!(page.notice.as_ref().unwrap().text.contains("55%"));
        page.drag_speaking(5);
        assert!(!page.finish_speaking(false) && page.speaking() == 55, "a cancelled drag puts it back");
        page.drag_speaking(55);
        assert!(!page.finish_speaking(true), "letting go where it started is not a change");
        assert_eq!(on_disk(&file).sounds.volume, 65, "the orb's own sounds are a different volume");
    }

    #[test]
    fn the_speaking_volume_buttons_step_by_ten_and_stop_at_the_ends() {
        let (mut page, file) = page();
        assert!(page.step_speaking(1) && on_disk(&file).audio.volume == 99);
        assert!(page.step_speaking(1) && on_disk(&file).audio.volume == 100);
        assert!(!page.step_speaking(1));
        for _ in 0..12 {
            page.step_speaking(-1);
        }
        assert_eq!(on_disk(&file).audio.volume, 0);
        assert!(!page.step_speaking(-1));
    }

    #[test]
    fn a_microphone_and_a_speaker_are_picked_by_name_and_empty_means_the_windows_default() {
        let (mut page, file) = page();
        assert!(page.set_mic("Headset (Buds)"));
        assert!(page.set_speaker("Speakers (Echo Pop)"));
        let saved = on_disk(&file).audio;
        assert_eq!((saved.mic.as_str(), saved.speaker.as_str(), saved.volume), ("Headset (Buds)", "Speakers (Echo Pop)", 89));
        assert!(page.notice.as_ref().unwrap().text.contains("Speakers (Echo Pop)") && page.notice.as_ref().unwrap().text.contains("no restart"));
        assert!(!page.set_mic("Headset (Buds)"), "already that one");
        assert!(page.set_mic(""));
        assert_eq!(on_disk(&file).audio.mic, "");
        assert!(page.notice.as_ref().unwrap().text.contains("the Windows default"));
        assert!(!page.set_mic(&"x".repeat(settings::MAX_DEVICE + 1)) && !page.notice.as_ref().unwrap().good);
    }

    #[test]
    fn the_wake_word_and_the_follow_up_are_saved_at_once_and_alone() {
        let (mut page, file) = page();
        assert_eq!((page.settings.voice.wake_word, page.settings.voice.keep_listening), (true, false), "as the example file has it");
        assert!(page.set_wake_word(false) && page.set_keep_listening(true));
        let saved = on_disk(&file).voice;
        assert_eq!((saved.wake_word, saved.keep_listening), (false, true));
        assert!(page.notice.as_ref().unwrap().text.contains("ready for a moment"));
        assert_eq!((on_disk(&file).audio.volume, on_disk(&file).hotkeys.talk.as_str()), (89, "f9"));
    }

    #[test]
    fn resetting_the_audio_and_voice_puts_the_defaults_back_and_keeps_everything_else() {
        let (mut page, file) = page();
        assert!(page.reset_general());
        let saved = on_disk(&file);
        assert_eq!((saved.audio, saved.voice), (settings::Audio::default(), settings::VoiceOptions::default()));
        assert_eq!((saved.hotkeys.talk.as_str(), saved.sounds.volume, saved.commands.len()), ("f9", 65, 2));
    }

    #[test]
    fn leaving_the_general_tab_drops_a_slider_drag_in_progress() {
        let mut page = SettingsState::default();
        page.drag_speaking(5);
        page.select(Section::Sounds);
        assert_eq!(page.speaking(), 100);
    }

    #[test]
    fn an_audio_change_that_cannot_be_saved_changes_nothing_and_says_why() {
        let (mut page, file) = page();
        let before = page.settings.clone();
        page.path = Some(file.parent().unwrap().join("gone").join("settings.json"));
        assert!(!page.set_mic("USB") && !page.set_wake_word(false) && !page.step_speaking(1));
        assert_eq!(page.settings, before);
        assert!(page.notice.as_ref().unwrap().text.contains("could not write"));
    }
}

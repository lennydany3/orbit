//! The Settings page's state and the rules that move it: recording a hotkey, editing a command, saving. No drawing and no
//! windowing, so all of it is tested; `settings_view` only draws it.
//!
//! Every change is saved the moment it is made (a hotkey picked, a command saved, switched on or off, deleted), as one whole
//! file written in one go. If the save fails the change is not made, and the page says why.

use std::path::PathBuf;

use rune::{Key, KeyEvent, TextEdit};

use crate::settings::{self, Command, Hotkeys, MAX_COMMANDS, MAX_NAME, MAX_PHRASE, MAX_PHRASES, MAX_STEPS, Settings, Step};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Section {
    Hotkeys,
    Commands,
}

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
            StepKind::Open => "An app, a website or a folder. Folders only open; nothing is ever run.",
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
    pub notice: Option<Notice>,
    /// The command waiting for "are you sure" before it is deleted.
    pub confirm_delete: Option<usize>,
    /// Why Windows would not give Orbit the orb's chord, if it would not: it does not work until it is changed.
    pub orb_error: Option<String>,
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
            section: Section::Hotkeys,
            draft: None,
            recording: None,
            notice: None,
            confirm_delete: None,
            orb_error: None,
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
        self.notice = None;
        self.confirm_delete = None;
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
        self.notice = None;
    }

    pub fn cancel_record(&mut self) {
        self.recording = None;
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
        self.saved(done, &format!("Hold {} to talk. Orbit uses it straight away.", settings::talk_label(name)))
    }

    /// All four back to what they were out of the box.
    pub fn reset_hotkeys(&mut self) -> bool {
        let mut next = self.settings.clone();
        next.hotkeys = Hotkeys::default();
        let done = self.commit(next);
        self.recording = None;
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
        page.select(Section::Commands);
        assert_eq!((page.recording, page.section), (None, Section::Commands));
    }
}

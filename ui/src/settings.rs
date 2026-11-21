//! Orbit's settings file: the hotkeys and the user's own commands. The window writes it (the Settings page); Orbit (Python)
//! reads it and starts a command when its phrase is said. The two sides share the rules, checked against the same example
//! files in `tests/fixtures` so they cannot drift apart: what a key combination looks like, how a phrase is compared, what a
//! step may be.
//!
//! Reading is forgiving, as on the Python side: a missing file or broken JSON means the defaults, and a bad entry is left out
//! and listed in `problems`. Writing is careful: a temporary file, then a rename, so a crash never leaves half a file; and
//! settings this version does not know are written back as they were.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

pub const VERSION: i64 = 1;
pub const MAX_COMMANDS: usize = 50;
pub const MAX_PHRASES: usize = 8;
pub const MAX_STEPS: usize = 20;
pub const MAX_NAME: usize = 60;
pub const MAX_PHRASE: usize = 80;
pub const MAX_TARGET: usize = 300;
pub const MAX_TEXT: usize = 500;
pub const MAX_WAIT_S: f64 = 30.0;

pub const DEFAULT_ORB: &str = "ctrl+alt+o";
pub const DEFAULT_YES: &str = "ctrl+alt+y";
pub const DEFAULT_NO: &str = "ctrl+alt+n";
pub const DEFAULT_TALK: &str = "ctrl_r";
pub const DEFAULT_VOLUME: u8 = 40;
/// Out of the box the orb sits in the bottom right corner, at the middle size.
pub const DEFAULT_ORB_X: f32 = 1.0;
pub const DEFAULT_ORB_Y: f32 = 1.0;

/// The settings file: `ORBIT_SETTINGS`, or `settings.json` in the project folder (the same place Orbit looks).
pub fn path() -> PathBuf {
    std::env::var_os("ORBIT_SETTINGS").map(PathBuf::from).unwrap_or_else(|| crate::spawn::project_root().join("settings.json"))
}

// ---- keys -------------------------------------------------------------------------------------------------------------

const MODIFIERS: [&str; 4] = ["ctrl", "alt", "shift", "win"];
const ALIASES: &[(&str, &str)] = &[
    ("control", "ctrl"), ("windows", "win"), ("super", "win"), ("cmd", "win"), ("meta", "win"), ("option", "alt"), ("escape", "esc"),
    ("return", "enter"), ("del", "delete"), ("ins", "insert"), ("pgup", "pageup"), ("pgdn", "pagedown"), ("prtsc", "printscreen"),
    ("prtscn", "printscreen"), ("spacebar", "space"), ("bksp", "backspace"),
];
const NAMED_KEYS: &[&str] = &[
    "enter", "esc", "tab", "space", "backspace", "delete", "insert", "home", "end", "pageup", "pagedown", "up", "down", "left", "right",
    "printscreen", "minus", "equals", "comma", "period", "slash", "semicolon", "quote", "lbracket", "rbracket", "backslash", "backtick",
];

/// Is this a key name Orbit can press? A letter, a digit, F1 to F24, or one of the named keys.
pub fn key_known(name: &str) -> bool {
    let mut chars = name.chars();
    match (chars.next(), chars.next()) {
        (Some(c), None) => c.is_ascii_lowercase() || c.is_ascii_digit(),
        _ => {
            if let Some(n) = name.strip_prefix('f').and_then(|n| n.parse::<u32>().ok()) {
                return (1..=24).contains(&n) && name == format!("f{n}");
            }
            NAMED_KEYS.contains(&name)
        }
    }
}

/// "Win + Shift + S" -> "shift+win+s": the one way a key combination is written in the file. Errors say what is wrong.
pub fn parse_combo(text: &str) -> Result<String, String> {
    let parts: Vec<String> = text.split('+').map(|p| p.trim().to_lowercase()).collect();
    if text.is_empty() || parts.iter().any(String::is_empty) {
        return Err("write keys like ctrl+shift+s".into());
    }
    let names: Vec<&str> = parts.iter().map(|p| ALIASES.iter().find(|(a, _)| a == p).map_or(p.as_str(), |(_, to)| *to)).collect();
    let mods: Vec<&str> = names.iter().copied().filter(|n| MODIFIERS.contains(n)).collect();
    let keys: Vec<&str> = names.iter().copied().filter(|n| !MODIFIERS.contains(n)).collect();
    let mut seen = mods.clone();
    seen.sort_unstable();
    seen.dedup();
    if seen.len() != mods.len() {
        return Err("a modifier is repeated".into());
    }
    match keys.as_slice() {
        [] => Err("a combination needs exactly one key besides ctrl, alt, shift and win".into()),
        [key] if !key_known(key) => Err(format!("unknown key '{key}'")),
        [key] => Ok(MODIFIERS.iter().filter(|m| mods.contains(m)).chain(std::iter::once(key)).copied().collect::<Vec<_>>().join("+")),
        _ => Err("only one key besides the modifiers".into()),
    }
}

/// A chord for a global hotkey: a key combination Windows will hand to Orbit from any app, so it must include Ctrl, Alt or Win
/// (or be an F key), and be one Rune can register. Returns it as written in the file.
pub fn parse_hotkey(text: &str) -> Result<String, String> {
    let combo = parse_combo(text)?;
    let (mods, key): (Vec<&str>, &str) = {
        let parts: Vec<&str> = combo.split('+').collect();
        (parts[..parts.len() - 1].to_vec(), parts[parts.len() - 1])
    };
    let is_f_key = key.strip_prefix('f').is_some_and(|n| n.parse::<u32>().is_ok());
    if !is_f_key && !mods.iter().any(|m| matches!(*m, "ctrl" | "alt" | "win")) {
        return Err("a hotkey needs Ctrl, Alt or Win as well, or it would be typed into every app".into());
    }
    combo.parse::<rune::Shortcut>().map_err(|_| format!("Windows cannot use {} as a hotkey", pretty(&combo)))?;
    Ok(combo)
}

/// "ctrl+alt+o" -> "Ctrl + Alt + O".
pub fn pretty(combo: &str) -> String {
    combo
        .split('+')
        .map(|p| match p {
            "ctrl" => "Ctrl".to_owned(),
            "alt" => "Alt".to_owned(),
            "shift" => "Shift".to_owned(),
            "win" => "Win".to_owned(),
            "esc" => "Esc".to_owned(),
            "pageup" => "Page Up".to_owned(),
            "pagedown" => "Page Down".to_owned(),
            "printscreen" => "Print Screen".to_owned(),
            k if k.len() == 1 || (k.starts_with('f') && k[1..].chars().all(|c| c.is_ascii_digit())) => k.to_uppercase(),
            k => {
                let mut c = k.chars();
                c.next().map(|f| f.to_uppercase().chain(c).collect()).unwrap_or_default()
            }
        })
        .collect::<Vec<_>>()
        .join(" + ")
}

/// "ctrl+alt+y" -> "Ctrl+Alt+Y", for a line of small print.
pub fn compact(combo: &str) -> String {
    pretty(combo).replace(" + ", "+")
}

/// The hold-to-talk keys, by the name Orbit's keyboard listener uses, with what to call them.
pub const TALK_KEYS: &[(&str, &str)] = &[
    ("ctrl_r", "Right Ctrl"), ("ctrl_l", "Left Ctrl"), ("alt_r", "Right Alt"), ("alt_l", "Left Alt"), ("shift_r", "Right Shift"),
    ("caps_lock", "Caps Lock"), ("scroll_lock", "Scroll Lock"), ("pause", "Pause"), ("insert", "Insert"),
    ("f8", "F8"), ("f9", "F9"), ("f10", "F10"), ("f11", "F11"), ("f12", "F12"),
];

/// Every name the Python side accepts for the talk key (the page offers `TALK_KEYS`; a hand-written file may use any of these).
pub const TALK_NAMES: &[&str] = &[
    "ctrl_r", "ctrl_l", "alt_r", "alt_l", "alt_gr", "shift_r", "shift_l", "caps_lock", "scroll_lock", "num_lock", "pause", "insert",
    "home", "end", "page_up", "page_down", "menu", "print_screen", "tab", "space",
];

pub fn valid_talk_key(name: &str) -> bool {
    if name.is_empty() {
        return false;
    }
    let function = name.strip_prefix('f').and_then(|n| n.parse::<u32>().ok()).is_some_and(|n| (1..=24).contains(&n) && name == format!("f{n}"));
    function || TALK_NAMES.contains(&name) || (name.chars().count() == 1 && !name.trim().is_empty())
}

pub fn talk_label(name: &str) -> String {
    TALK_KEYS.iter().find(|(n, _)| *n == name).map(|(_, l)| (*l).to_owned()).unwrap_or_else(|| name.replace('_', " "))
}

// ---- phrases ----------------------------------------------------------------------------------------------------------

/// How a phrase is compared with what was said: lowercase, no punctuation, single spaces ("Work mode!" == "work  mode").
pub fn normalize_phrase(text: &str) -> String {
    let flat: String = text.to_lowercase().replace('\'', "").chars().map(|c| if c.is_alphanumeric() || c == '_' || c.is_whitespace() { c } else { ' ' }).collect();
    flat.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn clean(text: &str, limit: usize) -> String {
    if text.chars().count() > limit * 4 {
        return String::new();
    }
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

// ---- the model --------------------------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct Hotkeys {
    pub orb: String,
    pub yes: String,
    pub no: String,
    pub talk: String,
}

impl Default for Hotkeys {
    fn default() -> Self {
        Hotkeys { orb: DEFAULT_ORB.into(), yes: DEFAULT_YES.into(), no: DEFAULT_NO.into(), talk: DEFAULT_TALK.into() }
    }
}

/// The orb's sounds: on or off, and how loud (0 to 100, whole numbers). Quiet by default: Orbit is meant to be calm.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Sounds {
    pub enabled: bool,
    pub volume: u8,
}

impl Default for Sounds {
    fn default() -> Self {
        Sounds { enabled: true, volume: DEFAULT_VOLUME }
    }
}

impl Sounds {
    /// Would a sound be heard at all?
    pub fn audible(&self) -> bool {
        self.enabled && self.volume > 0
    }
}

/// A volume as the file may write it: a number from 0 to 100 (a fraction is rounded, halves up). Anything else is not one.
/// Python reads it the same way (`tests/fixtures/sounds.json`).
pub fn parse_volume(value: &Value) -> Option<u8> {
    let n = value.as_f64().filter(|n| n.is_finite() && (0.0..=100.0).contains(n))?;
    Some((n + 0.5).floor() as u8)
}

/// How big the floating orb is.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum OrbSize {
    Small,
    #[default]
    Medium,
    Large,
}

impl OrbSize {
    pub const ALL: [OrbSize; 3] = [OrbSize::Small, OrbSize::Medium, OrbSize::Large];

    /// How it is written in the file.
    pub fn name(self) -> &'static str {
        match self {
            OrbSize::Small => "small",
            OrbSize::Medium => "medium",
            OrbSize::Large => "large",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            OrbSize::Small => "Small",
            OrbSize::Medium => "Medium",
            OrbSize::Large => "Large",
        }
    }

    /// The orb's width in points.
    pub fn diameter(self) -> f32 {
        match self {
            OrbSize::Small => 80.0,
            OrbSize::Medium => 112.0,
            OrbSize::Large => 152.0,
        }
    }

    pub fn parse(name: &str) -> Option<OrbSize> {
        OrbSize::ALL.into_iter().find(|s| s.name() == name)
    }
}

/// Where the floating orb sits and how big it is. `x` and `y` run from 0 (left, top) to 1 (right, bottom) over the part of the
/// screen apps can use; see `layout` for how that becomes a place.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct OrbLook {
    pub x: f32,
    pub y: f32,
    pub size: OrbSize,
}

impl Default for OrbLook {
    fn default() -> Self {
        OrbLook { x: DEFAULT_ORB_X, y: DEFAULT_ORB_Y, size: OrbSize::Medium }
    }
}

/// A position as the number written in the file (0.667, not 0.6669999957...).
fn permille(v: f32) -> f64 {
    (f64::from(v) * 1000.0).round() / 1000.0
}

impl OrbLook {
    /// A position as the file keeps it: 0 to 1, to three decimals (so the file stays tidy and the page can compare).
    pub fn rounded(v: f32) -> f32 {
        ((v.clamp(0.0, 1.0) * 1000.0).round() / 1000.0).clamp(0.0, 1.0)
    }

    /// The same size at another spot, rounded as the file keeps it.
    pub fn at(self, x: f32, y: f32) -> OrbLook {
        OrbLook { x: OrbLook::rounded(x), y: OrbLook::rounded(y), ..self }
    }

    fn to_value(self) -> Value {
        json!({"x": permille(self.x), "y": permille(self.y), "size": self.size.name()})
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Step {
    /// An app, a website or a folder: which one is worked out when it runs.
    Open(String),
    Keys(String),
    Type(String),
    Wait(f64),
}

impl Step {
    pub fn kind(&self) -> &'static str {
        match self {
            Step::Open(_) => "open",
            Step::Keys(_) => "keys",
            Step::Type(_) => "type",
            Step::Wait(_) => "wait",
        }
    }

    /// What it does, in the words Orbit also uses when it asks before running a command.
    pub fn describe(&self) -> String {
        match self {
            Step::Open(t) => format!("open {t}"),
            Step::Keys(k) => format!("press {k}"),
            Step::Type(t) => format!("type \"{t}\""),
            Step::Wait(s) => format!("wait {s} seconds"),
        }
    }

    /// The step as it will be saved, or why it cannot be.
    pub fn checked(&self) -> Result<Step, String> {
        match self {
            Step::Open(t) => match clean(t, MAX_TARGET).as_str() {
                "" => Err("an open step has nothing to open".into()),
                t if t.chars().count() > MAX_TARGET => Err(format!("what to open is longer than {MAX_TARGET} characters")),
                t => Ok(Step::Open(t.to_owned())),
            },
            Step::Keys(k) => parse_combo(k).map(Step::Keys).map_err(|e| format!("keys '{k}': {e}")),
            Step::Type(t) if t.is_empty() => Err("a type step has no text".into()),
            Step::Type(t) if t.chars().count() > MAX_TEXT => Err(format!("typed text is longer than {MAX_TEXT} characters")),
            Step::Type(t) => Ok(Step::Type(t.clone())),
            Step::Wait(s) if *s > 0.0 && *s <= MAX_WAIT_S => Ok(Step::Wait(*s)),
            Step::Wait(_) => Err(format!("a wait must be more than 0 and at most {MAX_WAIT_S} seconds")),
        }
    }

    fn parse(raw: &Value) -> Result<Step, String> {
        let Some(obj) = raw.as_object() else { return Err("a step is not an object".into()) };
        let text = |key: &str| obj.get(key).and_then(Value::as_str);
        match obj.get("kind").and_then(Value::as_str) {
            Some("open") => Step::Open(text("target").unwrap_or_default().to_owned()).checked(),
            Some("keys") => match text("keys") {
                Some(k) => Step::Keys(k.to_owned()).checked(),
                None => Err("a keys step has no keys".into()),
            },
            Some("type") => Step::Type(text("text").unwrap_or_default().to_owned()).checked(),
            Some("wait") => match obj.get("seconds") {
                Some(Value::Number(n)) => Step::Wait(n.as_f64().unwrap_or(0.0)).checked(),
                _ => Err(format!("a wait must be more than 0 and at most {MAX_WAIT_S} seconds")),
            },
            other => Err(format!("unknown step '{}'", other.unwrap_or("None"))),
        }
    }

    fn to_value(&self) -> Value {
        match self {
            Step::Open(t) => json!({"kind": "open", "target": t}),
            Step::Keys(k) => json!({"kind": "keys", "keys": k}),
            Step::Type(t) => json!({"kind": "type", "text": t}),
            Step::Wait(s) => json!({"kind": "wait", "seconds": s}),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Command {
    pub id: String,
    pub name: String,
    pub enabled: bool,
    pub phrases: Vec<String>,
    pub steps: Vec<Step>,
    /// Anything else the file had on this command (written back untouched).
    extra: Map<String, Value>,
}

impl Command {
    pub fn new(id: String, name: String, phrases: Vec<String>, steps: Vec<Step>) -> Command {
        Command { id, name, enabled: true, phrases, steps, extra: Map::new() }
    }

    /// The same command with new contents: its id, whether it is on, and anything else the file had stay as they were.
    pub fn with_parts(&self, name: String, phrases: Vec<String>, steps: Vec<Step>) -> Command {
        Command { name, phrases, steps, ..self.clone() }
    }

    /// Does it press keys or type? Orbit asks for a yes before running those.
    pub fn sends_keys(&self) -> bool {
        self.steps.iter().any(|s| matches!(s, Step::Keys(_) | Step::Type(_)))
    }

    pub fn describe(&self) -> String {
        self.steps.iter().map(Step::describe).collect::<Vec<_>>().join(", then ")
    }

    fn to_value(&self) -> Value {
        let mut obj = self.extra.clone();
        obj.insert("id".into(), json!(self.id));
        obj.insert("name".into(), json!(self.name));
        obj.insert("enabled".into(), json!(self.enabled));
        obj.insert("phrases".into(), json!(self.phrases));
        obj.insert("steps".into(), Value::Array(self.steps.iter().map(Step::to_value).collect()));
        Value::Object(obj)
    }
}

/// The settings, as read.
#[derive(Clone, Debug, PartialEq, Default)]
pub struct Settings {
    pub hotkeys: Hotkeys,
    pub commands: Vec<Command>,
    pub sounds: Sounds,
    pub orb: OrbLook,
    /// Anything else the file had (written back untouched).
    extra: Map<String, Value>,
}

/// What reading the file gave.
#[derive(Clone, Debug, PartialEq, Default)]
pub struct Loaded {
    pub settings: Settings,
    /// What was left out, and why.
    pub problems: Vec<String>,
    /// Was there a file at all?
    pub existed: bool,
}

impl Settings {
    /// From decoded JSON. Anything wrong is left out and listed.
    pub fn parse(data: &Value) -> (Settings, Vec<String>) {
        let mut settings = Settings::default();
        let mut problems = Vec::new();
        let Some(root) = data.as_object() else {
            return (settings, vec!["the settings file is not a JSON object, so the defaults are used".into()]);
        };
        settings.extra = root.iter().filter(|(k, _)| !matches!(k.as_str(), "version" | "hotkeys" | "sounds" | "orb" | "commands")).map(|(k, v)| (k.clone(), v.clone())).collect();
        match root.get("sounds") {
            None => {}
            Some(Value::Object(sounds)) => {
                if let Some(value) = sounds.get("enabled") {
                    match value.as_bool() {
                        Some(on) => settings.sounds.enabled = on,
                        None => problems.push(format!("the sounds switch {value} is not true or false, so on is used")),
                    }
                }
                if let Some(value) = sounds.get("volume") {
                    match parse_volume(value) {
                        Some(v) => settings.sounds.volume = v,
                        None => problems.push(format!("the sound volume {value} is not a number from 0 to 100, so {DEFAULT_VOLUME} is used")),
                    }
                }
            }
            Some(_) => problems.push("the sounds setting is not an object, so the defaults are used".into()),
        }
        match root.get("orb") {
            None => {}
            Some(Value::Object(orb)) => {
                for (name, field) in [("x", &mut settings.orb.x), ("y", &mut settings.orb.y)] {
                    if let Some(value) = orb.get(name) {
                        match value.as_f64().filter(|n| n.is_finite() && (0.0..=1.0).contains(n)) {
                            Some(n) => *field = OrbLook::rounded(n as f32),
                            None => problems.push(format!("the orb's {name} position {value} is not a number from 0 to 1, so the corner is used")),
                        }
                    }
                }
                if let Some(value) = orb.get("size") {
                    match value.as_str().and_then(OrbSize::parse) {
                        Some(size) => settings.orb.size = size,
                        None => problems.push(format!("the orb size {value} is not small, medium or large, so medium is used")),
                    }
                }
            }
            Some(_) => problems.push("the orb setting is not an object, so the defaults are used".into()),
        }
        if let Some(keys) = root.get("hotkeys").and_then(Value::as_object) {
            let mut take = |field: &mut String, name: &str, default: &str, chord: bool| {
                let Some(value) = keys.get(name) else { return };
                let text = value.as_str().unwrap_or_default();
                let ok = if chord { parse_hotkey(text).ok() } else { valid_talk_key(text).then(|| text.to_owned()) };
                match ok {
                    Some(v) => *field = v,
                    None => problems.push(format!("the {name} key {value} is not one Orbit can use, so {default} is used")),
                }
            };
            take(&mut settings.hotkeys.orb, "orb", DEFAULT_ORB, true);
            take(&mut settings.hotkeys.yes, "yes", DEFAULT_YES, true);
            take(&mut settings.hotkeys.no, "no", DEFAULT_NO, true);
            take(&mut settings.hotkeys.talk, "talk", DEFAULT_TALK, false);
            let h = &mut settings.hotkeys;
            if h.orb == h.yes || h.orb == h.no || h.yes == h.no {
                problems.push("two hotkeys are the same, so the defaults are used".into());
                let talk = std::mem::take(&mut h.talk);
                *h = Hotkeys { talk, ..Hotkeys::default() };
            }
        }
        if let Some(list) = root.get("commands").and_then(Value::as_array) {
            let mut seen: BTreeMap<String, String> = BTreeMap::new();
            for raw in list.iter().take(MAX_COMMANDS) {
                match Command::parse(raw, &seen) {
                    Ok((command, notes)) => {
                        problems.extend(notes);
                        if command.enabled {
                            for p in &command.phrases {
                                seen.insert(p.clone(), command.name.clone());
                            }
                        }
                        settings.commands.push(command);
                    }
                    Err(notes) => problems.extend(notes),
                }
            }
            if list.len() > MAX_COMMANDS {
                problems.push(format!("only the first {MAX_COMMANDS} commands are used"));
            }
        }
        (settings, problems)
    }

    /// The JSON that is written: keys in a fixed order, two-space indent, a closing newline.
    pub fn to_json(&self) -> String {
        let mut root = self.extra.clone();
        root.insert("version".into(), json!(VERSION));
        root.insert("hotkeys".into(), json!({"orb": self.hotkeys.orb, "yes": self.hotkeys.yes, "no": self.hotkeys.no, "talk": self.hotkeys.talk}));
        root.insert("sounds".into(), json!({"enabled": self.sounds.enabled, "volume": self.sounds.volume}));
        if self.orb != OrbLook::default() {
            root.insert("orb".into(), self.orb.to_value());          // only when it was changed: the file Orbit reads stays as it was
        }
        root.insert("commands".into(), Value::Array(self.commands.iter().map(Command::to_value).collect()));
        let mut text = serde_json::to_string_pretty(&Value::Object(root)).unwrap_or_default();
        text.push('\n');
        text
    }

    /// Writes the file whole or not at all.
    pub fn save(&self, file: &Path) -> Result<(), String> {
        let temp = file.with_extension("json.tmp");
        std::fs::write(&temp, self.to_json()).map_err(|e| format!("could not write {}: {e}", temp.display()))?;
        std::fs::rename(&temp, file).map_err(|e| {
            let _ = std::fs::remove_file(&temp);
            format!("could not save {}: {e}", file.display())
        })
    }
}

impl Command {
    fn parse(raw: &Value, seen: &BTreeMap<String, String>) -> Result<(Command, Vec<String>), Vec<String>> {
        let Some(obj) = raw.as_object() else { return Err(vec!["a command is not an object".into()]) };
        let name: String = clean(obj.get("name").and_then(Value::as_str).unwrap_or_default(), MAX_NAME).chars().take(MAX_NAME).collect();
        if name.is_empty() {
            return Err(vec!["a command with no name was ignored".into()]);
        }
        let label = format!("'{name}'");
        let mut notes = Vec::new();
        let mut phrases: Vec<String> = Vec::new();
        for p in obj.get("phrases").and_then(Value::as_array).into_iter().flatten() {
            let phrase = match p.as_str() {
                Some(s) if s.chars().count() <= MAX_PHRASE * 2 => normalize_phrase(s),
                _ => String::new(),
            };
            if phrase.is_empty() || phrase.chars().count() > MAX_PHRASE {
                continue;
            }
            if let Some(owner) = seen.get(&phrase) {
                notes.push(format!("{label}: '{phrase}' already belongs to '{owner}', so it was skipped"));
            } else if !phrases.contains(&phrase) && phrases.len() < MAX_PHRASES {
                phrases.push(phrase);
            }
        }
        let mut steps = Vec::new();
        for raw_step in obj.get("steps").and_then(Value::as_array).into_iter().flatten() {
            match Step::parse(raw_step) {
                Ok(step) => steps.push(step),
                Err(why) => {
                    notes.push(format!("{label} was ignored: {why}"));
                    return Err(notes);
                }
            }
        }
        if steps.is_empty() {
            notes.push(format!("{label} was ignored: it has no steps"));
            return Err(notes);
        }
        if steps.len() > MAX_STEPS {
            notes.push(format!("{label} was ignored: more than {MAX_STEPS} steps"));
            return Err(notes);
        }
        if phrases.is_empty() {
            notes.push(format!("{label} was ignored: it has nothing to say to start it"));
            return Err(notes);
        }
        let id: String = {
            let given: String = clean(obj.get("id").and_then(Value::as_str).unwrap_or_default(), 40).chars().take(40).collect();
            if given.is_empty() { slug(&name) } else { given }
        };
        let enabled = obj.get("enabled").and_then(Value::as_bool).unwrap_or(true);
        let extra = obj.iter().filter(|(k, _)| !matches!(k.as_str(), "id" | "name" | "enabled" | "phrases" | "steps")).map(|(k, v)| (k.clone(), v.clone())).collect();
        Ok((Command { id, name, enabled, phrases, steps, extra }, notes))
    }
}

/// "Work mode!" -> "work-mode".
pub fn slug(name: &str) -> String {
    let flat: String = name.to_lowercase().chars().map(|c| if c.is_alphanumeric() { c } else { '-' }).collect();
    let slug = flat.split('-').filter(|p| !p.is_empty()).collect::<Vec<_>>().join("-");
    if slug.is_empty() { "command".into() } else { slug }
}

/// Reads the file. Never fails: see the module notes.
pub fn load(file: &Path) -> Loaded {
    let text = match std::fs::read_to_string(file) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Loaded::default(),
        Err(e) => return Loaded { problems: vec![format!("could not read {}: {e}", file.display())], existed: true, ..Loaded::default() },
    };
    match serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}')) {
        Ok(data) => {
            let (settings, problems) = Settings::parse(&data);
            Loaded { settings, problems, existed: true }
        }
        Err(e) => Loaded { problems: vec![format!("{} is not valid JSON ({e}), so the defaults are used", file.display())], existed: true, ..Loaded::default() },
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    const EXAMPLE: &str = include_str!("../../tests/fixtures/settings_example.json");
    const COMBOS: &str = include_str!("../../tests/fixtures/key_combos.json");
    const PHRASES: &str = include_str!("../../tests/fixtures/phrases.json");
    const TALK: &str = include_str!("../../tests/fixtures/talk_keys.json");
    const SOUNDS: &str = include_str!("../../tests/fixtures/sounds.json");

    fn parsed(json: &str) -> (Settings, Vec<String>) {
        Settings::parse(&serde_json::from_str(json).unwrap())
    }

    fn command(extra: &str) -> String {
        format!(r#"{{"commands":[{{"name":"Work","phrases":["work mode"],"steps":[{{"kind":"open","target":"Spotify"}}{extra}]}}]}}"#)
    }

    // ---- the files the Python side also reads ----

    #[test]
    fn key_combinations_are_read_the_way_orbit_reads_them() {
        for case in serde_json::from_str::<Vec<Value>>(COMBOS).unwrap() {
            let input = case["in"].as_str().unwrap();
            match case.get("out") {
                Some(out) => assert_eq!(parse_combo(input).as_deref(), Ok(out.as_str().unwrap()), "{input:?}"),
                None => assert!(parse_combo(input).is_err(), "{input:?} should not be a key combination"),
            }
        }
    }

    #[test]
    fn phrases_are_compared_the_way_orbit_compares_them() {
        for pair in serde_json::from_str::<Vec<Vec<String>>>(PHRASES).unwrap() {
            assert_eq!(normalize_phrase(&pair[0]), pair[1], "{:?}", pair[0]);
        }
    }

    #[test]
    fn the_talk_keys_are_the_ones_orbit_accepts() {
        let keys: Value = serde_json::from_str(TALK).unwrap();
        for k in keys["valid"].as_array().unwrap() {
            assert!(valid_talk_key(k.as_str().unwrap()), "{k}");
        }
        for k in keys["invalid"].as_array().unwrap() {
            assert!(!valid_talk_key(k.as_str().unwrap()), "{k}");
        }
        for (name, label) in TALK_KEYS {
            assert!(valid_talk_key(name) && !label.is_empty(), "the page offers {name}, which Orbit would refuse");
        }
    }

    #[test]
    fn the_example_file_reads_as_written_and_writes_back_byte_for_byte() {
        let (s, problems) = parsed(EXAMPLE);
        assert!(problems.is_empty(), "{problems:?}");
        assert_eq!(s.hotkeys, Hotkeys { orb: "ctrl+alt+o".into(), yes: "ctrl+alt+y".into(), no: "ctrl+alt+n".into(), talk: "f9".into() });
        assert_eq!(s.commands.len(), 2);
        let work = &s.commands[0];
        assert_eq!((work.id.as_str(), work.name.as_str(), work.enabled), ("work-mode", "Work mode", true));
        assert_eq!(work.phrases, ["work mode", "start working"]);
        assert_eq!(
            work.steps,
            [Step::Open("Spotify".into()), Step::Open("https://github.com".into()), Step::Wait(1.5), Step::Keys("ctrl+shift+s".into())]
        );
        assert!(!s.commands[1].enabled);
        assert_eq!(s.sounds, Sounds { enabled: false, volume: 65 });
        assert_eq!(s.to_json(), EXAMPLE, "the window writes exactly the file the example is");
    }

    #[test]
    fn the_sound_volume_is_read_the_way_orbit_reads_it() {
        let cases: Value = serde_json::from_str(SOUNDS).unwrap();
        for case in cases["volume"].as_array().unwrap() {
            let given = &case["in"];
            match case.get("out") {
                Some(out) => assert_eq!(parse_volume(given), Some(out.as_u64().unwrap() as u8), "{given}"),
                None => assert_eq!(parse_volume(given), None, "{given} is not a volume"),
            }
            let (s, problems) = parsed(&json!({"sounds": {"volume": given}}).to_string());
            assert_eq!((s.sounds.volume, problems.len()), (case.get("out").map_or(DEFAULT_VOLUME, |o| o.as_u64().unwrap() as u8), usize::from(case.get("out").is_none())), "{given}");
        }
        assert_eq!(cases["default_volume"], DEFAULT_VOLUME);
    }

    #[test]
    fn the_sounds_switch_is_read_the_way_orbit_reads_it() {
        let cases: Value = serde_json::from_str(SOUNDS).unwrap();
        for case in cases["enabled"].as_array().unwrap() {
            let (s, problems) = parsed(&json!({"sounds": {"enabled": case["in"]}}).to_string());
            assert_eq!((s.sounds.enabled, problems.len()), (case.get("out").map_or(true, |o| o.as_bool().unwrap()), usize::from(case.get("out").is_none())), "{}", case["in"]);
        }
    }

    #[test]
    fn no_sounds_entry_means_a_quiet_default_that_is_on() {
        let (s, problems) = parsed("{}");
        assert_eq!(s.sounds, Sounds { enabled: true, volume: 40 });
        assert!(problems.is_empty() && s.sounds.audible());
        assert!(!Sounds { enabled: true, volume: 0 }.audible() && !Sounds { enabled: false, volume: 90 }.audible());
        let (s, problems) = parsed(r#"{"sounds":{"volume":70}}"#);
        assert_eq!((s.sounds, problems.len()), (Sounds { enabled: true, volume: 70 }, 0), "each half stands alone");
    }

    #[test]
    fn a_sounds_entry_that_is_not_an_object_is_ignored_with_a_reason() {
        for raw in ["true", "5", "\"loud\"", "[]", "null"] {
            let (s, problems) = parsed(&format!(r#"{{"sounds":{raw}}}"#));
            assert_eq!(s.sounds, Sounds::default(), "{raw}");
            assert!(problems[0].contains("not an object"), "{raw}: {problems:?}");
        }
    }

    #[test]
    fn the_sounds_are_written_back_with_the_rest_and_nothing_else_is_lost() {
        let (s, _) = parsed(r#"{"sounds":{"enabled":false,"volume":12.5},"theme":"dark"}"#);
        let back: Value = serde_json::from_str(&s.to_json()).unwrap();
        assert_eq!(back["sounds"], json!({"enabled": false, "volume": 13}));
        assert_eq!(back["theme"], "dark");
    }

    // ---- the orb ----

    #[test]
    fn no_orb_entry_means_the_corner_at_the_middle_size_and_the_default_writes_nothing() {
        let (s, problems) = parsed("{}");
        assert_eq!(s.orb, OrbLook { x: 1.0, y: 1.0, size: OrbSize::Medium });
        assert!(problems.is_empty());
        let back: Value = serde_json::from_str(&s.to_json()).unwrap();
        assert!(back.get("orb").is_none(), "an untouched orb leaves the file as it was");
    }

    #[test]
    fn the_orbs_place_and_size_are_read_and_written_back() {
        let (s, problems) = parsed(r#"{"orb":{"x":0.25,"y":0.6666,"size":"large"}}"#);
        assert!(problems.is_empty(), "{problems:?}");
        assert_eq!(s.orb, OrbLook { x: 0.25, y: 0.667, size: OrbSize::Large }, "kept to three decimals");
        let back: Value = serde_json::from_str(&s.to_json()).unwrap();
        assert_eq!(back["orb"], json!({"x": 0.25, "y": 0.667, "size": "large"}));
        assert_eq!(parsed(&s.to_json()).0, s, "what is written reads back the same");
    }

    #[test]
    fn each_part_of_the_orb_stands_alone_and_a_bad_one_keeps_its_default() {
        let (s, problems) = parsed(r#"{"orb":{"x":0.3}}"#);
        assert_eq!((s.orb, problems.len()), (OrbLook { x: 0.3, y: 1.0, size: OrbSize::Medium }, 0));
        for bad in ["-0.1", "1.5", "\"left\"", "null", "true"] {
            let (s, problems) = parsed(&format!(r#"{{"orb":{{"x":{bad},"y":0.5,"size":"huge"}}}}"#));
            assert_eq!(s.orb, OrbLook { x: 1.0, y: 0.5, size: OrbSize::Medium }, "{bad}");
            assert_eq!(problems.len(), 2, "{bad}: {problems:?}");
        }
        for raw in ["true", "5", "\"corner\"", "[]", "null"] {
            let (s, problems) = parsed(&format!(r#"{{"orb":{raw}}}"#));
            assert_eq!(s.orb, OrbLook::default(), "{raw}");
            assert!(problems[0].contains("not an object"), "{raw}: {problems:?}");
        }
    }

    #[test]
    fn the_orb_is_written_with_the_rest_and_nothing_else_is_lost() {
        let (mut s, _) = parsed(r#"{"theme":"dark","sounds":{"enabled":false,"volume":10}}"#);
        s.orb = OrbLook::default().at(0.5, 0.25);
        let back: Value = serde_json::from_str(&s.to_json()).unwrap();
        assert_eq!((back["theme"].as_str(), back["orb"]["x"].as_f64(), back["sounds"]["volume"].as_u64()), (Some("dark"), Some(0.5), Some(10)));
        assert_eq!(back.as_object().unwrap().keys().filter(|k| *k == "orb").count(), 1);
    }

    #[test]
    fn the_sizes_are_in_order_and_have_names_the_file_can_use() {
        let widths: Vec<f32> = OrbSize::ALL.iter().map(|s| s.diameter()).collect();
        assert!(widths.windows(2).all(|w| w[0] < w[1]) && OrbSize::default().diameter() == 112.0);
        for size in OrbSize::ALL {
            assert_eq!(OrbSize::parse(size.name()), Some(size));
        }
        assert_eq!(OrbSize::parse("Medium"), None);
    }

    // ---- keys ----

    #[test]
    fn a_hotkey_needs_ctrl_alt_or_win_unless_it_is_an_f_key() {
        assert_eq!(parse_hotkey("Ctrl+Alt+P").as_deref(), Ok("ctrl+alt+p"));
        assert_eq!(parse_hotkey("win+shift+o").as_deref(), Ok("shift+win+o"));
        assert_eq!(parse_hotkey("f9").as_deref(), Ok("f9"));
        assert!(parse_hotkey("shift+o").unwrap_err().contains("Ctrl, Alt or Win"));
        assert!(parse_hotkey("o").is_err());
        assert!(parse_hotkey("ctrl+banana").unwrap_err().contains("unknown key"));
    }

    #[test]
    fn a_key_windows_cannot_register_is_refused_with_its_name() {
        // Every named key works in a command; the few Rune cannot register as a hotkey say so instead of failing later.
        for key in NAMED_KEYS {
            match parse_hotkey(&format!("ctrl+alt+{key}")) {
                Ok(c) => assert_eq!(c, format!("ctrl+alt+{key}")),
                Err(why) => assert!(why.contains("cannot use"), "{key}: {why}"),
            }
        }
    }

    #[test]
    fn chords_read_as_people_write_them() {
        assert_eq!(pretty("ctrl+alt+o"), "Ctrl + Alt + O");
        assert_eq!(pretty("shift+win+pagedown"), "Shift + Win + Page Down");
        assert_eq!(pretty("f9"), "F9");
        assert_eq!(pretty("ctrl+esc"), "Ctrl + Esc");
        assert_eq!(talk_label("ctrl_r"), "Right Ctrl");
        assert_eq!(talk_label("page_up"), "page up");
    }

    // ---- reading ----

    #[test]
    fn nothing_there_means_the_defaults() {
        let dir = tempdir("none");
        let loaded = load(&dir.join("missing.json"));
        assert_eq!(loaded, Loaded::default());
        assert_eq!(loaded.settings.hotkeys.orb, "ctrl+alt+o");
        assert!(!loaded.existed && loaded.settings.commands.is_empty());
    }

    #[test]
    fn broken_json_means_the_defaults_and_says_so() {
        let dir = tempdir("broken");
        std::fs::write(dir.join("s.json"), "{not json").unwrap();
        let loaded = load(&dir.join("s.json"));
        assert!(loaded.existed && loaded.problems[0].contains("not valid JSON") && loaded.settings.commands.is_empty());
    }

    #[test]
    fn a_byte_order_mark_is_ignored() {
        let dir = tempdir("bom");
        std::fs::write(dir.join("s.json"), "\u{feff}{\"hotkeys\":{\"talk\":\"f8\"}}").unwrap();
        assert_eq!(load(&dir.join("s.json")).settings.hotkeys.talk, "f8");
    }

    #[test]
    fn a_bad_hotkey_keeps_its_default() {
        let (s, problems) = parsed(r#"{"hotkeys":{"orb":"shift+o","yes":5,"no":"ctrl+alt+m","talk":"banana"}}"#);
        let h = &s.hotkeys;
        assert_eq!((h.orb.as_str(), h.yes.as_str(), h.no.as_str(), h.talk.as_str()), ("ctrl+alt+o", "ctrl+alt+y", "ctrl+alt+m", "ctrl_r"));
        assert_eq!(problems.len(), 3, "{problems:?}");
    }

    #[test]
    fn two_hotkeys_that_are_the_same_fall_back_to_the_defaults_but_keep_the_talk_key() {
        let (s, problems) = parsed(r#"{"hotkeys":{"orb":"ctrl+alt+y","yes":"Ctrl+Alt+Y","talk":"f9"}}"#);
        assert_eq!(s.hotkeys, Hotkeys { talk: "f9".into(), ..Hotkeys::default() });
        assert!(problems.iter().any(|p| p.contains("two hotkeys")), "{problems:?}");
    }

    #[test]
    fn something_that_is_not_an_object_is_ignored() {
        for data in ["[]", "\"x\"", "3", "null"] {
            assert!(parsed(data).1[0].contains("not a JSON object"), "{data}");
        }
    }

    #[test]
    fn a_command_is_phrases_plus_steps_and_phrases_are_made_comparable() {
        let (s, problems) = parsed(r#"{"commands":[{"name":" Work ","phrases":["  Work   MODE!  ","Let's work","work mode"],"steps":[{"kind":"open","target":"Spotify"}]}]}"#);
        assert!(problems.is_empty());
        let c = &s.commands[0];
        assert_eq!((c.phrases.clone(), c.id.as_str(), c.enabled, c.name.as_str()), (vec!["work mode".to_owned(), "lets work".to_owned()], "work", true, "Work"));
    }

    #[test]
    fn a_command_missing_a_name_steps_or_phrases_is_dropped_with_a_reason() {
        let good = r#"{"name":"A","phrases":["a"],"steps":[{"kind":"open","target":"x"}]}"#;
        let bad = [
            r#"{"phrases":["a"],"steps":[{"kind":"open","target":"x"}]}"#,
            r#"{"name":"A","phrases":["a"],"steps":[]}"#,
            r#"{"name":"A","phrases":[],"steps":[{"kind":"open","target":"x"}]}"#,
            r#"{"name":"A","phrases":["!!!"],"steps":[{"kind":"open","target":"x"}]}"#,
            "\"x\"",
            "4",
        ];
        for b in bad {
            let (s, problems) = parsed(&format!(r#"{{"commands":[{b},{good}]}}"#));
            assert_eq!(s.commands.len(), 1, "{b}");
            assert_eq!(problems.len(), 1, "{b}: {problems:?}");
        }
    }

    #[test]
    fn one_bad_step_drops_the_whole_command_rather_than_running_half() {
        let long = format!(r#"{{"kind":"type","text":"{}"}}"#, "x".repeat(MAX_TEXT + 1));
        let steps = [
            r#"{"kind":"open"}"#,
            r#"{"kind":"open","target":"  "}"#,
            r#"{"kind":"keys","keys":"ctrl+banana"}"#,
            r#"{"kind":"keys"}"#,
            r#"{"kind":"type","text":""}"#,
            r#"{"kind":"wait","seconds":0}"#,
            r#"{"kind":"wait","seconds":31}"#,
            r#"{"kind":"wait","seconds":"2"}"#,
            r#"{"kind":"run","program":"calc.exe"}"#,
            "\"open Spotify\"",
            long.as_str(),
        ];
        for step in steps {
            let (s, problems) = parsed(&command(&format!(",{step}")));
            assert!(s.commands.is_empty(), "{step}");
            assert!(problems[0].contains("was ignored") || problems[0].contains("not an object"), "{step}: {problems:?}");
        }
    }

    #[test]
    fn a_keys_step_is_stored_in_its_one_canonical_form() {
        let (s, _) = parsed(&command("").replace(r#"{"kind":"open","target":"Spotify"}"#, r#"{"kind":"keys","keys":"Win+Shift+S"}"#));
        assert_eq!(s.commands[0].steps, [Step::Keys("shift+win+s".into())]);
    }

    #[test]
    fn two_commands_cannot_share_a_phrase_and_the_first_keeps_it() {
        let a = r#"{"name":"A","phrases":["go"],"steps":[{"kind":"open","target":"x"}]}"#;
        let b = r#"{"name":"B","phrases":["go","other"],"steps":[{"kind":"open","target":"x"}]}"#;
        let (s, problems) = parsed(&format!(r#"{{"commands":[{a},{b}]}}"#));
        assert_eq!(s.commands[1].phrases, ["other"]);
        assert!(problems[0].contains("'go' already belongs to 'A'"));
        let off = a.replace(r#""name":"A","#, r#""name":"A","enabled":false,"#);
        let (s, problems) = parsed(&format!(r#"{{"commands":[{off},{b}]}}"#));
        assert_eq!((s.commands[1].phrases.len(), problems.len()), (2, 0), "a switched-off command lets go of its phrase");
    }

    #[test]
    fn limits_keep_the_file_small() {
        let one = |i: usize| format!(r#"{{"name":"C{i}","phrases":["p{i}"],"steps":[{{"kind":"open","target":"x"}}]}}"#);
        let many: Vec<String> = (0..MAX_COMMANDS + 5).map(one).collect();
        let (s, problems) = parsed(&format!(r#"{{"commands":[{}]}}"#, many.join(",")));
        assert_eq!(s.commands.len(), MAX_COMMANDS);
        assert!(problems.last().unwrap().contains("only the first"));
        let phrases: Vec<String> = (0..20).map(|i| format!("\"phrase {i}\"")).collect();
        let (s, _) = parsed(&format!(r#"{{"commands":[{{"name":"A","phrases":[{}],"steps":[{{"kind":"open","target":"x"}}]}}]}}"#, phrases.join(",")));
        assert_eq!(s.commands[0].phrases.len(), MAX_PHRASES);
        let waits = vec![r#"{"kind":"wait","seconds":1}"#; MAX_STEPS + 1].join(",");
        assert!(parsed(&format!(r#"{{"commands":[{{"name":"A","phrases":["a"],"steps":[{waits}]}}]}}"#)).0.commands.is_empty());
    }

    // ---- writing ----

    #[test]
    fn what_this_version_does_not_know_is_written_back_untouched() {
        let (s, _) = parsed(r#"{"version":1,"theme":{"accent":"blue"},"commands":[{"name":"A","phrases":["a"],"steps":[{"kind":"open","target":"x"}],"note":"mine","enabled":false}]}"#);
        let back: Value = serde_json::from_str(&s.to_json()).unwrap();
        assert_eq!(back["theme"], json!({"accent": "blue"}));
        assert_eq!(back["commands"][0]["note"], "mine");
        assert_eq!(back["commands"][0]["enabled"], false);
    }

    #[test]
    fn saving_writes_the_whole_file_and_leaves_no_temporary_one_behind() {
        let dir = tempdir("save");
        let file = dir.join("settings.json");
        let (s, _) = parsed(EXAMPLE);
        s.save(&file).unwrap();
        assert_eq!(std::fs::read_to_string(&file).unwrap(), EXAMPLE);
        let mut changed = s.clone();
        changed.hotkeys.talk = "f8".into();
        changed.save(&file).expect("saving over an existing file replaces it");
        assert_eq!(load(&file).settings.hotkeys.talk, "f8");
        let leftovers: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(leftovers, ["settings.json"], "no .tmp file");
    }

    #[test]
    fn a_save_into_a_folder_that_is_not_there_says_so() {
        let dir = tempdir("nodir");
        let err = Settings::default().save(&dir.join("missing").join("settings.json")).unwrap_err();
        assert!(err.contains("could not write"), "{err}");
    }

    #[test]
    fn a_command_says_what_it_will_do_in_plain_words() {
        let (s, _) = parsed(EXAMPLE);
        assert_eq!(s.commands[0].describe(), "open Spotify, then open https://github.com, then wait 1.5 seconds, then press ctrl+shift+s");
        assert_eq!(s.commands[1].describe(), "type \"Hello there!\"");
        assert!(s.commands[0].sends_keys());
    }

    #[test]
    fn a_name_becomes_an_id() {
        assert_eq!(slug("Work mode!"), "work-mode");
        assert_eq!(slug("!!!"), "command");
        assert_eq!(slug("Caf\u{e9}  No 5"), "caf\u{e9}-no-5");
    }

    /// A fresh empty folder for a test.
    pub(crate) fn tempdir(tag: &str) -> PathBuf {
        use std::hash::{BuildHasher, Hasher};
        let unique = std::collections::hash_map::RandomState::new().build_hasher().finish();
        let dir = std::env::temp_dir().join(format!("orbit-ui-{tag}-{}-{unique:x}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }
}

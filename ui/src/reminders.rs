//! Reminders: the list on the Reminders page, and the form that makes or changes one. Orbit (Python) is the one that fires them (it says
//! them aloud and tells the window); the window adds, edits, switches off and deletes. The file is `reminders.json` next to the
//! settings (see `userdata`, and `orbit/reminders.py` for the other side, which follows the same rules):
//!
//! ```json
//! {"version": 1,
//!  "reminders": [{"id": "r-3f9a1c2b", "title": "Stretch", "due": "2026-11-23T17:00", "repeat": "none", "enabled": true, "created": "..."}],
//!  "history": [{"id": "r-3f9a1c2b", "title": "Stretch", "at": "2026-11-22T17:00"}],
//!  "seen": "2026-11-22T09:00"}
//! ```
//!
//! No drawing and no windowing, so all of it is tested; `reminders_view` only draws it.

use std::path::PathBuf;

use rune::TextEdit;
use serde_json::{Map, Value, json};

use crate::datetime::{MONTHS, Stamp, WEEKDAYS};
use crate::userdata;

pub const MAX_REMINDERS: usize = 200;
pub const MAX_TITLE: usize = 120;
pub const MAX_HISTORY: usize = 30;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Repeat {
    #[default]
    Once,
    Daily,
    Weekdays,
    Weekly,
    Monthly,
}

impl Repeat {
    pub const ALL: [Repeat; 5] = [Repeat::Once, Repeat::Daily, Repeat::Weekdays, Repeat::Weekly, Repeat::Monthly];

    /// How it is written in the file.
    pub fn name(self) -> &'static str {
        match self {
            Repeat::Once => "none",
            Repeat::Daily => "daily",
            Repeat::Weekdays => "weekdays",
            Repeat::Weekly => "weekly",
            Repeat::Monthly => "monthly",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Repeat::Once => "Once",
            Repeat::Daily => "Every day",
            Repeat::Weekdays => "Weekdays",
            Repeat::Weekly => "Every week",
            Repeat::Monthly => "Every month",
        }
    }

    pub fn parse(name: &str) -> Option<Repeat> {
        Repeat::ALL.into_iter().find(|r| r.name() == name)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Reminder {
    pub id: String,
    pub title: String,
    pub due: Stamp,
    pub repeat: Repeat,
    pub enabled: bool,
    pub created: Option<Stamp>,
    /// Anything else the file had on it (written back untouched).
    extra: Map<String, Value>,
}

const KNOWN: [&str; 6] = ["id", "title", "due", "repeat", "enabled", "created"];

impl Reminder {
    pub fn new(title: String, due: Stamp, repeat: Repeat, now: Stamp) -> Reminder {
        Reminder { id: userdata::new_id("r"), title, due, repeat, enabled: true, created: Some(Stamp { second: 0, ..now }), extra: Map::new() }
    }

    fn parse(raw: &Value) -> Option<Reminder> {
        let obj = raw.as_object()?;
        let title = userdata::clean(userdata::text(obj, "title")?, MAX_TITLE);
        let due = Stamp::parse(userdata::text(obj, "due")?)?;
        if title.is_empty() {
            return None;
        }
        let id = userdata::clean(userdata::text(obj, "id").unwrap_or_default(), 40);
        Some(Reminder {
            id: if id.is_empty() { userdata::new_id("r") } else { id },
            title,
            due,
            repeat: userdata::text(obj, "repeat").and_then(Repeat::parse).unwrap_or_default(),
            enabled: obj.get("enabled").and_then(Value::as_bool).unwrap_or(true),
            created: userdata::text(obj, "created").and_then(Stamp::parse),
            extra: userdata::rest(obj, &KNOWN),
        })
    }

    fn to_value(&self) -> Value {
        let mut obj = self.extra.clone();
        obj.insert("id".into(), json!(self.id));
        obj.insert("title".into(), json!(self.title));
        obj.insert("due".into(), json!(self.due.to_text()));
        obj.insert("repeat".into(), json!(self.repeat.name()));
        obj.insert("enabled".into(), json!(self.enabled));
        if let Some(created) = self.created {
            obj.insert("created".into(), json!(created.to_text()));
        }
        Value::Object(obj)
    }
}

/// One time a reminder went off.
#[derive(Clone, Debug, PartialEq)]
pub struct Fired {
    pub id: String,
    pub title: String,
    pub at: Stamp,
}

/// What `reminders.json` holds.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Reminders {
    pub items: Vec<Reminder>,
    pub history: Vec<Fired>,
    pub seen: Option<Stamp>,
    rest: Map<String, Value>,
}

impl Reminders {
    pub fn from_map(data: &Map<String, Value>) -> Reminders {
        let items = data.get("reminders").and_then(Value::as_array).map(|a| a.iter().take(MAX_REMINDERS).filter_map(Reminder::parse).collect()).unwrap_or_default();
        let history = data
            .get("history")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .take(MAX_HISTORY)
                    .filter_map(|h| {
                        let h = h.as_object()?;
                        Some(Fired { id: userdata::text(h, "id")?.to_owned(), title: userdata::clean(userdata::text(h, "title")?, MAX_TITLE), at: Stamp::parse(userdata::text(h, "at")?)? })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Reminders { items, history, seen: userdata::text(data, "seen").and_then(Stamp::parse), rest: userdata::rest(data, &["version", "reminders", "history", "seen"]) }
    }

    pub fn to_map(&self) -> Map<String, Value> {
        let mut data = self.rest.clone();
        data.insert("reminders".into(), Value::Array(self.items.iter().map(Reminder::to_value).collect()));
        data.insert(
            "history".into(),
            Value::Array(self.history.iter().take(MAX_HISTORY).map(|h| json!({"id": h.id, "title": h.title, "at": h.at.to_text()})).collect()),
        );
        if let Some(seen) = self.seen {
            data.insert("seen".into(), json!(seen.to_text()));
        }
        data
    }

    /// How many reminders have gone off since the notifications were last looked at.
    pub fn unread(&self) -> usize {
        self.history.iter().filter(|h| self.seen.is_none_or(|seen| h.at > seen)).count()
    }

    /// The ones to show: the ones that are on, soonest first, then the ones that are off.
    pub fn ordered(&self) -> Vec<&Reminder> {
        let mut list: Vec<&Reminder> = self.items.iter().collect();
        list.sort_by_key(|r| (!r.enabled, r.due));
        list
    }
}

// ---- times: what to call them, what the next one is ----------------------------------------------------------------------------

/// 5:00 PM
pub fn clock_text(at: &Stamp) -> String {
    let hour = if at.hour.is_multiple_of(12) { 12 } else { at.hour % 12 };
    format!("{hour}:{:02} {}", at.minute, if at.hour < 12 { "AM" } else { "PM" })
}

/// "Today", "Tomorrow", "Thursday", "25 Dec" or "25 Dec 2027": a day, as it is said.
pub fn day_text(day: &Stamp, now: &Stamp) -> String {
    match day.day_number() - now.day_number() {
        0 => "Today".to_owned(),
        1 => "Tomorrow".to_owned(),
        2..=6 => WEEKDAYS[day.weekday() as usize].to_owned(),
        _ if day.year == now.year => format!("{} {}", day.day, MONTHS[day.month as usize - 1]),
        _ => format!("{} {} {}", day.day, MONTHS[day.month as usize - 1], day.year),
    }
}

/// What the list says about when a reminder goes off: "Today \u{b7} 5:00 PM", "Every day \u{b7} 8:00 AM", "Every Monday \u{b7} 10:00 AM".
pub fn when_text(reminder: &Reminder, now: &Stamp) -> String {
    let at = clock_text(&reminder.due);
    match reminder.repeat {
        Repeat::Once => format!("{} \u{b7} {at}", day_text(&reminder.due, now)),
        Repeat::Daily => format!("Every day \u{b7} {at}"),
        Repeat::Weekdays => format!("Every weekday \u{b7} {at}"),
        Repeat::Weekly => format!("Every {} \u{b7} {at}", WEEKDAYS[reminder.due.weekday() as usize]),
        Repeat::Monthly => format!("Monthly on the {} \u{b7} {at}", ordinal(reminder.due.day)),
    }
}

fn ordinal(n: u32) -> String {
    let suffix = if (11..=13).contains(&(n % 100)) { "th" } else { ["th", "st", "nd", "rd"].get((n % 10) as usize).copied().unwrap_or("th") };
    format!("{n}{suffix}")
}

/// The next time a repeating reminder is due that is later than `after` (a one-off has none: it comes back as it was).
/// The same rules as `orbit/reminders.py`.
pub fn advance(due: Stamp, repeat: Repeat, after: Stamp) -> Stamp {
    match repeat {
        Repeat::Once => due,
        Repeat::Monthly => (1..).map(|n| due.add_months(n)).find(|t| *t > after).unwrap_or(due),
        Repeat::Daily | Repeat::Weekdays | Repeat::Weekly => {
            let step = if repeat == Repeat::Weekly { 7 } else { 1 };
            let mut next = due;
            loop {
                next = next.add_days(step);
                if repeat == Repeat::Weekdays && next.weekday() >= 5 {
                    continue;
                }
                if next > after {
                    return next;
                }
            }
        }
    }
}

// ---- reading what is typed in the form -------------------------------------------------------------------------------------

/// A day, as typed: today, tomorrow, a weekday (the next one, today included), 2026-11-23, 23 Nov, Nov 23, 23 Nov 2027.
pub fn parse_day(text: &str, now: &Stamp) -> Result<Stamp, String> {
    let said = text.trim().to_lowercase();
    let words: Vec<&str> = said.split_whitespace().collect();
    let wrong = || "Write a day like Today, Tomorrow, Friday, 25 Dec or 2026-12-25.".to_owned();
    let day = |offset: i64| now.add_days(offset).at(0, 0);
    match words.as_slice() {
        [] => Err("Pick a day.".to_owned()),
        ["today"] => Ok(day(0)),
        ["tomorrow"] => Ok(day(1)),
        [name] | ["next", name] | ["on", name] if weekday_named(name).is_some() => {
            let target = weekday_named(name).unwrap_or(0);
            let ahead = (i64::from(target) - i64::from(now.weekday())).rem_euclid(7);
            Ok(day(if words[0] == "next" && ahead == 0 { 7 } else { ahead }))
        }
        [one] if one.contains('-') => {
            let parts: Vec<&str> = one.split('-').collect();
            let [y, m, d] = parts.as_slice() else { return Err(wrong()) };
            let number = |s: &str| s.parse::<u32>().ok();
            Stamp::new(y.parse().map_err(|_| wrong())?, number(m).ok_or_else(wrong)?, number(d).ok_or_else(wrong)?, 0, 0, 0).ok_or_else(|| "That day is not on the calendar.".to_owned())
        }
        [a, b] | [a, b, _] => {
            let (number, month) = day_and_month(a, b).or_else(|| day_and_month(b, a)).ok_or_else(wrong)?;
            let year = match words.get(2) {
                Some(y) => y.parse::<i32>().map_err(|_| wrong())?,
                None => now.year,
            };
            let found = Stamp::new(year, month, number, 0, 0, 0).ok_or_else(|| "That day is not on the calendar.".to_owned())?;
            Ok(if words.len() == 2 && found < now.at(0, 0) { Stamp::new(year + 1, month, number, 0, 0, 0).unwrap_or(found) } else { found })
        }
        _ => Err(wrong()),
    }
}

/// "25th" and "dec" (in either order): a day of the month and a month.
fn day_and_month(day: &str, month: &str) -> Option<(u32, u32)> {
    let number = day.trim_end_matches(|c: char| c.is_ascii_alphabetic()).parse::<u32>().ok()?;
    Some((number, month_named(month)?))
}

fn weekday_named(word: &str) -> Option<u32> {
    let word = word.trim_end_matches(['.', ',']);
    (word.len() >= 3).then_some(())?;
    WEEKDAYS.iter().position(|d| d.to_lowercase().starts_with(word) && (word.len() >= 3)).map(|i| i as u32)
}

fn month_named(word: &str) -> Option<u32> {
    let word = word.trim_end_matches(['.', ',']);
    (word.len() >= 3).then_some(())?;
    const LONG: [&str; 12] = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
    LONG.iter().position(|m| m.starts_with(word)).map(|i| i as u32 + 1)
}

/// A time of day, as typed: 5pm, 5:30 pm, 17:30, 9, noon, midnight. With no am or pm it is the 24-hour clock (9 is nine in the morning).
pub fn parse_time(text: &str) -> Result<(u32, u32), String> {
    let wrong = || "Write a time like 5:30 PM or 17:30.".to_owned();
    let said = text.trim().to_lowercase().replace('.', ":");
    match said.as_str() {
        "" => return Err("Pick a time.".to_owned()),
        "noon" => return Ok((12, 0)),
        "midnight" => return Ok((0, 0)),
        _ => {}
    }
    let (digits, meridiem) = match said.strip_suffix("pm").map(|d| (d, Some(true))).or_else(|| said.strip_suffix("am").map(|d| (d, Some(false)))) {
        Some((d, m)) => (d.trim().trim_end_matches(':'), m),
        None => (said.as_str(), None),
    };
    let (h, m) = match digits.split_once(':') {
        Some((h, m)) if m.len() == 2 => (h, m),
        Some(_) => return Err(wrong()),
        None if digits.len() <= 2 => (digits, "00"),
        None => return Err(wrong()),
    };
    let (h, m): (u32, u32) = (h.parse().map_err(|_| wrong())?, m.parse().map_err(|_| wrong())?);
    if m > 59 {
        return Err(wrong());
    }
    match meridiem {
        Some(pm) if (1..=12).contains(&h) => Ok((h % 12 + if pm { 12 } else { 0 }, m)),
        Some(_) => Err(wrong()),
        None if h < 24 => Ok((h, m)),
        None => Err(wrong()),
    }
}

// ---- the form -------------------------------------------------------------------------------------------------------------

/// Quick ways to fill in the time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Quick {
    InMinutes(i64),
    ThisEvening,
    TomorrowMorning,
}

impl Quick {
    pub const ALL: [Quick; 4] = [Quick::InMinutes(10), Quick::InMinutes(60), Quick::ThisEvening, Quick::TomorrowMorning];

    pub fn label(self) -> String {
        match self {
            Quick::InMinutes(60) => "In 1 hour".to_owned(),
            Quick::InMinutes(m) => format!("In {m} min"),
            Quick::ThisEvening => "This evening".to_owned(),
            Quick::TomorrowMorning => "Tomorrow morning".to_owned(),
        }
    }

    /// The moment it means.
    pub fn moment(self, now: &Stamp) -> Stamp {
        match self {
            Quick::InMinutes(m) => Stamp { second: 0, ..now.add_minutes(m) },
            Quick::ThisEvening => {
                let six = now.at(18, 0);
                if six > *now { six } else { now.add_days(1).at(18, 0) }
            }
            Quick::TomorrowMorning => now.add_days(1).at(9, 0),
        }
    }
}

/// A reminder being made or changed, before it is saved.
#[derive(Clone)]
pub struct Draft {
    /// Which reminder this changes (by id); `None` for a new one.
    pub id: Option<String>,
    pub title: TextEdit,
    pub day: TextEdit,
    pub time: TextEdit,
    pub repeat: Repeat,
    /// Why the last Save did not go through.
    pub errors: Vec<String>,
    base: Option<Reminder>,
}

impl Draft {
    /// A new one: today, an hour from now, on the hour.
    pub fn blank(now: &Stamp) -> Draft {
        let when = now.add_minutes(60).at(now.add_minutes(60).hour, 0);
        Draft::of(None, String::new(), when, Repeat::Once, now, None)
    }

    fn of(id: Option<String>, title: String, when: Stamp, repeat: Repeat, now: &Stamp, base: Option<Reminder>) -> Draft {
        Draft { id, title: TextEdit::new(&title), day: TextEdit::new(day_text(&when, now)), time: TextEdit::new(clock_text(&when)), repeat, errors: Vec::new(), base }
    }

    pub fn editing(reminder: &Reminder, now: &Stamp) -> Draft {
        Draft::of(Some(reminder.id.clone()), reminder.title.clone(), reminder.due, reminder.repeat, now, Some(reminder.clone()))
    }

    pub fn apply(&mut self, quick: Quick, now: &Stamp) {
        let when = quick.moment(now);
        self.day.replace_text(day_text(&when, now));
        self.time.replace_text(clock_text(&when));
        self.errors.clear();
    }

    /// The finished reminder, or everything that is wrong with it, in words.
    pub fn build(&self, now: &Stamp) -> Result<Reminder, Vec<String>> {
        let mut errors = Vec::new();
        let title = userdata::clean(&self.title.text, MAX_TITLE * 4);
        if title.is_empty() {
            errors.push("Say what Orbit should remind you about.".to_owned());
        } else if title.chars().count() > MAX_TITLE {
            errors.push(format!("The reminder is longer than {MAX_TITLE} characters."));
        }
        let day = parse_day(&self.day.text, now);
        let time = parse_time(&self.time.text);
        if let Err(why) = &day {
            errors.push(why.clone());
        }
        if let Err(why) = &time {
            errors.push(why.clone());
        }
        let (Ok(day), Ok((h, m))) = (day, time) else { return Err(errors) };
        let mut due = day.at(h, m);
        if due <= *now && self.repeat != Repeat::Once {
            due = advance(due, self.repeat, *now);
        } else if due <= *now && errors.is_empty() {
            errors.push("That time has already passed. Pick one that is still ahead.".to_owned());
        }
        if !errors.is_empty() {
            return Err(errors);
        }
        Ok(match &self.base {
            Some(base) => Reminder { title, due, repeat: self.repeat, ..base.clone() },
            None => Reminder::new(title, due, self.repeat, *now),
        })
    }
}

// ---- the page's state -----------------------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct Notice {
    pub text: String,
    pub good: bool,
}

/// Everything the Reminders page shows and edits.
#[derive(Default)]
pub struct RemindersState {
    /// Where it is saved. `None` saves nothing (headless scenes, tests).
    pub path: Option<PathBuf>,
    pub data: Reminders,
    pub draft: Option<Draft>,
    pub notice: Option<Notice>,
    /// The reminder waiting for "are you sure" before it is deleted.
    pub confirm_delete: Option<String>,
    stamp: Option<(u128, u64)>,
}

impl RemindersState {
    /// Reminders from JSON text, saved nowhere (for the headless scenes and tests).
    pub fn from_json(json: &str) -> RemindersState {
        let data = serde_json::from_str::<Value>(json).ok().and_then(|v| v.as_object().map(Reminders::from_map)).unwrap_or_default();
        RemindersState { data, ..Default::default() }
    }

    /// The reminders in `path`, which is also where changes are saved.
    pub fn open(path: PathBuf) -> RemindersState {
        let data = Reminders::from_map(&userdata::read(&path));
        RemindersState { stamp: userdata::stamp(&path), data, path: Some(path), ..Default::default() }
    }

    /// Reads the file again if the other side changed it. True if it did.
    pub fn reload_if_changed(&mut self) -> bool {
        let Some(path) = &self.path else { return false };
        let now = userdata::stamp(path);
        if now == self.stamp {
            return false;
        }
        self.stamp = now;
        self.data = Reminders::from_map(&userdata::read(path));
        true
    }

    fn good(&mut self, text: impl Into<String>) {
        self.notice = Some(Notice { text: text.into(), good: true });
    }

    fn bad(&mut self, text: impl Into<String>) {
        self.notice = Some(Notice { text: text.into(), good: false });
    }

    /// Reads the file fresh, lets `change` alter it, and writes it whole. The change is not made if it cannot be saved.
    fn change<T>(&mut self, change: impl FnOnce(&mut Reminders) -> Result<T, String>) -> Result<T, String> {
        let mut next = match &self.path {
            Some(path) => Reminders::from_map(&userdata::read(path)),
            None => self.data.clone(),
        };
        let done = change(&mut next)?;
        if let Some(path) = &self.path {
            userdata::write(path, next.to_map())?;
            self.stamp = userdata::stamp(path);
        }
        self.data = next;
        Ok(done)
    }

    pub fn new_draft(&mut self, now: &Stamp) {
        if self.data.items.len() >= MAX_REMINDERS {
            return self.bad(format!("That is as many reminders as Orbit keeps ({MAX_REMINDERS}). Delete one first."));
        }
        self.notice = None;
        self.draft = Some(Draft::blank(now));
    }

    pub fn edit(&mut self, id: &str, now: &Stamp) {
        if let Some(reminder) = self.data.items.iter().find(|r| r.id == id) {
            self.notice = None;
            self.draft = Some(Draft::editing(reminder, now));
        }
    }

    pub fn cancel_draft(&mut self) {
        self.draft = None;
    }

    pub fn title_edit(&mut self) -> &mut TextEdit {
        &mut self.draft_or_scratch().title
    }

    pub fn day_edit(&mut self) -> &mut TextEdit {
        &mut self.draft_or_scratch().day
    }

    pub fn time_edit(&mut self) -> &mut TextEdit {
        &mut self.draft_or_scratch().time
    }

    /// A late keystroke for a form that has gone has somewhere harmless to go.
    fn draft_or_scratch(&mut self) -> &mut Draft {
        self.draft.get_or_insert_with(|| Draft::blank(&Stamp::now()))
    }

    pub fn set_repeat(&mut self, repeat: Repeat) {
        if let Some(d) = &mut self.draft {
            d.repeat = repeat;
        }
    }

    pub fn quick(&mut self, quick: Quick, now: &Stamp) {
        if let Some(d) = &mut self.draft {
            d.apply(quick, now);
        }
    }

    /// Saves the form. False (and the reasons on the form) if it is not complete.
    pub fn save_draft(&mut self, now: &Stamp) -> bool {
        let Some(draft) = &self.draft else { return false };
        let built = match draft.build(now) {
            Ok(r) => r,
            Err(errors) => {
                if let Some(d) = &mut self.draft {
                    d.errors = errors;
                }
                return false;
            }
        };
        let when = when_text(&built, now);
        let title = built.title.clone();
        let editing = draft.id.clone();
        let saved = self.change(|data| {
            match editing.as_deref().and_then(|id| data.items.iter().position(|r| r.id == id)) {
                Some(i) => data.items[i] = built,
                None if data.items.len() >= MAX_REMINDERS => return Err(format!("That is as many reminders as Orbit keeps ({MAX_REMINDERS}).")),
                None => data.items.push(built),
            }
            Ok(())
        });
        match saved {
            Ok(()) => {
                self.draft = None;
                self.good(format!("\"{title}\" is saved: {when}. Orbit tells you when it is time."));
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

    /// Switches a reminder on or off. (One that is switched on again with its time already past moves to its next time, or says so.)
    pub fn toggle(&mut self, id: &str, now: &Stamp) -> bool {
        let id = id.to_owned();
        let done = self.change(|data| {
            let r = data.items.iter_mut().find(|r| r.id == id).ok_or("That reminder is gone.")?;
            r.enabled = !r.enabled;
            if r.enabled && r.due <= *now {
                if r.repeat == Repeat::Once {
                    r.enabled = false;
                    return Err(format!("\"{}\" was for a time that has passed. Edit it to pick a new time.", r.title));
                }
                r.due = advance(r.due, r.repeat, *now);
            }
            Ok((r.title.clone(), r.enabled))
        });
        match done {
            Ok((title, on)) => {
                self.good(format!("\"{title}\" is {}.", if on { "on" } else { "off" }));
                true
            }
            Err(why) => {
                self.bad(why);
                false
            }
        }
    }

    pub fn ask_delete(&mut self, id: &str) {
        self.confirm_delete = self.data.items.iter().any(|r| r.id == id).then(|| id.to_owned());
    }

    pub fn delete(&mut self, id: &str) -> bool {
        self.confirm_delete = None;
        let id = id.to_owned();
        match self.change(|data| {
            let at = data.items.iter().position(|r| r.id == id).ok_or("That reminder is gone.")?;
            Ok(data.items.remove(at).title)
        }) {
            Ok(title) => {
                self.good(format!("\"{title}\" is deleted."));
                true
            }
            Err(why) => {
                self.bad(why);
                false
            }
        }
    }

    /// The notifications have been looked at: nothing is unread until the next reminder goes off.
    pub fn mark_seen(&mut self, now: &Stamp) -> bool {
        if self.data.unread() == 0 {
            return false;
        }
        let now = Stamp { second: 0, ..*now };
        self.change(|data| {
            // (never earlier than the newest one that went off, so a PC clock that was set back cannot leave it unread)
            data.seen = Some(data.history.iter().map(|h| h.at).fold(now, Stamp::max));
            Ok(())
        })
        .is_ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::tests::tempdir;

    const EXAMPLE: &str = include_str!("../../tests/fixtures/reminders_example.json");

    fn at(text: &str) -> Stamp {
        Stamp::parse(text).unwrap()
    }

    const NOW: &str = "2026-11-22T14:30"; // a Sunday afternoon

    fn page() -> (RemindersState, PathBuf) {
        let dir = tempdir("reminders");
        let file = dir.join("reminders.json");
        std::fs::write(&file, EXAMPLE).unwrap();
        (RemindersState::open(file.clone()), file)
    }

    fn on_disk(file: &std::path::Path) -> Reminders {
        Reminders::from_map(&userdata::read(file))
    }

    fn fill(page: &mut RemindersState, title: &str, day: &str, time: &str, repeat: Repeat) {
        let d = page.draft.as_mut().unwrap();
        d.title.replace_text(title);
        d.day.replace_text(day);
        d.time.replace_text(time);
        d.repeat = repeat;
    }

    // ---- the file (the example is read by the Python side too) ----

    #[test]
    fn the_example_file_is_read_as_written_and_written_back_the_same() {
        let data: Value = serde_json::from_str(EXAMPLE).unwrap();
        let r = Reminders::from_map(data.as_object().unwrap());
        assert_eq!(r.items.len(), 3);
        let first = &r.items[0];
        assert_eq!((first.id.as_str(), first.title.as_str(), first.due.to_text(), first.repeat, first.enabled), ("r-3f9a1c2b", "Call mom", "2026-11-22T17:00".to_owned(), Repeat::Once, true));
        assert_eq!((r.items[1].repeat, r.items[2].enabled), (Repeat::Daily, false));
        assert_eq!((r.history.len(), r.history[0].title.as_str(), r.seen), (1, "Stretch", Some(at("2026-11-22T07:00"))));
        let mut back = r.to_map();
        back.insert("version".into(), json!(1));
        assert_eq!(Value::Object(back), data, "written back with nothing lost, the unknown fields included");
    }

    #[test]
    fn bad_entries_are_left_out_and_the_rest_kept() {
        let v = json!({"reminders": [{"title": "no time"}, {"due": "2026-11-23T09:00"}, "x", 4, {"id": "r-1", "title": "Fine", "due": "2026-11-23T09:00", "repeat": "yearly"},
                                     {"title": "Bad time", "due": "tomorrow"}], "history": [5, {"id": "a"}]});
        let r = Reminders::from_map(v.as_object().unwrap());
        assert_eq!(r.items.iter().map(|r| (r.title.as_str(), r.repeat)).collect::<Vec<_>>(), [("Fine", Repeat::Once)], "an unknown repeat is a one-off");
        assert!(r.history.is_empty() && r.seen.is_none());
    }

    #[test]
    fn the_unread_count_is_what_went_off_since_the_notifications_were_last_looked_at() {
        let (mut page, file) = page();
        assert_eq!(page.data.unread(), 1, "'Stretch' fired at 08:00, after the notifications were last looked at (07:00)");
        assert!(page.mark_seen(&at("2026-11-22T18:00")));
        assert_eq!((page.data.unread(), on_disk(&file).seen), (0, Some(at("2026-11-22T18:00"))));
        assert!(!page.mark_seen(&at("2026-11-22T19:00")), "nothing new to mark");
        let none_seen = Reminders { seen: None, ..page.data.clone() };
        assert_eq!(none_seen.unread(), 1, "never looked: everything that fired is new");
    }

    #[test]
    fn the_list_has_the_soonest_first_and_the_ones_that_are_off_last() {
        let (page, _) = page();
        let titles: Vec<&str> = page.data.ordered().iter().map(|r| r.title.as_str()).collect();
        assert_eq!(titles, ["Call mom", "Take medicine", "Water the plants"]);
    }

    // ---- times ----

    #[test]
    fn times_are_said_the_way_the_other_side_says_them() {
        let now = at(NOW);
        assert_eq!(clock_text(&at("2026-11-22T17:00")), "5:00 PM");
        assert_eq!(clock_text(&at("2026-11-22T00:05")), "12:05 AM");
        assert_eq!(clock_text(&at("2026-11-22T12:00")), "12:00 PM");
        assert_eq!(day_text(&at("2026-11-22T09:00"), &now), "Today");
        assert_eq!(day_text(&at("2026-11-23T09:00"), &now), "Tomorrow");
        assert_eq!(day_text(&at("2026-11-26T09:00"), &now), "Thursday");
        assert_eq!(day_text(&at("2026-12-25T09:00"), &now), "25 Dec");
        assert_eq!(day_text(&at("2027-01-05T09:00"), &now), "5 Jan 2027");
        let r = |due: &str, repeat| Reminder::new("x".into(), at(due), repeat, now);
        assert_eq!(when_text(&r("2026-11-22T17:00", Repeat::Once), &now), "Today \u{b7} 5:00 PM");
        assert_eq!(when_text(&r("2026-11-23T08:00", Repeat::Daily), &now), "Every day \u{b7} 8:00 AM");
        assert_eq!(when_text(&r("2026-11-23T10:00", Repeat::Weekly), &now), "Every Monday \u{b7} 10:00 AM");
        assert_eq!(when_text(&r("2026-12-01T09:00", Repeat::Monthly), &now), "Monthly on the 1st \u{b7} 9:00 AM");
        assert_eq!(when_text(&r("2026-12-22T09:00", Repeat::Monthly), &now), "Monthly on the 22nd \u{b7} 9:00 AM");
        assert_eq!(ordinal(11), "11th");
        assert_eq!(ordinal(23), "23rd");
    }

    #[test]
    fn a_repeating_reminder_moves_to_the_next_time_after_now_like_the_other_side_does() {
        let now = at(NOW);
        let due = at("2026-11-20T08:00"); // a Friday
        assert_eq!(advance(due, Repeat::Daily, now).to_text(), "2026-11-23T08:00");
        assert_eq!(advance(due, Repeat::Weekdays, now).to_text(), "2026-11-23T08:00", "Saturday and Sunday are skipped");
        assert_eq!(advance(due, Repeat::Weekly, now).to_text(), "2026-11-27T08:00");
        assert_eq!(advance(at("2026-01-31T09:00"), Repeat::Monthly, at("2026-01-31T10:00")).to_text(), "2026-02-28T09:00");
        assert_eq!(advance(due, Repeat::Once, now), due);
    }

    #[test]
    fn a_day_is_read_as_it_is_typed() {
        let now = at(NOW);
        let day = |t: &str| parse_day(t, &now).map(|d| d.to_text());
        assert_eq!(day("today"), Ok("2026-11-22T00:00".into()));
        assert_eq!(day("  Tomorrow "), Ok("2026-11-23T00:00".into()));
        assert_eq!(day("friday"), Ok("2026-11-27T00:00".into()));
        assert_eq!(day("sunday"), Ok("2026-11-22T00:00".into()), "today counts");
        assert_eq!(day("next sunday"), Ok("2026-11-29T00:00".into()));
        assert_eq!(day("mon"), Ok("2026-11-23T00:00".into()));
        assert_eq!(day("2026-12-25"), Ok("2026-12-25T00:00".into()));
        assert_eq!(day("25 Dec"), Ok("2026-12-25T00:00".into()));
        assert_eq!(day("dec 25"), Ok("2026-12-25T00:00".into()));
        assert_eq!(day("5 January"), Ok("2027-01-05T00:00".into()), "a day already gone this year is next year's");
        assert_eq!(day("5 jan 2030"), Ok("2030-01-05T00:00".into()));
        assert!(day("").unwrap_err().contains("Pick a day"));
        for bad in ["someday", "2026-13-01", "2026-02-30", "31 Nov", "banana 4", "1/2/2026", "tomorrow morning"] {
            assert!(day(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn a_time_is_read_as_it_is_typed() {
        for (text, want) in [("5pm", (17, 0)), ("5 PM", (17, 0)), ("5:30pm", (17, 30)), ("5.30 pm", (17, 30)), ("12am", (0, 0)), ("12:15 am", (0, 15)), ("12pm", (12, 0)),
                             ("17:30", (17, 30)), ("9", (9, 0)), ("09:05", (9, 5)), ("0:00", (0, 0)), ("23:59", (23, 59)), ("noon", (12, 0)), ("Midnight", (0, 0))] {
            assert_eq!(parse_time(text), Ok(want), "{text}");
        }
        for bad in ["", "25:00", "5:60", "13pm", "0pm", "soon", "5:3", "123", "5:30:00", "pm"] {
            assert!(parse_time(bad).is_err(), "{bad:?}");
        }
        assert!(parse_time("").unwrap_err().contains("Pick a time"));
    }

    // ---- the form ----

    #[test]
    fn a_new_form_starts_an_hour_from_now_on_the_hour() {
        let d = Draft::blank(&at(NOW));
        assert_eq!((d.day.text.as_str(), d.time.text.as_str(), d.repeat), ("Today", "3:00 PM", Repeat::Once));
        let late = Draft::blank(&at("2026-11-22T23:40"));
        assert_eq!((late.day.text.as_str(), late.time.text.as_str()), ("Tomorrow", "12:00 AM"));
    }

    #[test]
    fn a_reminder_made_on_the_form_is_in_the_file_orbit_reads() {
        let (mut page, file) = page();
        let now = at(NOW);
        page.new_draft(&now);
        fill(&mut page, "  Buy   milk ", "tomorrow", "9am", Repeat::Weekly);
        assert!(page.save_draft(&now));
        assert!(page.draft.is_none() && page.notice.as_ref().unwrap().good);
        let saved = on_disk(&file);
        let made = saved.items.last().unwrap();
        assert_eq!((made.title.as_str(), made.due.to_text(), made.repeat, made.enabled), ("Buy milk", "2026-11-23T09:00".to_owned(), Repeat::Weekly, true));
        assert!(made.id.starts_with("r-") && made.created == Some(at(NOW)));
        assert_eq!(saved.items.len(), 4, "and the others were not touched");
        assert!(page.notice.as_ref().unwrap().text.contains("Buy milk") && page.notice.as_ref().unwrap().text.contains("Every Monday \u{b7} 9:00 AM"), "{:?}", page.notice);
    }

    #[test]
    fn an_incomplete_form_is_not_saved_and_every_problem_is_listed() {
        let (mut page, file) = page();
        let now = at(NOW);
        page.new_draft(&now);
        fill(&mut page, " ", "someday", "soon", Repeat::Once);
        assert!(!page.save_draft(&now));
        let errors = page.draft.as_ref().unwrap().errors.clone();
        assert_eq!(errors.len(), 3, "{errors:?}");
        assert!(errors[0].contains("what Orbit should remind") && errors[1].contains("Write a day") && errors[2].contains("Write a time"));
        assert_eq!(on_disk(&file).items.len(), 3, "nothing was written");
    }

    #[test]
    fn a_time_that_has_passed_is_refused_for_a_one_off_and_moved_on_for_a_repeating_one() {
        let (mut page, file) = page();
        let now = at(NOW);
        page.new_draft(&now);
        fill(&mut page, "Too late", "today", "9am", Repeat::Once);
        assert!(!page.save_draft(&now) && page.draft.as_ref().unwrap().errors[0].contains("already passed"));
        fill(&mut page, "Every morning", "today", "9am", Repeat::Daily);
        assert!(page.save_draft(&now));
        assert_eq!(on_disk(&file).items.last().unwrap().due.to_text(), "2026-11-23T09:00");
    }

    #[test]
    fn editing_keeps_the_id_whether_it_is_on_and_what_else_the_file_had() {
        let (mut page, file) = page();
        let now = at(NOW);
        page.edit("r-3f9a1c2b", &now);
        let d = page.draft.as_ref().unwrap();
        assert_eq!((d.title.text.as_str(), d.day.text.as_str(), d.time.text.as_str()), ("Call mom", "Today", "5:00 PM"));
        fill(&mut page, "Call dad", "tomorrow", "6pm", Repeat::Once);
        assert!(page.save_draft(&now));
        let saved = on_disk(&file);
        assert_eq!(saved.items.len(), 3);
        let changed = saved.items.iter().find(|r| r.id == "r-3f9a1c2b").unwrap();
        assert_eq!((changed.title.as_str(), changed.due.to_text()), ("Call dad", "2026-11-23T18:00".to_owned()));
        assert_eq!(userdata::read(&file)["reminders"][0]["note"], "mine", "a field this version does not know is kept");
    }

    #[test]
    fn the_quick_buttons_fill_in_the_day_and_the_time() {
        let now = at(NOW);
        let mut d = Draft::blank(&now);
        d.apply(Quick::InMinutes(10), &now);
        assert_eq!((d.day.text.as_str(), d.time.text.as_str()), ("Today", "2:40 PM"));
        d.apply(Quick::ThisEvening, &now);
        assert_eq!((d.day.text.as_str(), d.time.text.as_str()), ("Today", "6:00 PM"));
        d.apply(Quick::ThisEvening, &at("2026-11-22T19:00"));
        assert_eq!(d.day.text, "Tomorrow");
        d.apply(Quick::TomorrowMorning, &now);
        assert_eq!((d.day.text.as_str(), d.time.text.as_str()), ("Tomorrow", "9:00 AM"));
        d.apply(Quick::InMinutes(60), &at("2026-11-22T23:30"));
        assert_eq!((d.day.text.as_str(), d.time.text.as_str()), ("Tomorrow", "12:30 AM"));
        assert!(Quick::ALL.iter().all(|q| !q.label().is_empty()));
    }

    // ---- the list ----

    #[test]
    fn a_reminder_can_be_switched_on_and_off_and_a_stale_one_cannot_be_switched_on() {
        let (mut page, file) = page();
        let now = at(NOW);
        assert!(page.toggle("r-3f9a1c2b", &now));
        assert!(!on_disk(&file).items.iter().find(|r| r.id == "r-3f9a1c2b").unwrap().enabled);
        assert!(page.notice.as_ref().unwrap().text.contains("is off"));
        assert!(!page.toggle("r-3f9a1c2b", &at("2026-11-22T18:00")), "its time passed while it was off");
        assert!(page.notice.as_ref().unwrap().text.contains("time that has passed") && !page.notice.as_ref().unwrap().good);
        assert!(page.toggle("r-0d0d0d0d", &now), "a repeating one moves on to its next time instead");
        assert!(!page.toggle("nope", &now));
    }

    #[test]
    fn deleting_asks_first_and_then_removes_only_that_reminder() {
        let (mut page, file) = page();
        page.ask_delete("r-3f9a1c2b");
        assert_eq!(page.confirm_delete.as_deref(), Some("r-3f9a1c2b"));
        assert_eq!(on_disk(&file).items.len(), 3, "asking changes nothing");
        assert!(page.delete("r-3f9a1c2b"));
        assert_eq!((page.confirm_delete.clone(), on_disk(&file).items.len()), (None, 2));
        page.ask_delete("nope");
        assert_eq!(page.confirm_delete, None);
        assert!(!page.delete("nope") && !page.notice.as_ref().unwrap().good);
    }

    #[test]
    fn what_orbit_did_to_the_file_is_not_lost_by_the_windows_next_save() {
        let (mut page, file) = page();
        let mut theirs = userdata::read(&file);
        theirs.get_mut("reminders").unwrap().as_array_mut().unwrap().push(json!({"id": "r-from-voice", "title": "Said aloud", "due": "2026-11-23T10:00"}));
        userdata::write(&file, theirs).unwrap();
        assert!(page.toggle("r-3f9a1c2b", &at(NOW)));
        assert!(on_disk(&file).items.iter().any(|r| r.id == "r-from-voice"), "the voice's reminder survived the window's save");
    }

    #[test]
    fn a_change_the_other_side_made_is_noticed_and_our_own_is_not() {
        let (mut page, file) = page();
        assert!(!page.reload_if_changed());
        page.toggle("r-3f9a1c2b", &at(NOW));
        assert!(!page.reload_if_changed(), "what we wrote ourselves is not news");
        let mut theirs = userdata::read(&file);
        theirs.insert("history".into(), json!([]));
        userdata::write(&file, theirs).unwrap();
        assert!(page.reload_if_changed() && page.data.history.is_empty());
    }

    #[test]
    fn a_save_that_fails_changes_nothing_and_says_why() {
        let (mut page, file) = page();
        let before = page.data.clone();
        page.path = Some(file.parent().unwrap().join("gone").join("reminders.json"));
        assert!(!page.toggle("r-3f9a1c2b", &at(NOW)));
        assert_eq!(page.data, before, "the page still shows what is really saved");
        page.new_draft(&at(NOW));
        fill(&mut page, "x", "tomorrow", "9am", Repeat::Once);
        assert!(!page.save_draft(&at(NOW)) && page.draft.as_ref().unwrap().errors[0].contains("could not write"));
    }

    #[test]
    fn a_page_with_no_file_saves_by_creating_it_and_one_with_no_path_changes_only_what_it_shows() {
        let dir = tempdir("reminders-fresh");
        let file = dir.join("reminders.json");
        let mut page = RemindersState::open(file.clone());
        page.new_draft(&at(NOW));
        fill(&mut page, "First", "tomorrow", "9am", Repeat::Once);
        assert!(page.save_draft(&at(NOW)) && file.exists());
        let mut loose = RemindersState::default();
        loose.new_draft(&at(NOW));
        fill(&mut loose, "Only here", "tomorrow", "9am", Repeat::Once);
        assert!(loose.save_draft(&at(NOW)) && loose.data.items.len() == 1);
    }

    #[test]
    fn a_late_keystroke_for_a_form_that_is_gone_goes_nowhere_visible() {
        let mut page = RemindersState::default();
        page.title_edit().replace_text("late");
        page.cancel_draft();
        assert!(page.draft.is_none());
    }

    #[test]
    fn there_is_a_limit_and_it_is_said() {
        let mut page = RemindersState::default();
        for i in 0..MAX_REMINDERS {
            page.data.items.push(Reminder::new(format!("R{i}"), at("2026-11-23T09:00"), Repeat::Once, at(NOW)));
        }
        page.new_draft(&at(NOW));
        assert!(page.draft.is_none() && page.notice.as_ref().unwrap().text.contains("as many reminders"));
    }
}

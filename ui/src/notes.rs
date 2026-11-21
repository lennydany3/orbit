//! Notes and tasks: the list and the editor on the Notes page. The file is `notes.json` next to the settings (see `userdata`, and
//! `orbit/notes.py` for the other side, which adds to it when you say "note down ..." or "add ... to my tasks"):
//!
//! ```json
//! {"version": 1,
//!  "notes": [{"id": "n-1a2b3c4d", "title": "Gym plan", "body": "Mon legs ...", "created": "2026-11-22T10:15", "updated": "2026-11-22T10:20"}],
//!  "tasks": [{"id": "t-9f8e7d6c", "text": "Buy milk", "done": false, "created": "2026-11-22T10:16"}]}
//! ```
//!
//! What is typed into a note is saved a moment after the typing stops (and at once when another note is opened or the page is left),
//! as one whole file. No drawing and no windowing, so all of it is tested; `notes_view` only draws it.

use std::path::PathBuf;

use rune::TextEdit;
use serde_json::{Map, Value, json};

use crate::datetime::Stamp;
use crate::userdata;

pub const MAX_NOTES: usize = 500;
pub const MAX_TASKS: usize = 500;
pub const MAX_TITLE: usize = 80;
pub const MAX_BODY: usize = 20_000;
pub const MAX_TASK: usize = 200;
/// How long after the last keystroke a note is saved (seconds).
pub const SAVE_AFTER: f64 = 0.8;
/// What a note with no title is called.
pub const UNTITLED: &str = "Untitled";

#[derive(Clone, Debug, PartialEq)]
pub struct Note {
    pub id: String,
    pub title: String,
    pub body: String,
    pub created: Option<Stamp>,
    pub updated: Option<Stamp>,
    extra: Map<String, Value>,
}

const NOTE_KNOWN: [&str; 5] = ["id", "title", "body", "created", "updated"];

impl Note {
    fn parse(raw: &Value) -> Option<Note> {
        let obj = raw.as_object()?;
        let title = userdata::clean(userdata::text(obj, "title").unwrap_or_default(), MAX_TITLE);
        let body: String = userdata::text(obj, "body").unwrap_or_default().chars().take(MAX_BODY).collect();
        if title.is_empty() && body.trim().is_empty() {
            return None;
        }
        let id = userdata::clean(userdata::text(obj, "id").unwrap_or_default(), 40);
        Some(Note {
            id: if id.is_empty() { userdata::new_id("n") } else { id },
            title,
            body,
            created: userdata::text(obj, "created").and_then(Stamp::parse),
            updated: userdata::text(obj, "updated").and_then(Stamp::parse),
            extra: userdata::rest(obj, &NOTE_KNOWN),
        })
    }

    fn to_value(&self) -> Value {
        let mut obj = self.extra.clone();
        obj.insert("id".into(), json!(self.id));
        obj.insert("title".into(), json!(self.title));
        obj.insert("body".into(), json!(self.body));
        for (name, when) in [("created", self.created), ("updated", self.updated)] {
            if let Some(when) = when {
                obj.insert(name.into(), json!(when.to_text()));
            }
        }
        Value::Object(obj)
    }

    /// What the list calls it.
    pub fn heading(&self) -> &str {
        if self.title.is_empty() { UNTITLED } else { &self.title }
    }

    /// The first line of the note, for the list (its title, when it has one, is already shown above it).
    pub fn preview(&self) -> String {
        let body = self.body.split_whitespace().collect::<Vec<_>>().join(" ");
        let shown: String = body.chars().take(90).collect();
        if body.chars().count() > 90 { format!("{shown}\u{2026}") } else { shown }
    }

    fn matches(&self, words: &[String]) -> bool {
        let hay = format!("{} {}", self.title, self.body).to_lowercase();
        words.iter().all(|w| hay.contains(w.as_str()))
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Task {
    pub id: String,
    pub text: String,
    pub done: bool,
    pub created: Option<Stamp>,
    extra: Map<String, Value>,
}

const TASK_KNOWN: [&str; 4] = ["id", "text", "done", "created"];

impl Task {
    fn parse(raw: &Value) -> Option<Task> {
        let obj = raw.as_object()?;
        let text = userdata::clean(userdata::text(obj, "text")?, MAX_TASK);
        if text.is_empty() {
            return None;
        }
        let id = userdata::clean(userdata::text(obj, "id").unwrap_or_default(), 40);
        Some(Task {
            id: if id.is_empty() { userdata::new_id("t") } else { id },
            text,
            done: obj.get("done").and_then(Value::as_bool) == Some(true),
            created: userdata::text(obj, "created").and_then(Stamp::parse),
            extra: userdata::rest(obj, &TASK_KNOWN),
        })
    }

    fn to_value(&self) -> Value {
        let mut obj = self.extra.clone();
        obj.insert("id".into(), json!(self.id));
        obj.insert("text".into(), json!(self.text));
        obj.insert("done".into(), json!(self.done));
        if let Some(created) = self.created {
            obj.insert("created".into(), json!(created.to_text()));
        }
        Value::Object(obj)
    }
}

/// What `notes.json` holds.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Notes {
    pub notes: Vec<Note>,
    pub tasks: Vec<Task>,
    rest: Map<String, Value>,
}

impl Notes {
    pub fn from_map(data: &Map<String, Value>) -> Notes {
        let list = |key: &str| data.get(key).and_then(Value::as_array).cloned().unwrap_or_default();
        Notes {
            notes: list("notes").iter().take(MAX_NOTES).filter_map(Note::parse).collect(),
            tasks: list("tasks").iter().take(MAX_TASKS).filter_map(Task::parse).collect(),
            rest: userdata::rest(data, &["version", "notes", "tasks"]),
        }
    }

    pub fn to_map(&self) -> Map<String, Value> {
        let mut data = self.rest.clone();
        data.insert("notes".into(), Value::Array(self.notes.iter().map(Note::to_value).collect()));
        data.insert("tasks".into(), Value::Array(self.tasks.iter().map(Task::to_value).collect()));
        data
    }

    /// The newest change first (notes changed in the same minute: the one saved last).
    pub fn recent(&self) -> Vec<&Note> {
        let mut list: Vec<(usize, &Note)> = self.notes.iter().enumerate().collect();
        list.sort_by_key(|(i, n)| std::cmp::Reverse((n.updated.or(n.created), *i)));
        list.into_iter().map(|(_, n)| n).collect()
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Tab {
    #[default]
    Notes,
    Tasks,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Notice {
    pub text: String,
    pub good: bool,
}

/// Everything the Notes page shows and edits.
pub struct NotesState {
    /// Where it is saved. `None` saves nothing (headless scenes, tests).
    pub path: Option<PathBuf>,
    pub data: Notes,
    pub tab: Tab,
    pub search: TextEdit,
    /// The note open in the editor, by id, and what is in its two boxes.
    pub open: Option<String>,
    pub title: TextEdit,
    pub body: TextEdit,
    /// What the boxes held when they were last saved (or loaded): a difference means there is something to save.
    saved: (String, String),
    /// When the first unsaved keystroke happened (page clock, seconds).
    dirty_since: Option<f64>,
    pub new_task: TextEdit,
    pub notice: Option<Notice>,
    pub confirm_delete: Option<String>,
    stamp: Option<(u128, u64)>,
}

impl Default for NotesState {
    fn default() -> Self {
        NotesState {
            path: None,
            data: Notes::default(),
            tab: Tab::Notes,
            search: TextEdit::new(""),
            open: None,
            title: TextEdit::new(""),
            body: TextEdit::new(""),
            saved: (String::new(), String::new()),
            dirty_since: None,
            new_task: TextEdit::new(""),
            notice: None,
            confirm_delete: None,
            stamp: None,
        }
    }
}

impl NotesState {
    /// Notes from JSON text, saved nowhere (for the headless scenes and tests).
    pub fn from_json(json: &str) -> NotesState {
        let data = serde_json::from_str::<Value>(json).ok().and_then(|v| v.as_object().map(Notes::from_map)).unwrap_or_default();
        NotesState { data, ..Default::default() }
    }

    /// The notes in `path`, which is also where changes are saved.
    pub fn open(path: PathBuf) -> NotesState {
        let data = Notes::from_map(&userdata::read(&path));
        NotesState { stamp: userdata::stamp(&path), data, path: Some(path), ..Default::default() }
    }

    /// Reads the file again if the other side changed it. True if it did. A note open in the editor stays as it is being edited.
    pub fn reload_if_changed(&mut self) -> bool {
        let Some(path) = &self.path else { return false };
        let now = userdata::stamp(path);
        if now == self.stamp {
            return false;
        }
        self.stamp = now;
        self.data = Notes::from_map(&userdata::read(path));
        if self.open.as_ref().is_some_and(|id| !self.data.notes.iter().any(|n| n.id == *id)) && !self.unsaved() {
            self.close_note();                                              // deleted from the other side
        }
        true
    }

    fn good(&mut self, text: impl Into<String>) {
        self.notice = Some(Notice { text: text.into(), good: true });
    }

    fn bad(&mut self, text: impl Into<String>) {
        self.notice = Some(Notice { text: text.into(), good: false });
    }

    /// Reads the file fresh, lets `change` alter it, and writes it whole. The change is not made if it cannot be saved.
    fn change<T>(&mut self, change: impl FnOnce(&mut Notes) -> Result<T, String>) -> Result<T, String> {
        let mut next = match &self.path {
            Some(path) => Notes::from_map(&userdata::read(path)),
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

    // ---- the list ----

    /// The notes to list: the newest first, and only those with every word of the search in them.
    pub fn shown(&self) -> Vec<&Note> {
        let words: Vec<String> = self.search.text.to_lowercase().split_whitespace().map(str::to_owned).collect();
        self.data.recent().into_iter().filter(|n| n.matches(&words)).collect()
    }

    pub fn select_tab(&mut self, tab: Tab) {
        self.flush();
        self.tab = tab;
        self.notice = None;
        self.confirm_delete = None;
    }

    // ---- the editor ----

    pub fn new_note(&mut self, now: &Stamp) {
        self.flush();
        if self.data.notes.len() >= MAX_NOTES {
            return self.bad(format!("That is as many notes as Orbit keeps ({MAX_NOTES}). Delete one first."));
        }
        let stamp = Stamp { second: 0, ..*now };
        let id = userdata::new_id("n");
        let made = Note { id: id.clone(), title: UNTITLED.to_owned(), body: String::new(), created: Some(stamp), updated: Some(stamp), extra: Map::new() };
        match self.change(|data| {
            data.notes.push(made);
            Ok(())
        }) {
            Ok(()) => {
                self.tab = Tab::Notes;
                self.search.replace_text("");
                self.show(&id);
                self.notice = None;
            }
            Err(why) => self.bad(why),
        }
    }

    /// Opens a note in the editor (saving the one that was open).
    pub fn open_note(&mut self, id: &str) {
        self.flush();
        self.show(id);
        self.confirm_delete = None;
        self.notice = None;
    }

    fn show(&mut self, id: &str) {
        let Some(note) = self.data.notes.iter().find(|n| n.id == id) else { return };
        let (title, body) = (note.title.clone(), note.body.clone());
        self.title.replace_text(title.clone());
        self.body.replace_text(body.clone());
        self.saved = (title, body);
        self.open = Some(id.to_owned());
        self.dirty_since = None;
    }

    pub fn close_note(&mut self) {
        self.flush();
        self.open = None;
        self.title.replace_text("");
        self.body.replace_text("");
        self.saved = (String::new(), String::new());
    }

    pub fn title_edit(&mut self) -> &mut TextEdit {
        &mut self.title
    }

    pub fn body_edit(&mut self) -> &mut TextEdit {
        &mut self.body
    }

    pub fn search_edit(&mut self) -> &mut TextEdit {
        &mut self.search
    }

    pub fn task_edit(&mut self) -> &mut TextEdit {
        &mut self.new_task
    }

    /// Is there something typed in the editor that is not saved yet?
    fn unsaved(&self) -> bool {
        self.open.is_some() && (self.title.text != self.saved.0 || self.body.text != self.saved.1)
    }

    /// Looks at the boxes: is there anything not saved yet? Called every frame, so it only compares text. `now` is the page's clock.
    pub fn watch(&mut self, now: f64) {
        match (self.unsaved(), self.dirty_since) {
            (true, None) => self.dirty_since = Some(now),
            (false, Some(_)) => self.dirty_since = None,
            _ => {}
        }
    }

    /// When the typing so far is to be saved (page clock, seconds), if there is some not saved.
    pub fn save_due(&self) -> Option<f64> {
        self.dirty_since.map(|t| t + SAVE_AFTER)
    }

    /// Saves what was typed if it has been left alone long enough. True if it saved.
    pub fn save_if_due(&mut self, now: f64) -> bool {
        self.watch(now);
        match self.save_due() {
            Some(due) if due <= now => self.flush(),
            _ => false,
        }
    }

    /// Saves what is typed in the editor now, if it is different from what was saved. True if it saved something.
    pub fn flush(&mut self) -> bool {
        let Some(id) = self.open.clone() else { return false };
        if self.title.text == self.saved.0 && self.body.text == self.saved.1 {
            self.dirty_since = None;
            return false;
        }
        let title = userdata::clean(&self.title.text, MAX_TITLE * 4).chars().take(MAX_TITLE).collect::<String>();
        let body: String = self.body.text.chars().take(MAX_BODY).collect();
        let (typed_title, typed_body) = (self.title.text.clone(), self.body.text.clone());
        let now = Stamp::now();
        let saved = self.change(|data| {
            let updated = Some(Stamp { second: 0, ..now });
            match data.notes.iter().position(|n| n.id == id) {
                Some(i) => {
                    let n = &mut data.notes[i];
                    (n.title, n.body, n.updated) = (title.clone(), body.clone(), updated);
                }
                None if data.notes.len() >= MAX_NOTES => return Err("The note could not be saved: too many notes.".to_owned()),
                // deleted from the other side while it was being edited: what was typed is kept as a note again
                None => data.notes.push(Note { id: id.clone(), title: title.clone(), body: body.clone(), created: updated, updated, extra: Map::new() }),
            }
            Ok(())
        });
        self.dirty_since = None;
        match saved {
            Ok(()) => {
                self.saved = (typed_title, typed_body);
                true
            }
            Err(why) => {
                self.bad(why);
                self.dirty_since = Some(0.0);                               // try again at the next look
                false
            }
        }
    }

    pub fn ask_delete(&mut self, id: &str) {
        self.confirm_delete = self.data.notes.iter().any(|n| n.id == id).then(|| id.to_owned());
    }

    pub fn delete_note(&mut self, id: &str) -> bool {
        self.confirm_delete = None;
        if self.open.as_deref() == Some(id) {
            self.open = None;                                                  // what was typed goes with it
            self.dirty_since = None;
            self.title.replace_text("");
            self.body.replace_text("");
            self.saved = (String::new(), String::new());
        }
        let id = id.to_owned();
        match self.change(|data| {
            let at = data.notes.iter().position(|n| n.id == id).ok_or("That note is gone.")?;
            Ok(data.notes.remove(at).heading().to_owned())
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

    // ---- tasks ----

    pub fn open_tasks(&self) -> usize {
        self.data.tasks.iter().filter(|t| !t.done).count()
    }

    /// Adds the task typed in the box. False if there was nothing to add.
    pub fn add_task(&mut self, now: &Stamp) -> bool {
        let text = userdata::clean(&self.new_task.text, MAX_TASK * 4);
        if text.is_empty() {
            return false;
        }
        if text.chars().count() > MAX_TASK {
            self.bad(format!("A task is at most {MAX_TASK} characters."));
            return false;
        }
        let made = Task { id: userdata::new_id("t"), text, done: false, created: Some(Stamp { second: 0, ..*now }), extra: Map::new() };
        match self.change(|data| {
            if data.tasks.len() >= MAX_TASKS {
                return Err(format!("That is as many tasks as Orbit keeps ({MAX_TASKS}). Clear some first."));
            }
            data.tasks.push(made);
            Ok(())
        }) {
            Ok(()) => {
                self.new_task.replace_text("");
                self.notice = None;
                true
            }
            Err(why) => {
                self.bad(why);
                false
            }
        }
    }

    pub fn toggle_task(&mut self, id: &str) -> bool {
        let id = id.to_owned();
        match self.change(|data| {
            let task = data.tasks.iter_mut().find(|t| t.id == id).ok_or("That task is gone.")?;
            task.done = !task.done;
            Ok(())
        }) {
            Ok(()) => true,
            Err(why) => {
                self.bad(why);
                false
            }
        }
    }

    pub fn delete_task(&mut self, id: &str) -> bool {
        let id = id.to_owned();
        self.change(|data| {
            data.tasks.retain(|t| t.id != id);
            Ok(())
        })
        .is_ok()
    }

    /// Takes every finished task off the list. How many went.
    pub fn clear_done(&mut self) -> usize {
        match self.change(|data| {
            let before = data.tasks.len();
            data.tasks.retain(|t| !t.done);
            Ok(before - data.tasks.len())
        }) {
            Ok(n) => {
                if n > 0 {
                    self.good(format!("{n} finished {} cleared.", if n == 1 { "task is" } else { "tasks are" }));
                }
                n
            }
            Err(why) => {
                self.bad(why);
                0
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::tests::tempdir;

    const EXAMPLE: &str = include_str!("../../tests/fixtures/notes_example.json");

    fn at(text: &str) -> Stamp {
        Stamp::parse(text).unwrap()
    }

    fn page() -> (NotesState, PathBuf) {
        let dir = tempdir("notes");
        let file = dir.join("notes.json");
        std::fs::write(&file, EXAMPLE).unwrap();
        (NotesState::open(file.clone()), file)
    }

    fn on_disk(file: &std::path::Path) -> Notes {
        Notes::from_map(&userdata::read(file))
    }

    #[test]
    fn the_example_file_is_read_as_written_and_written_back_the_same() {
        let data: Value = serde_json::from_str(EXAMPLE).unwrap();
        let n = Notes::from_map(data.as_object().unwrap());
        assert_eq!((n.notes.len(), n.tasks.len()), (3, 3));
        assert_eq!((n.notes[0].title.as_str(), n.notes[0].body.as_str()), ("Gym plan", "Monday legs\nWednesday push\nFriday pull"));
        assert_eq!((n.tasks[0].text.as_str(), n.tasks[0].done, n.tasks[1].done), ("Buy milk", false, true));
        let mut back = n.to_map();
        back.insert("version".into(), json!(1));
        assert_eq!(Value::Object(back), data, "nothing lost, the unknown fields included");
    }

    #[test]
    fn bad_entries_are_left_out() {
        let v = json!({"notes": [{"title": "", "body": "  "}, "x", {"id": "n-1", "title": "Ok"}], "tasks": [{"text": ""}, 3, {"id": "t-1", "text": "Fine", "done": "yes"}]});
        let n = Notes::from_map(v.as_object().unwrap());
        assert_eq!(n.notes.iter().map(|n| n.title.as_str()).collect::<Vec<_>>(), ["Ok"]);
        assert_eq!(n.tasks.iter().map(|t| (t.text.as_str(), t.done)).collect::<Vec<_>>(), [("Fine", false)]);
    }

    #[test]
    fn the_newest_change_is_first_and_the_search_needs_every_word() {
        let (mut page, _) = page();
        assert_eq!(page.shown().iter().map(|n| n.heading()).collect::<Vec<_>>(), ["Wifi", "Gym plan", "Ideas"]);
        page.search.replace_text("push  FRIDAY");
        assert_eq!(page.shown().iter().map(|n| n.heading()).collect::<Vec<_>>(), ["Gym plan"], "in the body, any case");
        page.search.replace_text("wifi router zzz");
        assert!(page.shown().is_empty());
        page.search.replace_text("");
        assert_eq!(page.shown().len(), 3);
    }

    #[test]
    fn a_note_shows_a_heading_and_a_short_preview() {
        let (page, _) = page();
        let gym = page.data.notes.iter().find(|n| n.title == "Gym plan").unwrap();
        assert_eq!(gym.preview(), "Monday legs Wednesday push Friday pull");
        let untitled = Note { title: String::new(), ..gym.clone() };
        assert_eq!(untitled.heading(), UNTITLED);
        let long = Note { body: "word ".repeat(60), ..gym.clone() };
        assert!(long.preview().chars().count() <= 91 && long.preview().ends_with('\u{2026}'));
    }

    // ---- the editor ----

    #[test]
    fn a_new_note_is_made_saved_and_opened() {
        let (mut page, file) = page();
        page.new_note(&at("2026-11-22T14:30"));
        let id = page.open.clone().unwrap();
        let saved = on_disk(&file);
        let made = saved.notes.iter().find(|n| n.id == id).unwrap();
        assert_eq!((made.title.as_str(), made.body.as_str(), made.created), (UNTITLED, "", Some(at("2026-11-22T14:30"))));
        assert_eq!((page.title.text.as_str(), saved.notes.len()), (UNTITLED, 4));
        assert_eq!(page.shown()[0].id, id, "and it is the first in the list");
    }

    #[test]
    fn typing_is_saved_a_moment_after_it_stops_and_not_while_it_goes_on() {
        let (mut page, file) = page();
        page.open_note("n-gym");
        assert!(!page.save_if_due(10.0) && page.save_due().is_none(), "nothing changed: nothing to save");
        page.body.replace_text("Monday legs\nWednesday push\nFriday pull\nSunday rest");
        assert!(!page.save_if_due(10.0), "just typed");
        assert_eq!(page.save_due(), Some(10.0 + SAVE_AFTER));
        page.title.replace_text("Gym plan v2");
        assert!(!page.save_if_due(10.5), "still typing: the wait runs from the first unsaved keystroke, so it cannot be put off for ever");
        assert!(page.save_if_due(10.9));
        let note = on_disk(&file).notes.into_iter().find(|n| n.id == "n-gym").unwrap();
        assert_eq!((note.title.as_str(), note.body.ends_with("Sunday rest")), ("Gym plan v2", true));
        assert!(note.updated.is_some_and(|u| u > at("2026-11-21T18:30")), "its change time moved on");
        assert!(!page.save_if_due(20.0), "and it is not saved twice");
    }

    #[test]
    fn opening_another_note_saves_the_one_being_edited_first() {
        let (mut page, file) = page();
        page.open_note("n-gym");
        page.body.replace_text("changed");
        page.open_note("n-wifi");
        assert_eq!(page.open.as_deref(), Some("n-wifi"));
        assert_eq!(on_disk(&file).notes.iter().find(|n| n.id == "n-gym").unwrap().body, "changed");
        assert_eq!(page.title.text, "Wifi", "and the boxes now hold the other note");
    }

    #[test]
    fn a_note_whose_title_is_emptied_keeps_its_body_and_is_called_untitled() {
        let (mut page, file) = page();
        page.open_note("n-gym");
        page.title.replace_text("   ");
        assert!(page.flush());
        let note = on_disk(&file).notes.into_iter().find(|n| n.id == "n-gym").unwrap();
        assert_eq!((note.title.as_str(), note.heading()), ("", UNTITLED));
        assert!(note.body.starts_with("Monday"));
    }

    #[test]
    fn a_note_deleted_from_the_other_side_while_open_is_kept_when_the_typing_is_saved() {
        let (mut page, file) = page();
        page.open_note("n-ideas");
        page.body.replace_text("an idea I just had");
        let mut theirs = userdata::read(&file);
        theirs.get_mut("notes").unwrap().as_array_mut().unwrap().retain(|n| n["id"] != "n-ideas");
        userdata::write(&file, theirs).unwrap();
        assert!(page.reload_if_changed() && page.open.as_deref() == Some("n-ideas"), "typing is not thrown away");
        assert!(page.flush());
        assert_eq!(on_disk(&file).notes.iter().find(|n| n.id == "n-ideas").unwrap().body, "an idea I just had");
    }

    #[test]
    fn an_open_note_that_vanishes_with_nothing_typed_just_closes() {
        let (mut page, file) = page();
        page.open_note("n-ideas");
        let mut theirs = userdata::read(&file);
        theirs.get_mut("notes").unwrap().as_array_mut().unwrap().retain(|n| n["id"] != "n-ideas");
        userdata::write(&file, theirs).unwrap();
        assert!(page.reload_if_changed() && page.open.is_none());
    }

    #[test]
    fn deleting_asks_first_and_takes_the_open_note_out_of_the_editor() {
        let (mut page, file) = page();
        page.open_note("n-gym");
        page.body.replace_text("unsaved words");
        page.ask_delete("n-gym");
        assert_eq!(page.confirm_delete.as_deref(), Some("n-gym"));
        assert_eq!(on_disk(&file).notes.len(), 3, "asking changes nothing");
        assert!(page.delete_note("n-gym"));
        assert_eq!((page.open.clone(), page.confirm_delete.clone(), on_disk(&file).notes.len()), (None, None, 2));
        assert!(page.title.text.is_empty() && !page.save_if_due(99.0), "nothing is left to save");
        page.ask_delete("nope");
        assert!(page.confirm_delete.is_none() && !page.delete_note("nope"));
    }

    #[test]
    fn what_orbit_added_to_the_file_is_not_lost_by_the_windows_next_save() {
        let (mut page, file) = page();
        let mut theirs = userdata::read(&file);
        theirs.get_mut("notes").unwrap().as_array_mut().unwrap().push(json!({"id": "n-voice", "title": "Said aloud"}));
        theirs.get_mut("tasks").unwrap().as_array_mut().unwrap().push(json!({"id": "t-voice", "text": "Said aloud too"}));
        userdata::write(&file, theirs).unwrap();
        page.open_note("n-gym");
        page.body.replace_text("x");
        page.flush();
        page.toggle_task("t-milk");
        let saved = on_disk(&file);
        assert!(saved.notes.iter().any(|n| n.id == "n-voice") && saved.tasks.iter().any(|t| t.id == "t-voice"));
    }

    #[test]
    fn a_change_the_other_side_made_is_noticed_and_our_own_is_not() {
        let (mut page, file) = page();
        assert!(!page.reload_if_changed());
        page.toggle_task("t-milk");
        assert!(!page.reload_if_changed());
        let mut theirs = userdata::read(&file);
        theirs.get_mut("tasks").unwrap().as_array_mut().unwrap().clear();
        userdata::write(&file, theirs).unwrap();
        assert!(page.reload_if_changed() && page.data.tasks.is_empty());
    }

    // ---- tasks ----

    #[test]
    fn a_task_is_added_ticked_removed_and_cleared() {
        let (mut page, file) = page();
        assert_eq!(page.open_tasks(), 2);
        assert!(!page.add_task(&at("2026-11-22T14:30")), "nothing typed");
        page.new_task.replace_text("  Call   the bank ");
        assert!(page.add_task(&at("2026-11-22T14:30")));
        assert_eq!(page.new_task.text, "", "the box is ready for the next one");
        let saved = on_disk(&file);
        let made = saved.tasks.last().unwrap();
        assert_eq!((made.text.as_str(), made.done, made.created), ("Call the bank", false, Some(at("2026-11-22T14:30"))));
        assert!(page.toggle_task(&made.id.clone()) && page.open_tasks() == 2);
        assert!(page.toggle_task("t-milk") && page.open_tasks() == 1);
        assert_eq!(page.clear_done(), 3);
        assert_eq!(on_disk(&file).tasks.iter().map(|t| t.text.as_str()).collect::<Vec<_>>(), ["Pay rent"]);
        assert!(page.delete_task("t-rent") && on_disk(&file).tasks.is_empty());
        assert_eq!(page.clear_done(), 0);
        assert!(!page.toggle_task("nope") && !page.notice.as_ref().unwrap().good);
    }

    #[test]
    fn a_task_that_is_too_long_is_refused_with_the_limit() {
        let (mut page, _) = page();
        page.new_task.replace_text("x".repeat(MAX_TASK + 1));
        assert!(!page.add_task(&at("2026-11-22T14:30")) && page.notice.as_ref().unwrap().text.contains("at most"));
        assert_eq!(page.new_task.text.len(), MAX_TASK + 1, "what was typed is kept to be shortened");
    }

    #[test]
    fn a_save_that_fails_changes_nothing_and_says_why() {
        let (mut page, file) = page();
        page.new_task.replace_text("New one");
        let before = page.data.clone();
        page.path = Some(file.parent().unwrap().join("gone").join("notes.json"));
        assert!(!page.add_task(&at("2026-11-22T14:30")));
        assert_eq!(page.data, before);
        assert!(page.notice.as_ref().unwrap().text.contains("could not write"));
        assert_eq!(page.new_task.text, "New one", "so it can be tried again");
    }

    #[test]
    fn leaving_the_page_or_changing_tab_saves_what_is_typed() {
        let (mut page, file) = page();
        page.open_note("n-gym");
        page.body.replace_text("typed just now");
        page.select_tab(Tab::Tasks);
        assert_eq!(page.tab, Tab::Tasks);
        assert_eq!(on_disk(&file).notes.iter().find(|n| n.id == "n-gym").unwrap().body, "typed just now");
        page.select_tab(Tab::Notes);
        page.body.replace_text("and more");
        page.close_note();
        assert_eq!(on_disk(&file).notes.iter().find(|n| n.id == "n-gym").unwrap().body, "and more");
    }

    #[test]
    fn a_page_with_no_file_saves_by_creating_it() {
        let dir = tempdir("notes-fresh");
        let file = dir.join("notes.json");
        let mut page = NotesState::open(file.clone());
        page.new_note(&at("2026-11-22T14:30"));
        assert!(file.exists() && page.open.is_some());
        let mut loose = NotesState::default();
        loose.new_note(&at("2026-11-22T14:30"));
        assert_eq!(loose.data.notes.len(), 1);
    }
}

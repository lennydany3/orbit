//! Notes and tasks: the list and the editor on the Notes page. The file is `notes.json` next to the settings (see `userdata`, and
//! `orbit/notes.py` for the other side, which adds to it when you say "note down ..." or "add ... to my tasks"):
//!
//! ```json
//! {"version": 1,
//!  "notes": [{"id": "n-1a2b3c4d", "title": "Gym plan", "body": "Mon legs ...", "created": "2026-11-22T10:15", "updated": "2026-11-22T10:20"}],
//!  "tasks": [{"id": "t-9f8e7d6c", "text": "Buy milk", "done": false, "created": "2026-11-22T10:16", "list": "shopping"}]}
//! ```
//!
//! A task may name the list it is on ("shopping", "packing"); with no `list` it is on the plain task list, called Tasks. Orbit puts
//! things on a list when you say "add tomato and pasta to my shopping list".
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
/// The longest name a list has.
pub const MAX_LIST: usize = 30;
/// What the plain task list is called.
pub const PLAIN_LIST: &str = "Tasks";

/// A list's name as it is kept: lowercase words of letters, digits and hyphens, at most `MAX_LIST` characters ("" is the plain task
/// list). The same rule as `clean_list` in `orbit/notes.py`, so both sides spell a list the same way.
pub fn clean_list(name: &str) -> String {
    let mut words: Vec<String> = Vec::new();
    let mut word = String::new();
    for c in name.to_lowercase().chars().chain(std::iter::once(' ')) {
        if c.is_ascii_lowercase() || c.is_ascii_digit() || (c == '-' && !word.is_empty()) {
            word.push(c);
        } else if !word.is_empty() {
            words.push(std::mem::take(&mut word));
        }
    }
    words.join(" ").chars().take(MAX_LIST).collect::<String>().trim().to_owned()
}

/// How a list is called on the page and out loud: "Shopping", and "Tasks" for the plain one.
pub fn list_title(name: &str) -> String {
    let mut chars = name.chars();
    match chars.next() {
        None => PLAIN_LIST.to_owned(),
        Some(first) => first.to_uppercase().chain(chars).collect(),
    }
}
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
    /// Which list it is on ("" is the plain task list).
    pub list: String,
    extra: Map<String, Value>,
}

const TASK_KNOWN: [&str; 5] = ["id", "text", "done", "created", "list"];

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
            list: clean_list(userdata::text(obj, "list").unwrap_or_default()),
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
        if !self.list.is_empty() {
            obj.insert("list".into(), json!(self.list));
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

    /// Every list that has something on it, with how many are still to do: the plain task list first, then in the order they began.
    #[cfg(test)]
    fn data_lists(&self) -> Vec<String> {
        self.list_names().into_iter().map(|(name, _)| name).collect()
    }

    pub fn list_names(&self) -> Vec<(String, usize)> {
        let mut order: Vec<&str> = Vec::new();
        for t in &self.tasks {
            if !order.contains(&t.list.as_str()) {
                order.push(&t.list);
            }
        }
        order.sort_by_key(|name| !name.is_empty());
        order.into_iter().map(|name| (name.to_owned(), self.tasks.iter().filter(|t| t.list == name && !t.done).count())).collect()
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
    /// Which list the Tasks tab shows and adds to: `None` is every list together, `Some("")` the plain task list.
    pub list: Option<String>,
    /// The box a new list's name is typed in.
    pub new_list: TextEdit,
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
            list: None,
            new_list: TextEdit::new(""),
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

    pub fn list_edit(&mut self) -> &mut TextEdit {
        &mut self.new_list
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

    /// Is this task on what the Tasks tab shows (every list, or the one chosen)?
    pub fn in_view(&self, task: &Task) -> bool {
        self.list.as_ref().is_none_or(|list| *list == task.list)
    }

    /// How many tasks in view are still to do.
    pub fn open_tasks(&self) -> usize {
        self.data.tasks.iter().filter(|t| !t.done && self.in_view(t)).count()
    }

    /// How many tasks in view there are, finished or not.
    pub fn tasks_in_view(&self) -> usize {
        self.data.tasks.iter().filter(|t| self.in_view(t)).count()
    }

    /// What the Tasks tab offers to look at: everything, then each list (with how many are to do). The chosen list is there even when
    /// nothing is on it yet.
    pub fn list_choices(&self) -> Vec<(Option<String>, String, usize)> {
        let all = self.data.tasks.iter().filter(|t| !t.done).count();
        let mut choices = vec![(None, "All".to_owned(), all)];
        let mut named = self.data.list_names();
        if let Some(chosen) = &self.list
            && !named.iter().any(|(name, _)| name == chosen)
        {
            named.push((chosen.clone(), 0));
        }
        choices.extend(named.into_iter().map(|(name, open)| (Some(name.clone()), list_title(&name), open)));
        choices
    }

    /// Looks at one list (or, with `None`, every list together). New tasks go on the one being looked at.
    pub fn select_list(&mut self, list: Option<String>) {
        self.list = list;
        self.notice = None;
    }

    /// Makes the list named in the box the one looked at, so what is added next goes on it (it exists once something is on it).
    /// False if the box was empty.
    pub fn start_list(&mut self) -> bool {
        let name = clean_list(&self.new_list.text);
        if name.is_empty() {
            return false;
        }
        self.new_list.replace_text("");
        self.list = Some(name);
        self.notice = None;
        true
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
        let list = self.list.clone().unwrap_or_default();
        let made = Task { id: userdata::new_id("t"), text, done: false, created: Some(Stamp { second: 0, ..*now }), list, extra: Map::new() };
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

    /// Takes every finished task off what is in view (every list, or the one chosen). How many went.
    pub fn clear_done(&mut self) -> usize {
        let view = self.list.clone();
        match self.change(|data| {
            let before = data.tasks.len();
            data.tasks.retain(|t| !(t.done && view.as_ref().is_none_or(|list| *list == t.list)));
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

    // ---- lists ----

    const LISTS: &str = include_str!("../../tests/fixtures/notes_lists_example.json");

    fn lists_page() -> (NotesState, PathBuf) {
        let dir = tempdir("notes-lists");
        let file = dir.join("notes.json");
        std::fs::write(&file, LISTS).unwrap();
        (NotesState::open(file.clone()), file)
    }

    #[test]
    fn a_list_is_named_the_same_way_on_both_sides() {
        for (said, kept) in [
            ("Shopping", "shopping"), ("  Packing   list ", "packing list"), ("to-do", "to-do"), ("-x", "x"), ("a_b", "a b"), ("Caf\u{e9}", "caf"),
            ("", ""), ("!!!", ""), ("one two three four five six seven eight nine", "one two three four five six sev"[..30].trim_end()),
        ] {
            assert_eq!(clean_list(said), kept, "{said:?}");
        }
        assert_eq!((list_title(""), list_title("shopping"), list_title("packing list")), ("Tasks".to_owned(), "Shopping".to_owned(), "Packing list".to_owned()));
    }

    #[test]
    fn the_lists_file_is_read_as_written_and_written_back_the_same() {
        let data: Value = serde_json::from_str(LISTS).unwrap();
        let n = Notes::from_map(data.as_object().unwrap());
        assert_eq!(n.tasks.iter().map(|t| (t.text.as_str(), t.list.as_str())).collect::<Vec<_>>(), [("Call mom", ""), ("Tomato", "shopping"), ("Pasta", "shopping"), ("Toothbrush", "packing")]);
        let mut back = n.to_map();
        back.insert("version".into(), json!(1));
        assert_eq!(Value::Object(back), data, "a plain task has no list field, and nothing else is lost");
    }

    #[test]
    fn the_lists_are_counted_the_plain_one_first_then_in_the_order_they_began() {
        let (page, _) = lists_page();
        assert_eq!(page.data.list_names(), [("".to_owned(), 1), ("shopping".to_owned(), 2), ("packing".to_owned(), 1)]);
        assert_eq!(
            page.list_choices().iter().map(|(list, label, open)| (list.clone(), label.as_str(), *open)).collect::<Vec<_>>(),
            [(None, "All", 4), (Some("".into()), "Tasks", 1), (Some("shopping".into()), "Shopping", 2), (Some("packing".into()), "Packing", 1)]
        );
    }

    #[test]
    fn looking_at_one_list_shows_only_its_tasks_and_adds_to_it() {
        let (mut page, file) = lists_page();
        assert_eq!((page.open_tasks(), page.tasks_in_view()), (4, 4), "every list together");
        page.select_list(Some("shopping".into()));
        assert_eq!((page.open_tasks(), page.tasks_in_view()), (2, 2));
        assert!(page.data.tasks.iter().filter(|t| page.in_view(t)).all(|t| t.list == "shopping"));
        page.new_task.replace_text("Milkshake");
        assert!(page.add_task(&at("2026-11-22T14:30")));
        let saved = on_disk(&file);
        assert_eq!(saved.tasks.last().map(|t| (t.text.as_str(), t.list.as_str())), Some(("Milkshake", "shopping")));
        page.select_list(Some("".into()));
        page.new_task.replace_text("Pay rent");
        assert!(page.add_task(&at("2026-11-22T14:31")));
        assert_eq!(on_disk(&file).tasks.last().map(|t| t.list.as_str()), Some(""), "the plain list again: no list field");
        page.select_list(None);
        page.new_task.replace_text("Anywhere");
        assert!(page.add_task(&at("2026-11-22T14:32")));
        assert_eq!(on_disk(&file).tasks.last().map(|t| t.list.as_str()), Some(""), "adding while looking at all goes on the plain list");
    }

    #[test]
    fn a_new_list_is_named_in_the_box_and_is_offered_before_anything_is_on_it() {
        let (mut page, file) = lists_page();
        assert!(!page.start_list(), "nothing typed");
        page.new_list.replace_text("  Gym Bag!  ");
        assert!(page.start_list());
        assert_eq!((page.list.as_deref(), page.new_list.text.as_str()), (Some("gym bag"), ""));
        assert!(page.list_choices().iter().any(|(list, label, open)| list.as_deref() == Some("gym bag") && label == "Gym bag" && *open == 0), "offered while it is empty");
        page.new_task.replace_text("Towel");
        assert!(page.add_task(&at("2026-11-22T14:30")));
        assert_eq!(on_disk(&file).data_lists(), ["", "shopping", "packing", "gym bag"]);
    }

    #[test]
    fn clearing_finished_tasks_clears_only_what_is_in_view() {
        let (mut page, file) = lists_page();
        for id in ["t-call", "t-pasta"] {
            assert!(page.toggle_task(id));
        }
        page.select_list(Some("shopping".into()));
        assert_eq!(page.clear_done(), 1, "the finished shopping task; the finished plain one stays");
        assert_eq!(on_disk(&file).tasks.iter().map(|t| t.text.as_str()).collect::<Vec<_>>(), ["Call mom", "Tomato", "Toothbrush"]);
        page.select_list(None);
        assert_eq!(page.clear_done(), 1);
        assert_eq!(on_disk(&file).tasks.len(), 2);
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

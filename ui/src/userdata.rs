//! Where Orbit keeps what is yours (reminders, notes and tasks, playlists): small JSON files next to `settings.json`, which the window
//! and Orbit (Python) both read and write. The rules are the same on both sides (`orbit/userdata.py`): a whole file is written under a
//! temporary name and renamed over the old one, a missing or broken file reads as empty, and anything in a file or a record that this
//! version does not know is written back as it was.
//!
//! A change reads the file fresh, changes the one thing and writes it whole, so a change the other side made a moment ago is not lost.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

pub const REMINDERS_FILE: &str = "reminders.json";
pub const NOTES_FILE: &str = "notes.json";
pub const MUSIC_FILE: &str = "music.json";
pub const VERSION: i64 = 1;

/// The data file `name` that goes with a settings file: in the same folder.
pub fn beside(settings_file: &Path, name: &str) -> PathBuf {
    settings_file.with_file_name(name)
}

/// The file's JSON object, or an empty one if it is missing, unreadable, or not an object. Never fails.
pub fn read(path: &Path) -> Map<String, Value> {
    let Ok(text) = std::fs::read_to_string(path) else { return Map::new() };
    match serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}')) {
        Ok(Value::Object(map)) => map,
        _ => Map::new(),
    }
}

/// Writes the whole file or none of it.
pub fn write(path: &Path, mut data: Map<String, Value>) -> Result<(), String> {
    data.insert("version".into(), Value::from(VERSION));
    let mut text = serde_json::to_string_pretty(&Value::Object(data)).map_err(|e| e.to_string())?;
    text.push('\n');
    let temp = path.with_file_name(format!("{}.{}.tmp", path.file_name().and_then(|n| n.to_str()).unwrap_or("data"), std::process::id()));
    std::fs::write(&temp, text).map_err(|e| format!("could not write {}: {e}", temp.display()))?;
    std::fs::rename(&temp, path).map_err(|e| {
        let _ = std::fs::remove_file(&temp);
        format!("could not save {}: {e}", path.display())
    })
}

/// Changes whenever the file does (None if there is none): how a watcher notices an edit the other side made.
pub fn stamp(path: &Path) -> Option<(u128, u64)> {
    let meta = std::fs::metadata(path).ok()?;
    let modified = meta.modified().ok()?.duration_since(std::time::UNIX_EPOCH).ok()?.as_nanos();
    Some((modified, meta.len()))
}

/// A short unique id for a record: `r-3f9a1c2b`.
pub fn new_id(prefix: &str) -> String {
    format!("{prefix}-{:08x}", RandomState::new().build_hasher().finish() as u32)
}

/// A line of text: spaces squeezed, trimmed, cut to `limit` characters.
pub fn clean(text: &str, limit: usize) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(limit).collect()
}

/// The string at `key`, if there is one.
pub fn text<'a>(obj: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    obj.get(key).and_then(Value::as_str)
}

/// Everything in `obj` except these keys (what this version does not know, kept to be written back).
pub fn rest(obj: &Map<String, Value>, known: &[&str]) -> Map<String, Value> {
    obj.iter().filter(|(k, _)| !known.contains(&k.as_str())).map(|(k, v)| (k.clone(), v.clone())).collect()
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::settings::tests::tempdir;

    #[test]
    fn a_missing_broken_or_odd_file_reads_as_empty() {
        let dir = tempdir("userdata-read");
        assert!(read(&dir.join("none.json")).is_empty());
        std::fs::write(dir.join("a.json"), "{not json").unwrap();
        std::fs::write(dir.join("b.json"), "[1,2]").unwrap();
        std::fs::write(dir.join("c.json"), "\u{feff}{\"x\":1}").unwrap();
        assert!(read(&dir.join("a.json")).is_empty() && read(&dir.join("b.json")).is_empty());
        assert_eq!(read(&dir.join("c.json")).get("x"), Some(&Value::from(1)), "a byte order mark is ignored");
    }

    #[test]
    fn a_file_is_written_whole_with_a_version_and_leaves_no_temporary_one_behind() {
        let dir = tempdir("userdata-write");
        let file = dir.join("notes.json");
        let mut data = Map::new();
        data.insert("notes".into(), Value::Array(vec![]));
        write(&file, data.clone()).unwrap();
        write(&file, data).unwrap();
        let back = read(&file);
        assert_eq!(back.get("version"), Some(&Value::from(VERSION)));
        assert!(std::fs::read_to_string(&file).unwrap().ends_with("}\n"));
        let left: Vec<_> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(left, ["notes.json"]);
        assert!(write(&dir.join("gone").join("x.json"), Map::new()).unwrap_err().contains("could not write"));
    }

    #[test]
    fn the_stamp_moves_when_the_file_does() {
        let dir = tempdir("userdata-stamp");
        let file = dir.join("m.json");
        assert_eq!(stamp(&file), None);
        write(&file, Map::new()).unwrap();
        let first = stamp(&file);
        assert!(first.is_some());
        std::fs::write(&file, "{\"a\":\"a longer file than before\"}").unwrap();
        assert_ne!(stamp(&file), first);
    }

    #[test]
    fn ids_are_short_and_do_not_repeat() {
        let ids: Vec<String> = (0..50).map(|_| new_id("r")).collect();
        assert!(ids.iter().all(|i| i.len() == 10 && i.starts_with("r-")));
        let mut unique = ids.clone();
        unique.sort();
        unique.dedup();
        assert_eq!(unique.len(), 50);
    }

    #[test]
    fn text_is_squeezed_and_cut() {
        assert_eq!(clean("  Call   mom \n now ", 100), "Call mom now");
        assert_eq!(clean("abcdef", 3), "abc");
        assert_eq!(beside(Path::new("C:/x/settings.json"), REMINDERS_FILE), PathBuf::from("C:/x/reminders.json"));
    }
}

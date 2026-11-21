//! The new pages driven the way the window drives them: reminders, notes and tasks, playlists, the talk key, all saved to real files and
//! followed by the app as Orbit changes them.

use std::path::PathBuf;

use orbit_ui::app::{App, Page};
use orbit_ui::datetime::Stamp;
use orbit_ui::editor::{Section, SettingsState};
use orbit_ui::reminders::Repeat;
use orbit_ui::settings;
use orbit_ui::userdata;
use orbit_ui::watcher::{self, Watcher};
use rune::{Key, KeyEvent, Modifier, Mods, PointerButton};
use serde_json::json;

fn folder(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("orbit-ui-pages-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// An app whose settings and data files are in a fresh folder, holding the example files.
fn app_with_files(tag: &str) -> (App, PathBuf, Watcher) {
    let dir = folder(tag);
    let settings_file = dir.join("settings.json");
    std::fs::write(&settings_file, include_str!("../../tests/fixtures/settings_example.json")).unwrap();
    std::fs::write(dir.join("reminders.json"), include_str!("../../tests/fixtures/reminders_example.json")).unwrap();
    std::fs::write(dir.join("notes.json"), include_str!("../../tests/fixtures/notes_example.json")).unwrap();
    std::fs::write(dir.join("music.json"), include_str!("../../tests/fixtures/music_example.json")).unwrap();
    let watcher = Watcher::default();
    let app = App::default().with_settings(SettingsState::open(settings_file.clone())).with_data(&settings_file).with_watcher(watcher.clone());
    (app, dir, watcher)
}

fn key(key: Key) -> KeyEvent {
    KeyEvent { key, mods: Mods::default() }
}

// ---- the talk key: a key, or any of the five mouse buttons ----

#[test]
fn each_mouse_button_pressed_while_recording_becomes_the_talk_key_in_the_file_orbit_reads() {
    let (mut app, dir, _) = app_with_files("mouse");
    app.open_settings(Section::General);
    for (button, name) in [(PointerButton::Primary, "mouse1"), (PointerButton::Secondary, "mouse2"), (PointerButton::Middle, "mouse3"), (PointerButton::Back, "mouse4"), (PointerButton::Forward, "mouse5")] {
        app.prefs.begin_record_talk();
        assert!(app.record_button(button), "{name}: the press was for the recorder");
        assert_eq!(settings::load(&dir.join("settings.json")).settings.hotkeys.talk, name);
        assert!(!app.prefs.recording_talk, "recording is done once a button took");
    }
    assert!(!app.record_button(PointerButton::Forward), "not recording: a press is just a press");
    assert_eq!(settings::load(&dir.join("settings.json")).settings.commands.len(), 2, "nothing else in the file was touched");
}

#[test]
fn a_button_orbit_cannot_use_keeps_recording_and_says_so() {
    let (mut app, _, _) = app_with_files("odd-button");
    app.prefs.begin_record_talk();
    assert!(app.record_button(PointerButton::Other(9)));
    assert!(app.prefs.recording_talk && !app.prefs.notice.as_ref().unwrap().good);
}

#[test]
fn a_key_pressed_while_recording_is_taken_and_one_that_would_type_is_refused() {
    let (mut app, dir, _) = app_with_files("talk-key");
    app.prefs.begin_record_talk();
    let ctrl_right = KeyEvent { key: Key::Modifier { modifier: Modifier::ControlRight, pressed: true }, mods: Mods { ctrl: true, cmd: true, ..Default::default() } };
    assert!(app.record_key(&ctrl_right, None));
    assert_eq!(settings::load(&dir.join("settings.json")).settings.hotkeys.talk, "ctrl_r");

    app.prefs.begin_record_talk();
    assert!(app.record_key(&key(Key::Text("a".into())), Some('a')), "every key is for the recorder");
    assert!(app.prefs.recording_talk, "a letter is refused and recording goes on");
    assert!(app.prefs.notice.as_ref().unwrap().text.contains("typed into every app"));
    assert!(app.record_key(&key(Key::Function(10)), None));
    assert_eq!(settings::load(&dir.join("settings.json")).settings.hotkeys.talk, "f10");

    app.prefs.begin_record_talk();
    assert!(app.record_key(&key(Key::Escape), None));
    assert!(!app.prefs.recording_talk);
    assert_eq!(settings::load(&dir.join("settings.json")).settings.hotkeys.talk, "f10", "Esc changes nothing");
}

// ---- reminders ----

#[test]
fn the_bell_counts_what_went_off_until_the_reminders_page_is_looked_at() {
    let (mut app, dir, _) = app_with_files("bell");
    assert_eq!(app.unread(), 1, "'Stretch' went off after the notifications were last looked at");
    app.go(Page::Reminders);
    assert_eq!(app.unread(), 0);
    let saved = userdata::read(&dir.join("reminders.json"));
    let seen = saved.get("seen").and_then(|s| s.as_str()).and_then(Stamp::parse).expect("remembered in the file");
    assert!(seen >= Stamp::parse("2026-11-22T08:00").unwrap(), "never earlier than the newest reminder that went off");
    assert_eq!(saved["reminders"].as_array().unwrap().len(), 3, "the reminders themselves were not touched");
}

#[test]
fn a_reminder_said_aloud_by_orbit_appears_on_the_page_when_the_file_changes() {
    let (mut app, dir, watcher) = app_with_files("voice-reminder");
    let file = dir.join("reminders.json");
    let mut data = userdata::read(&file);
    data.get_mut("reminders").unwrap().as_array_mut().unwrap().push(json!({"id": "r-voice", "title": "Said aloud", "due": "2026-11-23T10:00"}));
    userdata::write(&file, data).unwrap();
    assert_eq!(app.reminders.data.items.len(), 3, "not looked at yet");
    watcher.mark(watcher::REMINDERS);
    assert!(app.advance(1.0), "the page changed");
    assert_eq!(app.reminders.data.items.len(), 4);
    assert!(!app.advance(1.1), "and nothing more changed");
}

#[test]
fn a_reminder_that_orbit_announces_refreshes_the_list_and_is_remembered_for_the_page() {
    let (mut app, dir, _) = app_with_files("announce");
    let file = dir.join("reminders.json");
    let mut data = userdata::read(&file);
    data.get_mut("history").unwrap().as_array_mut().unwrap().push(json!({"id": "r-3f9a1c2b", "title": "Call mom", "at": "2026-11-22T17:00"}));
    userdata::write(&file, data).unwrap();
    app.on_event(orbit_ui::link::Event::Message(r#"{"type":"reminder","id":"r-3f9a1c2b","title":"Call mom","text":"Reminder: Call mom."}"#.to_owned()), 1.0);
    assert_eq!(app.reminder_said.as_deref(), Some("Reminder: Call mom."));
    assert_eq!(app.unread(), 2, "the bell counts it without waiting for the file watcher");
}

#[test]
fn a_reminder_made_on_the_page_goes_in_the_file_with_the_repeat_chosen() {
    let (mut app, dir, _) = app_with_files("make-reminder");
    app.go(Page::Reminders);
    let now = Stamp::now();
    app.reminders.new_draft(&now);
    let d = app.reminders.draft.as_mut().unwrap();
    d.title.replace_text("Stand up");
    d.day.replace_text("tomorrow");
    d.time.replace_text("10:30");
    d.repeat = Repeat::Weekdays;
    assert!(app.reminders.save_draft(&now));
    let saved = userdata::read(&dir.join("reminders.json"));
    let made = saved["reminders"].as_array().unwrap().iter().find(|r| r["title"] == "Stand up").unwrap();
    assert_eq!((made["repeat"].as_str(), made["enabled"].as_bool()), (Some("weekdays"), Some(true)));
    assert!(made["due"].as_str().unwrap().ends_with("T10:30"));
}

// ---- notes and tasks ----

#[test]
fn what_is_typed_in_a_note_is_saved_a_moment_after_the_typing_stops_and_when_the_page_is_left() {
    let (mut app, dir, _) = app_with_files("autosave");
    let on_disk = |id: &str| userdata::read(&dir.join("notes.json"))["notes"].as_array().unwrap().iter().find(|n| n["id"] == id).unwrap()["body"].as_str().unwrap().to_owned();
    app.go(Page::Notes);
    app.notes.open_note("n-gym");
    app.notes.body.replace_text("Monday legs\nTuesday rest");
    assert!(!app.advance(5.0), "just typed: not saved yet");
    assert!(app.next_wake(5.0).is_some_and(|due| (due - 5.8).abs() < 1e-9), "the window wakes itself to save");
    assert!(on_disk("n-gym").starts_with("Monday legs\nWednesday"));
    assert!(app.advance(5.9), "left alone long enough: saved");
    assert_eq!(on_disk("n-gym"), "Monday legs\nTuesday rest");
    assert_eq!(app.next_wake(6.0), None, "and nothing is waited for any more");

    app.notes.body.replace_text("changed again");
    app.advance(7.0);
    app.go(Page::Home);
    assert_eq!(on_disk("n-gym"), "changed again", "leaving the page saves at once");
}

#[test]
fn a_task_ticked_on_the_page_and_a_note_added_by_voice_are_both_in_the_file() {
    let (mut app, dir, watcher) = app_with_files("tasks");
    assert!(app.notes.toggle_task("t-milk"));
    let file = dir.join("notes.json");
    let mut data = userdata::read(&file);
    data.get_mut("notes").unwrap().as_array_mut().unwrap().push(json!({"id": "n-voice", "title": "Said aloud"}));
    userdata::write(&file, data).unwrap();
    watcher.mark(watcher::NOTES);
    app.advance(1.0);
    assert!(app.notes.data.notes.iter().any(|n| n.id == "n-voice"));
    let tasks = &userdata::read(&file)["tasks"];
    assert_eq!(tasks[0]["done"], true, "the tick was saved");
}

// ---- music ----

#[test]
fn a_link_pasted_on_the_music_page_is_saved_and_the_name_orbit_looks_up_appears_by_itself() {
    let (mut app, dir, watcher) = app_with_files("music");
    app.go(Page::Music);
    app.music.link.replace_text("https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd");
    assert!(app.music.import(&Stamp::now()));
    let file = dir.join("music.json");
    let mut data = userdata::read(&file);
    let made = data["playlists"].as_array_mut().unwrap().last_mut().unwrap();
    assert_eq!(made["name"], "");
    made["name"] = json!("Focus Flow");
    userdata::write(&file, data).unwrap();
    watcher.mark(watcher::MUSIC);
    assert!(app.advance(1.0));
    assert_eq!(app.music.data.playlists.last().unwrap().name, "Focus Flow");
}

#[test]
fn the_play_button_needs_orbit_and_says_so_when_it_is_not_there() {
    let (mut app, _, _) = app_with_files("play");
    let id = app.music.data.playlists[0].id.clone();
    assert!(!app.play_playlist(&id));
    assert!(!app.music.notice.as_ref().unwrap().good && app.music.notice.as_ref().unwrap().text.contains("not running"));
    assert!(!app.play_playlist("p-gone"), "a playlist that is not saved is not played");
    assert!(!app.open_in_spotify("p-gone"));
}

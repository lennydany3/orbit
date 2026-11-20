//! The Settings page driven the way the window drives it: keys recorded, changes saved to a real file, the face following.

use orbit_ui::app::{App, Page};
use orbit_ui::editor::{Field, Section, SettingsState, StepKind};
use orbit_ui::settings;
use rune::{Key, KeyEvent, Mods};

const EXAMPLE: &str = include_str!("../../tests/fixtures/settings_example.json");

fn folder(tag: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("orbit-ui-page-{tag}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn app_with_file(tag: &str) -> (App, std::path::PathBuf) {
    let file = folder(tag).join("settings.json");
    std::fs::write(&file, EXAMPLE).unwrap();
    (App::default().with_settings(SettingsState::open(file.clone())), file)
}

fn ctrl_alt(c: char) -> KeyEvent {
    KeyEvent { key: Key::Command(c), mods: Mods { ctrl: true, cmd: true, alt: true, ..Default::default() } }
}

#[test]
fn the_window_starts_on_the_sphere_and_the_page_comes_and_goes() {
    let mut app = App::default();
    assert_eq!(app.page, Page::Stage);
    app.open_settings(Section::Commands);
    assert_eq!((app.page, app.prefs.section), (Page::Settings, Section::Commands));
    app.prefs.new_command();
    app.close_settings();
    assert_eq!(app.page, Page::Stage);
    assert!(app.prefs.draft.is_none(), "a half-made command is dropped on the way out");
}

#[test]
fn keys_are_only_taken_while_a_hotkey_is_being_recorded() {
    let (mut app, _) = app_with_file("keys");
    assert!(!app.record_key(&ctrl_alt('k'), None), "not recording: the key is not for the page");
    app.prefs.begin_record(Field::Yes);
    assert!(app.record_key(&ctrl_alt('k'), None));
    assert_eq!(app.prefs.hotkey(Field::Yes), "ctrl+alt+k");
    assert_eq!(app.prefs.recording, None, "done once it took");
}

#[test]
fn a_recorded_hotkey_is_saved_and_the_orb_card_shows_it() {
    let (mut app, file) = app_with_file("saved");
    app.prefs.begin_record(Field::No);
    app.record_key(&ctrl_alt('m'), None);
    assert_eq!(settings::load(&file).settings.hotkeys.no, "ctrl+alt+m");
    assert_eq!(app.face().no, "ctrl+alt+m", "the orb's hint and its chord follow");
    assert_eq!(app.face().yes, "ctrl+alt+y");
    assert_ne!(app.face().key(), orbit_ui::face::Face::new(None).key(), "so the card is redrawn");
}

#[test]
fn a_chord_that_is_taken_keeps_recording_so_another_can_be_tried() {
    let (mut app, file) = app_with_file("taken");
    app.prefs.begin_record(Field::Orb);
    assert!(app.record_key(&ctrl_alt('y'), None), "that is the yes chord");
    assert_eq!(app.prefs.recording, Some(Field::Orb));
    assert!(!app.prefs.notice.as_ref().unwrap().good);
    assert_eq!(settings::load(&file).settings.hotkeys.orb, "ctrl+alt+o", "nothing was saved");
    assert!(app.record_key(&ctrl_alt('q'), None));
    assert_eq!((app.prefs.recording, app.prefs.hotkey(Field::Orb)), (None, "ctrl+alt+q"));
}

#[test]
fn escape_stops_recording_and_a_bare_modifier_does_not() {
    let (mut app, _) = app_with_file("esc");
    app.prefs.begin_record(Field::Orb);
    let ctrl = KeyEvent { key: Key::Modifier { modifier: rune::Modifier::ControlLeft, pressed: true }, mods: Mods { ctrl: true, cmd: true, ..Default::default() } };
    assert!(app.record_key(&ctrl, None));
    assert_eq!(app.prefs.recording, Some(Field::Orb));
    assert!(app.record_key(&KeyEvent { key: Key::Escape, mods: Mods::default() }, None));
    assert_eq!((app.prefs.recording, app.prefs.hotkey(Field::Orb)), (None, "ctrl+alt+o"));
}

#[test]
fn resetting_from_the_page_puts_the_defaults_back_on_the_orb_too() {
    let (mut app, file) = app_with_file("reset");
    app.change_hotkey(Field::Yes, "ctrl+alt+k");
    app.reset_hotkeys();
    assert_eq!(settings::load(&file).settings.hotkeys, settings::Hotkeys::default());
    assert_eq!(app.face().yes, settings::DEFAULT_YES);
}

#[test]
fn a_command_made_on_the_page_is_in_the_file_orbit_reads() {
    let (mut app, file) = app_with_file("command");
    app.open_settings(Section::Commands);
    app.prefs.new_command();
    app.prefs.name_edit().replace_text("Screenshot");
    app.prefs.phrase_edit(0).replace_text("take a screenshot");
    app.prefs.set_step_kind(0, StepKind::Keys);
    app.prefs.step_edit(0).replace_text("Win+Shift+S");
    assert!(app.prefs.save_draft());
    let saved = settings::load(&file).settings;
    let made = saved.commands.last().unwrap();
    assert_eq!((made.name.as_str(), made.phrases.clone(), made.describe()), ("Screenshot", vec!["take a screenshot".to_owned()], "press shift+win+s".to_owned()));
    assert_eq!(saved.commands.len(), 3);
}

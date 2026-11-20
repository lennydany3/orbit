//! The Settings page driven the way the window drives it: keys recorded, changes saved to a real file, the face following.

use orbit_ui::app::{App, Page};
use orbit_ui::editor::{Field, Section, SettingsState, StepKind};
use orbit_ui::settings::{self, Sounds};
use orbit_ui::sound::Cue;
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

// ---- sounds ----

#[test]
fn a_change_made_on_the_sounds_page_is_in_the_file_and_can_be_heard() {
    let (mut app, file) = app_with_file("sounds");
    app.open_settings(Section::Sounds);
    assert_eq!(app.prefs.sounds().enabled, false, "the example file has them off");

    app.set_sounds_enabled(true);
    assert_eq!(settings::load(&file).settings.sounds, Sounds { enabled: true, volume: 65 });
    assert_eq!(app.shell.player.played, [(Cue::Up, 65)], "switching on plays the pop-up tone as a sample");

    app.prefs.drag_volume(20);
    app.finish_volume(true);
    assert_eq!(settings::load(&file).settings.sounds.volume, 20);
    assert_eq!(app.shell.player.played.last(), Some(&(Cue::Up, 20)), "and moving the volume plays it again, at the new volume");
    assert_eq!(app.shell.player.played.len(), 2);
}

#[test]
fn nothing_plays_for_a_change_that_is_not_one_or_while_the_sounds_are_off() {
    let (mut app, file) = app_with_file("quiet");
    app.set_sounds_enabled(false);
    assert!(app.shell.player.played.is_empty(), "switching off is silent");
    app.step_volume(1);
    assert_eq!(settings::load(&file).settings.sounds.volume, 75);
    assert!(app.shell.player.played.is_empty(), "the volume of sounds that are off can be set, but there is nothing to hear");
    app.prefs.drag_volume(10);
    app.finish_volume(false);
    app.prefs.drag_volume(75);
    app.finish_volume(true);
    assert!(app.shell.player.played.is_empty(), "a cancelled drag and a drag that ended where it began save nothing and play nothing");
}

#[test]
fn the_volume_slider_at_zero_saves_but_makes_no_sound() {
    let (mut app, file) = app_with_file("zero");
    app.set_sounds_enabled(true);
    app.prefs.drag_volume(0);
    app.finish_volume(true);
    assert_eq!(settings::load(&file).settings.sounds, Sounds { enabled: true, volume: 0 });
    assert_eq!(app.shell.player.played.len(), 1, "only the switch's sample played");
}

#[test]
fn resetting_the_sounds_goes_back_to_on_and_quiet_and_lets_you_hear_it() {
    let (mut app, file) = app_with_file("reset-sounds");
    app.reset_sounds();
    assert_eq!(settings::load(&file).settings.sounds, Sounds::default());
    assert_eq!(app.shell.player.played, [(Cue::Up, 40)]);
}

#[test]
fn changing_the_sounds_leaves_hotkeys_and_commands_as_they_were() {
    let (mut app, file) = app_with_file("sounds-only");
    app.set_sounds_enabled(true);
    app.step_volume(-1);
    let saved = settings::load(&file).settings;
    assert_eq!((saved.hotkeys.talk.as_str(), saved.commands.len()), ("f9", 2));
}

//! The Settings page driven the way the window drives it: keys recorded, changes saved to a real file, the face following.

use orbit_ui::app::{App, Page};
use orbit_ui::editor::{Field, Section, SettingsState, StepKind};
use orbit_ui::settings::{self, OrbLook, OrbSize, Sounds};
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
fn the_window_starts_on_the_sphere_and_the_pages_come_and_go() {
    let mut app = App::default();
    assert_eq!(app.page, Page::Home);
    app.go(Page::Commands);
    app.prefs.new_command();
    assert_eq!((app.page, app.prefs.draft.is_some()), (Page::Commands, true));
    app.go(Page::Home);
    assert_eq!(app.page, Page::Home);
    assert!(app.prefs.draft.is_none(), "a half-made command is dropped on the way out");
    app.open_settings(Section::Sounds);
    assert_eq!((app.page, app.prefs.section), (Page::Settings, Section::Sounds));
    app.close_settings();
    assert_eq!(app.page, Page::Home);
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
    app.go(Page::Commands);
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

// ---- the orb's place and size ----

#[test]
fn the_orb_is_on_screen_for_as_long_as_the_customization_page_is_open() {
    let (mut app, _) = app_with_file("preview");
    app.sync_shell(1.0);
    assert!(!app.previewing());
    app.open_settings(Section::Hotkeys);
    assert!(!app.previewing(), "only the customization page shows the orb");
    app.prefs.select(Section::Customization);
    assert!(app.previewing());
    assert!(app.presence.visible(app.active() || app.previewing(), 2.0));
    app.close_settings();
    assert!(!app.previewing(), "leaving the page lets the orb go (after its short linger)");
}

#[test]
fn a_dragged_orb_follows_the_pointer_and_is_saved_where_it_is_let_go() {
    let (mut app, file) = app_with_file("drag-orb");
    app.open_settings(Section::Customization);
    let home = app.face().place;
    app.prefs.drag_orb(0.1, 0.2);
    let during = app.face().place;
    assert_ne!(during, home, "the real orb moves while the pointer is still down");
    assert_eq!(settings::load(&file).settings.orb, OrbLook::default(), "nothing saved yet");
    assert!(app.prefs.finish_orb(true));
    assert_eq!(settings::load(&file).settings.orb, OrbLook { x: 0.1, y: 0.2, size: OrbSize::Medium });
    assert_eq!(app.face().place, during, "and stays where it was let go");
}

#[test]
fn escape_during_a_drag_puts_the_orb_back() {
    let (mut app, file) = app_with_file("drag-cancel");
    app.open_settings(Section::Customization);
    let home = app.face().place;
    app.prefs.drag_orb(0.5, 0.5);
    assert!(!app.prefs.finish_orb(false));
    assert_eq!(app.face().place, home);
    assert_eq!(settings::load(&file).settings.orb, OrbLook::default());
}

#[test]
fn a_spot_a_size_and_a_reset_are_each_saved_and_the_rest_of_the_file_is_untouched() {
    let (mut app, file) = app_with_file("spots");
    app.open_settings(Section::Customization);
    assert_eq!(app.prefs.spot_name(), Some("Bottom right"), "out of the box");
    assert!(app.prefs.place_orb(0.5, 0.0));
    assert_eq!(app.prefs.spot_name(), Some("Top"));
    assert!(app.prefs.set_orb_size(OrbSize::Large));
    let saved = settings::load(&file).settings;
    assert_eq!(saved.orb, OrbLook { x: 0.5, y: 0.0, size: OrbSize::Large });
    assert_eq!((saved.hotkeys.talk.as_str(), saved.commands.len(), saved.sounds), ("f9", 2, Sounds { enabled: false, volume: 65 }));
    assert!(!app.prefs.place_orb(0.5, 0.0), "pressing the spot the orb is on changes nothing");
    assert!(app.prefs.reset_orb());
    assert_eq!(settings::load(&file).settings.orb, OrbLook::default());
    let written: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&file).unwrap()).unwrap();
    assert!(written.get("orb").is_none(), "a default orb leaves no entry in the file");
}

#[test]
fn the_orbs_window_is_laid_out_for_the_place_and_size_chosen() {
    let (mut app, _) = app_with_file("face-place");
    let corner = app.face();
    app.prefs.place_orb(0.0, 0.0);
    app.prefs.set_orb_size(OrbSize::Small);
    let face = app.face();
    assert_ne!(face.key(), corner.key(), "so the window is redrawn and moved");
    assert_eq!(face.place.diameter, 80.0);
    assert!(face.place.flip, "in the top half the question opens below the orb");
    assert_eq!(face.size(), (orbit_ui::face::WIDTH, 80.0 + 28.0));
    let (x, y) = face.place.at.unwrap();
    assert!(x >= 0.0 && y >= 0.0, "on the screen");
}

#[test]
fn an_open_question_keeps_the_orb_where_it_was() {
    let (mut app, _) = app_with_file("ask-place");
    app.prefs.place_orb(0.5, 1.0);
    let calm = app.face().place;
    app.on_event(orbit_ui::link::Event::Status(orbit_ui::link::Status::Open), 0.0);
    app.on_event(orbit_ui::link::Event::Message(r#"{"type":"confirm","id":1,"question":"q","action":"close window Notepad"}"#.to_owned()), 0.0);
    let asking = app.face();
    assert_eq!(asking.size().1, calm.diameter + 28.0 + orbit_ui::face::ASK_EXTRA);
    assert_eq!(asking.place.orb_x, calm.orb_x, "side to side the orb has not moved");
    assert_eq!(asking.place.at.unwrap().1 + asking.size().1, calm.at.unwrap().1 + calm.diameter + 28.0, "and its bottom edge stayed put");
}

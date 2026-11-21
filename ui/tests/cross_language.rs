//! The window writes settings.json and Orbit (Python) reads it: here the real Python reads a file the real page wrote, and says
//! what it understood. Skipped (loudly) when the project's Python environment is not there.

use std::process::Command;

use orbit_ui::editor::{Field, SettingsState, StepKind};
use orbit_ui::spawn::{project_root, python_for};

#[test]
fn what_the_page_saves_is_what_orbit_understands() {
    let root = project_root();
    let python = python_for(&root);
    if !python.exists() {
        eprintln!("SKIPPED: no Python at {}", python.display());
        return;
    }
    let dir = std::env::temp_dir().join(format!("orbit-ui-cross-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let file = dir.join("settings.json");

    let mut page = SettingsState::open(file.clone());
    assert!(page.set_talk("f9"));
    assert!(page.apply_hotkey(Field::Orb, "ctrl+alt+p".into()));
    assert!(page.set_sounds_enabled(false));
    page.drag_volume(55);
    assert!(page.finish_volume(true));
    page.new_command();
    page.name_edit().replace_text("Caf\u{e9} time!");
    page.phrase_edit(0).replace_text("  Caf\u{e9}   TIME!  ");
    page.step_edit(0).replace_text("C:\\Users\\Public\\Documents");
    page.add_step(StepKind::Wait);
    page.step_edit(1).replace_text("2,5");
    page.add_step(StepKind::Keys);
    page.step_edit(2).replace_text("Win+Shift+S");
    page.add_step(StepKind::Type);
    page.step_edit(3).replace_text("say \"hi\"\tthere \u{1f600}");
    assert!(page.save_draft(), "{:?}", page.draft.as_ref().map(|d| d.errors.clone()));

    let script = "import json,sys; from pathlib import Path; from orbit import settings; \
                  s = settings.load(Path(sys.argv[1])); \
                  print(json.dumps({'problems': s.problems, 'hotkeys': s.hotkeys, 'sounds': [s.sounds.enabled, s.sounds.volume], 'commands': \
                  [{'name': c.name, 'phrases': c.phrases, 'describe': c.describe(), 'enabled': c.enabled, 'keys': c.sends_keys} for c in s.commands]}, ensure_ascii=True))";
    let out = Command::new(&python)
        .args(["-I", "-c", script])
        .arg(&file)
        .env("PYTHONPATH", root.join("src"))
        .env("PYTHONIOENCODING", "utf-8")
        .output()
        .expect("run python");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let seen: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();

    assert_eq!(seen["problems"], serde_json::json!([]), "Orbit read all of it");
    assert_eq!(seen["hotkeys"]["talk"], "f9");
    assert_eq!(seen["hotkeys"]["orb"], "ctrl+alt+p");
    assert_eq!(seen["sounds"], serde_json::json!([false, 55]), "the switch and the volume the page saved");
    let c = &seen["commands"][0];
    assert_eq!(c["name"], "Caf\u{e9} time!");
    assert_eq!(c["phrases"], serde_json::json!(["caf\u{e9} time"]), "both sides compare phrases the same way");
    assert_eq!(c["enabled"], true);
    assert_eq!(c["keys"], true);
    assert_eq!(
        c["describe"],
        "open C:\\Users\\Public\\Documents, then wait 2.5 seconds, then press shift+win+s, then type \"say \"hi\"\tthere \u{1f600}\"",
        "and describe a command in the same words the page does"
    );
    assert_eq!(page.settings.commands[0].describe(), c["describe"].as_str().unwrap());
    let _ = std::fs::remove_dir_all(&dir);
}

fn python_json(python: &std::path::Path, root: &std::path::Path, script: &str, args: &[&std::path::Path]) -> serde_json::Value {
    let out = Command::new(python)
        .args(["-I", "-c", script])
        .args(args)
        .env("PYTHONPATH", root.join("src"))
        .env("PYTHONIOENCODING", "utf-8")
        .output()
        .expect("run python");
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    serde_json::from_slice(&out.stdout).unwrap_or_else(|e| panic!("{e}: {}", String::from_utf8_lossy(&out.stdout)))
}

#[test]
fn the_audio_voice_and_mouse_choices_the_page_saves_are_what_orbit_understands() {
    use orbit_ui::editor::{talk_from_button, Section};
    let root = project_root();
    let python = python_for(&root);
    if !python.exists() {
        eprintln!("SKIPPED: no Python at {}", python.display());
        return;
    }
    let dir = std::env::temp_dir().join(format!("orbit-ui-cross-audio-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let file = dir.join("settings.json");

    let mut page = SettingsState::open(file.clone());
    page.select(Section::General);
    assert!(page.set_talk(talk_from_button(rune::PointerButton::Forward).unwrap()));
    assert!(page.set_mic("Microphone (USB Audio)") && page.set_speaker("Speakers (Echo Pop)"));
    page.drag_speaking(89);
    assert!(page.finish_speaking(true));
    assert!(page.set_wake_word(false) && page.set_keep_listening(false));

    let script = "import json,sys; from pathlib import Path; from orbit import settings; s = settings.load(Path(sys.argv[1])); \
                  print(json.dumps({'problems': s.problems, 'talk': s.talk_key, 'audio': [s.audio.mic, s.audio.speaker, s.audio.volume], \
                  'voice': [s.voice.wake_word, s.voice.keep_listening]}))";
    let seen = python_json(&python, &root, script, &[&file]);
    assert_eq!(seen["problems"], serde_json::json!([]), "Orbit read all of it");
    assert_eq!(seen["talk"], "mouse5", "a mouse button is a talk key Orbit's keyboard listener accepts");
    assert_eq!(seen["audio"], serde_json::json!(["Microphone (USB Audio)", "Speakers (Echo Pop)", 89]));
    assert_eq!(seen["voice"], serde_json::json!([false, false]));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn what_the_pages_save_for_reminders_notes_and_music_is_what_orbit_understands_and_the_other_way_round() {
    use orbit_ui::datetime::Stamp;
    use orbit_ui::music::MusicState;
    use orbit_ui::notes::NotesState;
    use orbit_ui::reminders::{RemindersState, Repeat};
    let root = project_root();
    let python = python_for(&root);
    if !python.exists() {
        eprintln!("SKIPPED: no Python at {}", python.display());
        return;
    }
    let dir = std::env::temp_dir().join(format!("orbit-ui-cross-data-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let (rem, notes, music) = (dir.join("reminders.json"), dir.join("notes.json"), dir.join("music.json"));
    let now = Stamp::parse("2026-11-22T14:30").unwrap();

    // the window writes...
    let mut r = RemindersState::open(rem.clone());
    r.new_draft(&now);
    {
        let d = r.draft.as_mut().unwrap();
        d.title.replace_text("Caf\u{e9}  time ");
        d.day.replace_text("tomorrow");
        d.time.replace_text("9am");
        d.repeat = Repeat::Weekly;
    }
    assert!(r.save_draft(&now));
    let mut n = NotesState::open(notes.clone());
    n.new_note(&now);
    n.title.replace_text("Gym plan");
    n.body.replace_text("Monday legs\nWednesday push");
    assert!(n.flush());
    n.new_task.replace_text("Buy milk");
    assert!(n.add_task(&now));
    let mut m = MusicState::open(music.clone());
    m.link.replace_text("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc");
    assert!(m.import(&now));

    // ...and Orbit reads
    let script = "import json,sys; from pathlib import Path; from orbit import userdata; \
                  from orbit.reminders import ReminderStore; from orbit.notes import NotesStore; from orbit.playlists import MusicStore; \
                  r = ReminderStore(Path(sys.argv[1])); n = NotesStore(Path(sys.argv[2])); m = MusicStore(Path(sys.argv[3])); \
                  print(json.dumps({'reminders': [[x.title, userdata.to_text(x.due), x.repeat, x.enabled] for x in r.reminders], \
                  'notes': [[x.title, x.body] for x in n.notes], 'tasks': [[x.text, x.done] for x in n.tasks], \
                  'playlists': [[p.kind, p.spotify_id, p.name, p.link.uri] for p in m.playlists]}, ensure_ascii=True))";
    let seen = python_json(&python, &root, script, &[&rem, &notes, &music]);
    assert_eq!(seen["reminders"], serde_json::json!([["Caf\u{e9} time", "2026-11-23T09:00", "weekly", true]]));
    assert_eq!(seen["notes"], serde_json::json!([["Gym plan", "Monday legs\nWednesday push"]]));
    assert_eq!(seen["tasks"], serde_json::json!([["Buy milk", false]]));
    assert_eq!(seen["playlists"], serde_json::json!([["playlist", "37i9dQZF1DXcBWIGoYBM5M", "", "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"]]));

    // Orbit writes (a reminder said aloud, a note, a task, and the name Spotify gave the playlist), and the window reads
    let script = "import sys; from datetime import datetime; from pathlib import Path; \
                  from orbit.reminders import ReminderStore; from orbit.notes import NotesStore; from orbit.playlists import MusicStore; \
                  r = ReminderStore(Path(sys.argv[1])); r.add('Said aloud', datetime(2026, 11, 23, 17, 0), 'daily', datetime(2026, 11, 22, 14, 31)); \
                  n = NotesStore(Path(sys.argv[2])); n.add_note('The wifi is on the router.', datetime(2026, 11, 22, 14, 32)); n.add_task('Pay rent', datetime(2026, 11, 22, 14, 33)); \
                  m = MusicStore(Path(sys.argv[3])); m.set_name(m.playlists[0].id, 'Gym Mix', only_if_unnamed=True); print('{}')";
    python_json(&python, &root, script, &[&rem, &notes, &music]);
    assert!(r.reload_if_changed() && n.reload_if_changed() && m.reload_if_changed(), "the window notices each of them");
    assert_eq!(r.data.items.iter().map(|x| (x.title.as_str(), x.repeat)).collect::<Vec<_>>(), [("Caf\u{e9} time", Repeat::Weekly), ("Said aloud", Repeat::Daily)]);
    assert_eq!(r.data.items[1].due.to_text(), "2026-11-23T17:00");
    assert_eq!(n.data.notes.iter().map(|x| x.title.as_str()).collect::<Vec<_>>(), ["Gym plan", "The wifi is on the router"]);
    assert_eq!(n.data.tasks.iter().map(|x| x.text.as_str()).collect::<Vec<_>>(), ["Buy milk", "Pay rent"]);
    assert_eq!(m.data.playlists[0].name, "Gym Mix");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_shopping_list_and_a_timer_are_understood_by_both_sides_and_survive_the_windows_saves() {
    use orbit_ui::datetime::Stamp;
    use orbit_ui::notes::NotesState;
    use orbit_ui::reminders::RemindersState;
    let root = project_root();
    let python = python_for(&root);
    if !python.exists() {
        eprintln!("SKIPPED: no Python at {}", python.display());
        return;
    }
    let dir = std::env::temp_dir().join(format!("orbit-ui-cross-lists-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let (rem, notes) = (dir.join("reminders.json"), dir.join("notes.json"));
    let now = Stamp::parse("2026-11-22T14:30").unwrap();

    // Orbit writes: "add tomato, pasta and milkshake to the shopping list", "add pay rent to my tasks", a timer and an alarm
    let script = "import sys; from datetime import datetime; from pathlib import Path; \
                  from orbit.reminders import ReminderStore; from orbit.notes import NotesStore; \
                  n = NotesStore(Path(sys.argv[2])); n.add_tasks(['Tomato', 'Pasta', 'Milkshake'], datetime(2026, 11, 22, 14, 30), 'shopping'); n.add_task('Pay rent', datetime(2026, 11, 22, 14, 31)); \
                  r = ReminderStore(Path(sys.argv[1])); r.add('Pasta', datetime(2026, 11, 22, 14, 40), 'none', datetime(2026, 11, 22, 14, 30), 'timer'); \
                  r.add('Wake up', datetime(2026, 11, 23, 6, 30), 'none', datetime(2026, 11, 22, 14, 30), 'alarm'); print('{}')";
    python_json(&python, &root, script, &[&rem, &notes]);

    // the window reads them, and adds to the shopping list, ticks one, and switches the timer off
    let mut n = NotesState::open(notes.clone());
    assert_eq!(n.data.list_names(), [("".to_owned(), 1), ("shopping".to_owned(), 3)], "the plain list first");
    assert_eq!(n.data.tasks.iter().map(|t| t.list.as_str()).collect::<Vec<_>>(), ["shopping", "shopping", "shopping", ""]);
    n.select_list(Some("shopping".into()));
    n.new_task.replace_text("Bread");
    assert!(n.add_task(&now));
    let tomato = n.data.tasks.iter().find(|t| t.text == "Tomato").unwrap().id.clone();
    assert!(n.toggle_task(&tomato));
    let mut r = RemindersState::open(rem.clone());
    assert_eq!(r.data.items.iter().map(|x| x.kind()).collect::<Vec<_>>(), ["timer", "alarm"]);
    let timer = r.data.items[0].id.clone();
    assert!(r.toggle(&timer, &now));

    // Orbit reads what the window saved
    let script = "import json,sys; from pathlib import Path; from orbit.reminders import ReminderStore; from orbit.notes import NotesStore; \
                  r = ReminderStore(Path(sys.argv[1])); n = NotesStore(Path(sys.argv[2])); \
                  print(json.dumps({'tasks': [[t.text, t.list, t.done] for t in n.tasks], 'lists': n.list_names(), \
                  'reminders': [[x.title, x.kind, x.enabled] for x in r.reminders]}, ensure_ascii=True))";
    let seen = python_json(&python, &root, script, &[&rem, &notes]);
    assert_eq!(
        seen["tasks"],
        serde_json::json!([["Tomato", "shopping", true], ["Pasta", "shopping", false], ["Milkshake", "shopping", false], ["Pay rent", "", false], ["Bread", "shopping", false]])
    );
    assert_eq!(seen["lists"], serde_json::json!([["", 1], ["shopping", 3]]));
    assert_eq!(seen["reminders"], serde_json::json!([["Pasta", "timer", false], ["Wake up", "alarm", true]]), "the window switched the timer off and did not forget it is a timer");
    let _ = std::fs::remove_dir_all(&dir);
}

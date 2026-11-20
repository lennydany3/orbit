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
                  print(json.dumps({'problems': s.problems, 'hotkeys': s.hotkeys, 'commands': \
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

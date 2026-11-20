//! The sign-in entry against the real registry, under a throwaway name so the real "Orbit" entry is never touched. The entry is
//! removed whatever happens, and the test checks it is gone.

use orbit_ui::autostart;
use std::path::Path;

struct Cleanup(String);

impl Drop for Cleanup {
    fn drop(&mut self) {
        let _ = autostart::disable(&self.0);
    }
}

#[test]
fn turning_it_on_and_off_round_trips_through_the_real_registry() {
    let name = format!("OrbitUiTest-{}", std::process::id());
    let _cleanup = Cleanup(name.clone());
    let exe = Path::new(r"C:\Program Files\Orbit\orbit-ui.exe");

    assert_eq!(autostart::current(&name), None, "nothing there to begin with");
    autostart::enable(&name, exe, &["--spawn"]).expect("written to HKCU Run");
    assert_eq!(autostart::current(&name).as_deref(), Some(autostart::command_line(exe, &["--spawn"]).as_str()), "read back exactly as written");

    autostart::enable(&name, exe, &["--spawn", "--window"]).expect("writing again replaces it");
    assert!(autostart::current(&name).unwrap().contains("'--window'"));

    autostart::disable(&name).expect("removed");
    assert_eq!(autostart::current(&name), None, "gone");
    autostart::disable(&name).expect("removing what is not there is fine");
}

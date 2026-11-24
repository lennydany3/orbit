// The installer has no console window (a debug build keeps one, for its messages while working on it).
#![cfg_attr(all(not(debug_assertions), windows), windows_subsystem = "windows")]

//! Orbit's installer. Everything is in `orbit_ui::setup`.

fn main() {
    std::process::exit(orbit_ui::setup::main());
}

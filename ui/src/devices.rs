//! The microphones and speakers on this PC, for the Audio card on the Settings page. The window does not talk to the sound system
//! itself: it asks Orbit's Python (`python -m orbit.voice.devices`, which prints one line of JSON) in a background thread, so a slow
//! sound driver never freezes the window. The same Python (the project's `.venv`, or `ORBIT_PYTHON`) that Orbit itself runs on.

use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver};

use rune::Waker;
use serde_json::Value;

use crate::spawn;

/// What a scan found. Each device is listed once, by the name Orbit opens it with.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Devices {
    pub inputs: Vec<String>,
    pub outputs: Vec<String>,
    pub default_input: String,
    pub default_output: String,
}

/// The JSON `orbit.voice.devices` prints -> the lists. None if it is not that.
pub fn parse(json: &str) -> Option<Devices> {
    let v: Value = serde_json::from_str(json.trim()).ok()?;
    let names = |key: &str| -> Option<Vec<String>> { v.get(key)?.as_array()?.iter().map(|n| n.as_str().map(str::to_owned)).collect() };
    let text = |key: &str| v.get(key).and_then(Value::as_str).unwrap_or_default().to_owned();
    Some(Devices { inputs: names("inputs")?, outputs: names("outputs")?, default_input: text("default_input"), default_output: text("default_output") })
}

/// Asks Python for the lists (blocks until it answers: use `scan_in_background`).
pub fn scan() -> Result<Devices, String> {
    let root = spawn::project_root();
    let python = spawn::python_for(&root);
    if !python.exists() {
        return Err(format!("Python not found at {} (set ORBIT_PYTHON, or create the project's .venv)", python.display()));
    }
    let mut command = Command::new(&python);
    command.args(["-m", "orbit.voice.devices"]).current_dir(&root).env("PYTHONIOENCODING", "utf-8").stdin(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);                               // CREATE_NO_WINDOW: no console flashes up
    }
    let out = command.output().map_err(|e| format!("could not ask Python for the audio devices: {e}"))?;
    parse(&String::from_utf8_lossy(&out.stdout)).ok_or_else(|| "Python could not list the audio devices.".to_owned())
}

/// Scans on its own thread; the answer arrives on the receiver, and the window is woken when it does.
pub fn scan_in_background(wake: Waker) -> Receiver<Result<Devices, String>> {
    let (tx, rx) = mpsc::channel();
    std::thread::Builder::new()
        .name("audio-devices".into())
        .spawn(move || {
            let _ = tx.send(scan());
            wake();
        })
        .ok();
    rx
}

/// Where the lists stand.
#[derive(Debug, Default)]
pub enum Scan {
    #[default]
    Idle,
    Scanning(Receiver<Result<Devices, String>>),
    Failed(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn what_python_prints_is_read() {
        let json = r#"{"inputs": ["Microphone (USB Audio)", "Headset (Buds)"], "outputs": ["Speakers (Realtek)"], "default_input": "Microphone (USB Audio)", "default_output": "Speakers (Realtek)"}"#;
        let d = parse(json).unwrap();
        assert_eq!(d.inputs, ["Microphone (USB Audio)", "Headset (Buds)"]);
        assert_eq!((d.outputs.as_slice(), d.default_input.as_str(), d.default_output.as_str()), (["Speakers (Realtek)".to_owned()].as_slice(), "Microphone (USB Audio)", "Speakers (Realtek)"));
        assert_eq!(parse(&format!("  {json}\n")), Some(d), "a trailing newline is fine");
    }

    #[test]
    fn what_is_not_the_lists_is_refused() {
        for bad in ["", "nope", "[]", r#"{"inputs": "x", "outputs": []}"#, r#"{"inputs": [1], "outputs": []}"#, r#"{"outputs": []}"#] {
            assert_eq!(parse(bad), None, "{bad:?}");
        }
        let empty = parse(r#"{"inputs": [], "outputs": []}"#).unwrap();
        assert_eq!((empty.inputs.len(), empty.default_input.as_str()), (0, ""), "no sound system: empty lists, still a list");
    }
}

//! Starts Orbit itself, so opening the window is all it takes: `orbit-ui --spawn`.
//!
//! The window makes a one-off password and a free port, starts `python -m orbit --voice --ipc` with them, and connects. It
//! keeps the child's standard input open and never writes to it: when the window closes or crashes the pipe ends, and Orbit
//! (started with `--exit-with-parent`) stops cleanly on its own. So there is never an Orbit left listening with no face.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::time::{Duration, Instant};

/// What to run, separate from running it so it can be checked.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub token: String,
    pub cwd: PathBuf,
    pub url: String,
}

/// Which Orbit to start.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// The real thing: microphone, wake word, the model.
    Orbit,
    /// A scripted turn with a yes/no question, for working on the window without speaking.
    Demo,
}

/// 192 random bits as hex. The standard library seeds its hash maps from the operating system's random source, and each
/// `RandomState` is a fresh key, so hashing nothing with it gives unpredictable output.
pub fn random_token() -> String {
    (0..3).map(|_| format!("{:016x}", RandomState::new().build_hasher().finish())).collect()
}

/// A port nobody is using right now.
pub fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind(("127.0.0.1", 0))?.local_addr()?.port())
}

/// The installed engine: `engine\orbit-engine.exe` beside this program, when there is one. Only the installer puts it there, so its
/// presence is what tells an installed Orbit from one run out of the source folder.
pub fn installed_engine() -> Option<PathBuf> {
    let beside = std::env::current_exe().ok()?.parent()?.join("engine").join("orbit-engine.exe");
    beside.is_file().then_some(beside)
}

/// Is this the installed app (as opposed to `cargo run` in the project)?
pub fn is_installed() -> bool {
    installed_engine().is_some()
}

/// Where what is yours lives: `ORBIT_DATA`; for the installed app the Orbit folder in `%APPDATA%` (so an update or an uninstall
/// never touches it); from the source, the project folder. The engine is told the same place (`ORBIT_DATA`), so both sides agree.
pub fn data_dir() -> PathBuf {
    if let Some(chosen) = std::env::var_os("ORBIT_DATA") {
        return PathBuf::from(chosen);
    }
    if is_installed()
        && let Some(appdata) = std::env::var_os("APPDATA")
    {
        return PathBuf::from(appdata).join("Orbit");
    }
    project_root()
}

/// The Orbit project folder: this one's parent, unless `ORBIT_ROOT` says otherwise.
pub fn project_root() -> PathBuf {
    std::env::var_os("ORBIT_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).parent().map(Path::to_path_buf).unwrap_or_default())
}

/// `ORBIT_PYTHON`, or the project's own virtual environment.
pub fn python_for(root: &Path) -> PathBuf {
    std::env::var_os("ORBIT_PYTHON").map(PathBuf::from).unwrap_or_else(|| root.join(".venv").join("Scripts").join("python.exe"))
}

pub fn plan(kind: Kind, root: &Path, python: PathBuf, port: u16, token: String) -> Plan {
    let mut args: Vec<String> = ["-m"].map(String::from).into();
    match kind {
        Kind::Orbit => args.extend(["orbit", "--voice", "--ipc", "--ipc-port"].map(String::from)),
        Kind::Demo => args.extend(["orbit.ipc.demo", "--confirm", "--port"].map(String::from)),
    }
    args.extend([port.to_string(), "--exit-with-parent".to_owned()]);
    let url = format!("ws://127.0.0.1:{port}/?token={token}");
    Plan { program: python, args, token, cwd: root.to_path_buf(), url }
}

/// Starts the engine with no console window, and sends what it prints to `logs\engine.log` (the window itself has no console, so
/// without this the engine's output would have nowhere to go).
#[cfg(windows)]
fn quiet(command: &mut Command, data: &Path) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
    let logs = data.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    let log = std::fs::File::create(logs.join("engine.log")).ok();
    match log.and_then(|f| f.try_clone().ok().map(|g| (f, g))) {
        Some((out, err)) => {
            command.stdout(Stdio::from(out)).stderr(Stdio::from(err));
        }
        None => {
            command.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }
}

#[cfg(not(windows))]
fn quiet(_command: &mut Command, _data: &Path) {}

/// A running Orbit that this window started.
pub struct Spawned {
    child: Child,
    stdin: Option<ChildStdin>,
    pub url: String,
}

impl Spawned {
    pub fn start(kind: Kind) -> Result<Spawned, String> {
        let port = free_port().map_err(|e| format!("no free port: {e}"))?;
        let data = data_dir();
        let plan = match installed_engine() {
            // Installed: the engine is a program of its own (no Python on this PC is needed) and it runs in the data folder.
            Some(engine) => {
                if kind == Kind::Demo {
                    return Err("the scripted demo is only in the source folder".to_owned());
                }
                let _ = std::fs::create_dir_all(&data);
                let mut plan = plan(kind, &data, engine, port, random_token());
                plan.args.drain(..2);                                  // "-m orbit": the engine already is `python -m orbit`
                plan
            }
            None => {
                let root = project_root();
                let python = python_for(&root);
                if !python.exists() {
                    return Err(format!("Python not found at {} (set ORBIT_PYTHON, or create the project's .venv)", python.display()));
                }
                plan(kind, &root, python, port, random_token())
            }
        };
        let mut command = Command::new(&plan.program);
        command
            .args(&plan.args)
            .current_dir(&plan.cwd)
            .env("ORBIT_IPC_TOKEN", &plan.token)
            .env("ORBIT_SETTINGS", crate::settings::path())
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped());
        if is_installed() {
            command.env("ORBIT_DATA", &data);
            quiet(&mut command, &data);
        }
        let mut child = command.spawn().map_err(|e| format!("could not start Orbit: {e}"))?;
        let stdin = child.stdin.take();
        Ok(Spawned { child, stdin, url: plan.url })
    }

    /// Has Orbit exited?
    pub fn exited(&mut self) -> Option<String> {
        self.child.try_wait().ok().flatten().map(|status| format!("Orbit stopped ({status})"))
    }

    /// Ends the pipe so Orbit stops by itself, and waits for it; kills it if it takes longer than `grace`.
    pub fn shutdown(&mut self, grace: Duration) {
        self.stdin = None;
        let until = Instant::now() + grace;
        while Instant::now() < until {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Spawned {
    fn drop(&mut self) {
        self.shutdown(Duration::from_secs(6));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_token_is_192_bits_of_hex_and_never_repeats() {
        let tokens: Vec<String> = (0..50).map(|_| random_token()).collect();
        assert!(tokens.iter().all(|t| t.len() == 48 && t.chars().all(|c| c.is_ascii_hexdigit())));
        let mut unique = tokens.clone();
        unique.sort();
        unique.dedup();
        assert_eq!(unique.len(), 50);
    }

    #[test]
    fn a_free_port_can_really_be_bound() {
        let port = free_port().unwrap();
        assert!(port > 1023);
        TcpListener::bind(("127.0.0.1", port)).expect("still free");
    }

    #[test]
    fn the_real_orbit_is_started_voice_first_with_its_link_and_the_lifetime_pipe() {
        let p = plan(Kind::Orbit, Path::new("C:/orbit"), PathBuf::from("py.exe"), 51234, "tok".into());
        assert_eq!(p.args, ["-m", "orbit", "--voice", "--ipc", "--ipc-port", "51234", "--exit-with-parent"]);
        assert_eq!(p.url, "ws://127.0.0.1:51234/?token=tok");
        assert_eq!((p.cwd, p.program), (PathBuf::from("C:/orbit"), PathBuf::from("py.exe")));
    }

    #[test]
    fn the_demo_is_the_scripted_turn_with_a_question() {
        let p = plan(Kind::Demo, Path::new("."), PathBuf::from("py"), 4000, "t".into());
        assert_eq!(p.args, ["-m", "orbit.ipc.demo", "--confirm", "--port", "4000", "--exit-with-parent"]);
    }

    #[test]
    fn the_planned_link_is_one_the_window_is_allowed_to_connect_to() {
        let p = plan(Kind::Orbit, Path::new("."), PathBuf::from("py"), 8765, random_token());
        assert!(crate::link::endpoint::parse(&p.url).is_ok());
    }
}

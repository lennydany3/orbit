//! What Orbit's installer does, apart from drawing: read the files it carries, run the install, report how far it got.
//!
//! `Orbit-Setup-x.y.z.exe` is this program with the real installer appended to its end (see `installer/pack.py`):
//!
//!   [orbit-setup.exe][Orbit-Core-Setup.exe][payload length: u64][files it installs: u64][b"ORBITPK1"]
//!
//! The appended installer is the Inno Setup one Orbit always shipped, so uninstalling, the Start menu entry and the shortcuts work
//! as before. It always runs very silently: every screen the person sees is this program's own.
//!
//! Orbit updates itself by downloading the newest `Orbit-Setup-*.exe` and running it with `/SILENT /SUPPRESSMSGBOXES /NORESTART
//! /CLOSEAPPLICATIONS` (see `updater.rs`), and copies already installed keep doing exactly that. So:
//!   * `/SILENT`      shows only the small "Updating Orbit" screen, which closes itself; the installer starts Orbit again after;
//!   * `/VERYSILENT`  shows nothing at all;
//!   * every other `/...` option is handed to the installer unchanged (`/DIR=...`, `/LOG=...`).

use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const MAGIC: &[u8; 8] = b"ORBITPK1";
const FOOTER: u64 = 24;
/// Inno Setup's id for Orbit (installer/orbit.iss `AppId`), where Windows keeps what it knows of the installed copy.
const UNINSTALL_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3C2A71-5B9E-4D6A-A1C4-7E0B3D92F5C8}_is1";

/// How the installer was started.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    /// Someone opened it: every screen.
    Interactive,
    /// An update: the "Updating Orbit" screen only, no questions.
    Silent,
    /// No window at all.
    VerySilent,
}

/// The command line, read once.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Launch {
    pub mode: Mode,
    /// Options for the inner installer (`/DIR=...`), without the ones this program handles.
    pub passthrough: Vec<String>,
}

impl Launch {
    pub fn parse<I: IntoIterator<Item = String>>(args: I) -> Launch {
        let mut launch = Launch { mode: Mode::Interactive, passthrough: Vec::new() };
        for arg in args {
            let upper = arg.to_ascii_uppercase();
            match upper.as_str() {
                "/VERYSILENT" => launch.mode = Mode::VerySilent,
                "/SILENT" if launch.mode != Mode::VerySilent => launch.mode = Mode::Silent,
                "/SILENT" => {}
                _ if arg.starts_with('/') => launch.passthrough.push(arg),
                _ => {}                                     // Rune's own options (--snap ...), handled by Rune
            }
        }
        launch
    }

    pub fn from_env() -> Launch {
        Launch::parse(std::env::args().skip(1))
    }
}

// ---- the payload ----------------------------------------------------------------------------------------------------------------

/// Where the inner installer sits inside the file, and how many files it installs (for the progress).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Payload {
    pub offset: u64,
    pub len: u64,
    pub files: u64,
}

/// Reads the footer of `file`. None when nothing is appended (a plain build of this program).
pub fn find_payload(file: &Path) -> io::Result<Option<Payload>> {
    let mut f = File::open(file)?;
    let size = f.metadata()?.len();
    if size < FOOTER {
        return Ok(None);
    }
    f.seek(SeekFrom::Start(size - FOOTER))?;
    let mut footer = [0u8; FOOTER as usize];
    f.read_exact(&mut footer)?;
    if &footer[16..24] != MAGIC {
        return Ok(None);
    }
    let len = u64::from_le_bytes(footer[0..8].try_into().expect("8 bytes"));
    let files = u64::from_le_bytes(footer[8..16].try_into().expect("8 bytes"));
    if len == 0 || len > size - FOOTER {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "the installer inside this file is damaged"));
    }
    Ok(Some(Payload { offset: size - FOOTER - len, len, files }))
}

/// Copies the inner installer out of `file` to `to`.
pub fn extract(file: &Path, payload: Payload, to: &Path) -> io::Result<()> {
    let mut from = File::open(file)?;
    from.seek(SeekFrom::Start(payload.offset))?;
    let mut out = io::BufWriter::new(File::create(to)?);
    let copied = io::copy(&mut from.take(payload.len), &mut out)?;
    out.flush()?;
    if copied != payload.len {
        return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "the installer inside this file is cut short"));
    }
    Ok(())
}

/// The inner installer to run: the one appended to this program, or (while developing) `ORBIT_SETUP_PAYLOAD`.
/// Returns its path, how many files it installs (0 when unknown) and the folder to remove afterwards.
fn unpack(work: &Path) -> Result<(PathBuf, u64), String> {
    if let Some(dev) = std::env::var_os("ORBIT_SETUP_PAYLOAD") {
        let files = std::env::var("ORBIT_SETUP_FILES").ok().and_then(|v| v.parse().ok()).unwrap_or(0);
        return Ok((PathBuf::from(dev), files));
    }
    let me = std::env::current_exe().map_err(|e| format!("cannot find this installer's own file ({e})"))?;
    let payload = find_payload(&me)
        .map_err(|e| format!("cannot read this installer ({e})"))?
        .ok_or("this installer has nothing to install (it was built without Orbit inside)")?;
    let inner = work.join("Orbit-Core-Setup.exe");
    extract(&me, payload, &inner).map_err(|e| format!("cannot unpack Orbit ({e}). Is the disk full?"))?;
    Ok((inner, payload.files))
}

// ---- what to install, and where -------------------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Choices {
    pub dir: PathBuf,
    pub startup: bool,
    pub desktop: bool,
    /// Pasted on the keys screen; written to `%APPDATA%\Orbit\.env` only when there is no keys file yet.
    pub groq_key: String,
}

fn reg_value(key: &str, name: &str) -> Option<String> {
    let out = Command::new("reg").args(["query", key, "/v", name]).output().ok()?;
    if !out.status.success() {
        return None;
    }
    parse_reg(&String::from_utf8_lossy(&out.stdout), name)
}

/// The value `name` in what `reg query` printed.
fn parse_reg(text: &str, name: &str) -> Option<String> {
    let line = text.lines().find(|l| l.trim_start().starts_with(name))?;
    line.split_once("REG_SZ").map(|(_, data)| data.trim().to_owned()).filter(|v| !v.is_empty())
}

/// Where Orbit is installed already, if it is.
pub fn installed_dir() -> Option<PathBuf> {
    reg_value(UNINSTALL_KEY, "InstallLocation").map(|p| PathBuf::from(p.trim_end_matches('\\')))
}

/// The installed version, if Orbit is installed.
pub fn installed_version() -> Option<String> {
    reg_value(UNINSTALL_KEY, "DisplayVersion")
}

/// Where a new install goes: `%LOCALAPPDATA%\Programs\Orbit`, Inno Setup's `{autopf}` for a per-user install.
pub fn default_dir() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Users\Public"));
    base.join("Programs").join("Orbit")
}

/// The keys file Orbit reads (`%APPDATA%\Orbit\.env`).
pub fn keys_file() -> Option<PathBuf> {
    std::env::var_os("APPDATA").map(|a| PathBuf::from(a).join("Orbit").join(".env"))
}

/// A pasted key, cleaned the way the old installer did: no spaces around it, no line breaks or quotes in it.
pub fn clean_key(key: &str) -> String {
    key.trim().chars().filter(|c| !matches!(c, '\r' | '\n' | '"')).collect()
}

/// The keys file for a first install: the Groq key, and an empty Spotify line to fill in later.
pub fn keys_text(groq: &str) -> String {
    format!("# Orbit's keys. Edit this file from the tray menu (API keys...), then restart Orbit.\r\nGROQ_API_KEY={groq}\r\nSPOTIFY_CLIENT_ID=\r\n")
}

/// Writes the key, unless there is no key or a keys file exists already (it is never overwritten).
pub fn save_key(file: &Path, groq: &str) -> io::Result<bool> {
    let groq = clean_key(groq);
    if groq.is_empty() || file.exists() {
        return Ok(false);
    }
    if let Some(dir) = file.parent() {
        fs::create_dir_all(dir)?;
    }
    fs::write(file, keys_text(&groq))?;
    Ok(true)
}

/// The inner installer's command line. `choices` is None for an update, which keeps the folder and options chosen last time.
pub fn inner_args(choices: Option<&Choices>, log: &Path, passthrough: &[String]) -> Vec<String> {
    let mut args: Vec<String> = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/SP-"].map(String::from).to_vec();
    args.push(format!("/LOG={}", log.display()));
    if let Some(c) = choices {
        args.push(format!("/DIR={}", c.dir.display()));
        let tasks: Vec<&str> = [(c.desktop, "desktopicon"), (c.startup, "startup")].into_iter().filter(|(on, _)| *on).map(|(_, t)| t).collect();
        args.push(format!("/TASKS={}", tasks.join(",")));
        args.push("/NOLAUNCH=1".into());               // this program offers "Start Orbit" on its last screen instead
    }
    args.extend(passthrough.iter().filter(|a| !a.to_ascii_uppercase().starts_with("/LOG")).cloned());
    args
}

// ---- running it -----------------------------------------------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    Unpacking,
    Preparing,
    Copying,
    Finishing,
    Done,
    Failed,
}

/// How far the install is, shared between the worker thread and the screen.
#[derive(Clone, Debug, PartialEq)]
pub struct Progress {
    pub phase: Phase,
    /// 0 to 1, from the files the installer has written.
    pub fraction: f32,
    pub files_done: u64,
    pub files_total: u64,
    /// Why it failed, in words.
    pub error: Option<String>,
    /// Where the installer's log is, kept when something went wrong.
    pub log: Option<PathBuf>,
}

impl Default for Progress {
    fn default() -> Self {
        Progress { phase: Phase::Unpacking, fraction: 0.0, files_done: 0, files_total: 0, error: None, log: None }
    }
}

pub type Shared = Arc<Mutex<Progress>>;

/// What Inno Setup's log says so far: the files written, and whether it is past copying.
pub fn read_log(text: &str) -> (u64, bool) {
    let written = text.matches("Dest filename:").count() as u64;
    let finishing = text.contains("Installation process succeeded") || text.contains("Need to restart Windows?");
    (written, finishing)
}

/// What an Inno Setup exit code means.
pub fn exit_reason(code: Option<i32>) -> String {
    match code {
        Some(1) => "the installer could not start".into(),
        Some(2) | Some(5) => "the install was cancelled".into(),
        Some(3) => "a file could not be prepared (is Orbit still running?)".into(),
        Some(4) => "something went wrong while copying Orbit's files".into(),
        Some(6) => "the install was stopped".into(),
        Some(7) => "Windows could not get ready for the install".into(),
        Some(8) => "a file is in use; close Orbit and try again".into(),
        Some(c) => format!("the installer stopped (code {c})"),
        None => "the installer was stopped".into(),
    }
}

fn set(shared: &Shared, f: impl FnOnce(&mut Progress)) {
    let mut p = shared.lock().unwrap_or_else(|e| e.into_inner());
    f(&mut p);
}

/// Runs the whole install on the calling thread, reporting into `shared` and calling `wake` after every change.
/// `choices` is None for an update (keep everything as it is).
pub fn run(choices: Option<Choices>, passthrough: &[String], shared: &Shared, wake: &dyn Fn()) {
    let work = std::env::temp_dir().join(format!("orbit-setup-{}", std::process::id()));
    let fail = |why: String, log: Option<PathBuf>| {
        let _ = fs::remove_dir_all(&work);                 // the log, if any, was copied out first
        set(shared, |p| {
            p.phase = Phase::Failed;
            p.error = Some(why);
            p.log = log;
        });
        wake();
    };
    if let Err(e) = fs::create_dir_all(&work) {
        return fail(format!("cannot make a work folder in {} ({e})", work.display()), None);
    }
    let (inner, files) = match unpack(&work) {
        Ok(found) => found,
        Err(why) => return fail(why, None),
    };
    set(shared, |p| {
        p.phase = Phase::Preparing;
        p.files_total = files;
    });
    wake();

    let log = work.join("install.log");
    let mut child = match Command::new(&inner).args(inner_args(choices.as_ref(), &log, passthrough)).spawn() {
        Ok(child) => child,
        Err(e) => return fail(format!("cannot start the installer ({e})"), None),
    };
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => {}
            Err(_) => break None,
        }
        if let Ok(text) = fs::read(&log) {
            let (written, finishing) = read_log(&String::from_utf8_lossy(&text));
            set(shared, |p| {
                p.files_done = written;
                if written > 0 && p.phase == Phase::Preparing {
                    p.phase = Phase::Copying;
                }
                if finishing {
                    p.phase = Phase::Finishing;
                }
                if files > 0 {
                    p.fraction = (written as f32 / files as f32).min(1.0);
                }
            });
            wake();
        }
        std::thread::sleep(Duration::from_millis(120));
    };
    let ok = status.as_ref().is_some_and(|s| s.success());
    if !ok {
        let kept = std::env::temp_dir().join("orbit-setup.log");
        let saved = fs::copy(&log, &kept).ok().map(|_| kept);
        return fail(exit_reason(status.and_then(|s| s.code())), saved);
    }
    if let (Some(c), Some(file)) = (&choices, keys_file())
        && let Err(e) = save_key(&file, &c.groq_key)
    {
        eprintln!("orbit-setup: could not save the Groq key ({e}); add it later from the tray menu");
    }
    let _ = fs::remove_dir_all(&work);
    set(shared, |p| {
        p.phase = Phase::Done;
        p.fraction = 1.0;
    });
    wake();
}

/// Starts the installed Orbit (its window opens), detached from this installer.
pub fn launch_orbit(dir: &Path) -> io::Result<()> {
    let mut command = Command::new(dir.join("Orbit.exe"));
    command.current_dir(dir);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0000_0008 | 0x0000_0200);   // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: it must outlive the installer
    }
    command.spawn().map(drop)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn the_updater_command_line_is_a_silent_update() {
        // What updater.rs runs, in every copy of Orbit already installed.
        let launch = Launch::parse(args(&["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]));
        assert_eq!(launch.mode, Mode::Silent);
        assert_eq!(launch.passthrough, args(&["/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]));
        assert_eq!(Launch::parse(args(&["/verysilent", "/SILENT"])).mode, Mode::VerySilent);
        assert_eq!(Launch::parse(args(&["--snap", "x.png"])), Launch { mode: Mode::Interactive, passthrough: vec![] });
    }

    #[test]
    fn a_payload_round_trips_and_a_plain_file_has_none() {
        let dir = std::env::temp_dir().join(format!("orbit-setup-test-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let packed = dir.join("packed.exe");
        let inner = b"INNER INSTALLER BYTES".to_vec();
        let mut bytes = b"MZ fake program".to_vec();
        bytes.extend(&inner);
        bytes.extend((inner.len() as u64).to_le_bytes());
        bytes.extend(1234u64.to_le_bytes());
        bytes.extend(MAGIC);
        fs::write(&packed, &bytes).unwrap();

        let payload = find_payload(&packed).unwrap().unwrap();
        assert_eq!((payload.len, payload.files), (inner.len() as u64, 1234));
        let out = dir.join("inner.exe");
        extract(&packed, payload, &out).unwrap();
        assert_eq!(fs::read(&out).unwrap(), inner);

        fs::write(dir.join("plain.exe"), b"MZ just a program, nothing appended to it at all").unwrap();
        assert_eq!(find_payload(&dir.join("plain.exe")).unwrap(), None);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_fresh_install_says_where_and_what_and_never_starts_orbit_itself() {
        let c = Choices { dir: PathBuf::from(r"C:\Apps\Orbit"), startup: true, desktop: false, groq_key: String::new() };
        let a = inner_args(Some(&c), Path::new(r"C:\t\install.log"), &args(&["/LOG=elsewhere", "/NORESTART"]));
        assert!(a.contains(&"/VERYSILENT".to_owned()) && a.contains(&r"/DIR=C:\Apps\Orbit".to_owned()));
        assert!(a.contains(&"/TASKS=startup".to_owned()) && a.contains(&"/NOLAUNCH=1".to_owned()));
        assert_eq!(a.iter().filter(|x| x.starts_with("/LOG")).count(), 1);          // ours: the progress is read from it
        let update = inner_args(None, Path::new("l"), &[]);
        assert!(!update.iter().any(|x| x.starts_with("/DIR") || x.starts_with("/TASKS") || x.starts_with("/NOLAUNCH")));
    }

    #[test]
    fn progress_is_counted_from_the_log() {
        let log = "Starting the installation process.\n-- File entry --\nDest filename: C:\\a\\Orbit.exe\n-- File entry --\nDest filename: C:\\a\\engine\\x.dll\n";
        assert_eq!(read_log(log), (2, false));
        assert_eq!(read_log(&format!("{log}Installation process succeeded.\n")), (2, true));
    }

    #[test]
    fn the_key_is_saved_once_and_never_over_an_existing_file() {
        let dir = std::env::temp_dir().join(format!("orbit-setup-keys-{}", std::process::id()));
        let file = dir.join("Orbit").join(".env");
        let _ = fs::remove_dir_all(&dir);
        assert!(!save_key(&file, "   ").unwrap());
        assert!(save_key(&file, " gsk_abc\"\r\n").unwrap());
        assert!(fs::read_to_string(&file).unwrap().contains("GROQ_API_KEY=gsk_abc\r\n"));
        assert!(!save_key(&file, "gsk_other").unwrap());
        assert!(fs::read_to_string(&file).unwrap().contains("gsk_abc"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_installed_folder_and_version_are_read_from_reg_output() {
        let out = "\r\nHKEY_CURRENT_USER\\Software\\Uninstall\\{8F3C2A71}_is1\r\n    InstallLocation    REG_SZ    C:\\Users\\me\\Programs\\Orbit\\\r\n\r\n";
        assert_eq!(parse_reg(out, "InstallLocation").as_deref(), Some(r"C:\Users\me\Programs\Orbit\"));
        assert_eq!(parse_reg("    DisplayVersion    REG_SZ    1.0.2\r\n", "DisplayVersion").as_deref(), Some("1.0.2"));
        assert_eq!(parse_reg("    DisplayVersion    REG_SZ    \r\n", "DisplayVersion"), None);
    }

    #[test]
    fn exit_codes_read_as_words() {
        assert!(exit_reason(Some(8)).contains("in use"));
        assert!(exit_reason(Some(42)).contains("42"));
    }
}

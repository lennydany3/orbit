//! Updates. Every push to `main` becomes a GitHub release with the installer attached (see `.github/workflows/release.yml`); the
//! installed Orbit looks at the latest release now and then, downloads a newer installer in the background, and offers to install
//! it ("Restart and update"). Nothing is ever installed without that click.
//!
//! The network is `curl.exe` (part of Windows 10 and 11), so there is no TLS library to ship or keep patched here. The download
//! is checked against the SHA-256 GitHub records for the asset before it is run. It is only ever fetched from the repository named
//! in `REPO`, over HTTPS, and a release that is a draft or a pre-release is never offered.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use rune::Waker;
use serde_json::Value;

/// Where releases are published. `ORBIT_UPDATE_REPO` points it at a fork.
pub const REPO: &str = "lennydany3/orbit";
/// How soon after start the first look is, and how often it is repeated.
pub const FIRST_CHECK: Duration = Duration::from_secs(20);
pub const EVERY: Duration = Duration::from_secs(6 * 60 * 60);

/// The version this program was built as.
pub const CURRENT: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Release {
    /// `1.2.3`, without the `v`.
    pub version: String,
    /// The installer to download, and its SHA-256 if GitHub gave one.
    pub url: String,
    pub file_name: String,
    pub sha256: Option<String>,
    pub size: u64,
    /// The release's page, for "What's new".
    pub page: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum Update {
    /// Nothing has been looked at yet.
    #[default]
    Idle,
    Checking,
    /// Looked, and this is the newest.
    UpToDate,
    Downloading(Release),
    /// Downloaded and checked: one click installs it.
    Ready(Release, PathBuf),
    /// Why the last look or download did not work (shown once, then forgotten on the next look).
    Failed(String),
}

impl Update {
    pub fn ready(&self) -> Option<&Release> {
        match self {
            Update::Ready(release, _) => Some(release),
            _ => None,
        }
    }

    /// A short line for the tray menu and tooltips.
    pub fn line(&self) -> String {
        match self {
            Update::Idle | Update::UpToDate => format!("Orbit {CURRENT} \u{b7} up to date"),
            Update::Checking => "Checking for updates\u{2026}".to_owned(),
            Update::Downloading(r) => format!("Downloading Orbit {}\u{2026}", r.version),
            Update::Ready(r, _) => format!("Orbit {} is ready to install", r.version),
            Update::Failed(_) => format!("Orbit {CURRENT} \u{b7} could not check for updates"),
        }
    }
}

// ---- versions ---------------------------------------------------------------------------------------------------------

/// `v1.2.3` or `1.2.3` as numbers. Anything after a `-` or `+` (a pre-release or build tag) is ignored; a missing part is 0.
pub fn parse_version(text: &str) -> Option<(u64, u64, u64)> {
    let core = text.trim().trim_start_matches(['v', 'V']).split(['-', '+']).next()?;
    let mut parts = core.split('.');
    let mut next = |required: bool| match parts.next() {
        Some(p) => p.parse::<u64>().ok().map(Some).unwrap_or(None),
        None if !required => Some(0),
        None => None,
    };
    let major = next(true)?;
    let minor = next(false)?;
    let patch = next(false)?;
    Some((major, minor, patch))
}

/// Is `candidate` newer than `current`? Unreadable versions are never newer.
pub fn is_newer(candidate: &str, current: &str) -> bool {
    match (parse_version(candidate), parse_version(current)) {
        (Some(a), Some(b)) => a > b,
        _ => false,
    }
}

// ---- the release ------------------------------------------------------------------------------------------------------

/// What GitHub's "latest release" JSON says, if it is a release with an installer. `Ok(None)`: nothing to offer.
pub fn parse_release(json: &str) -> Result<Option<Release>, String> {
    let v: Value = serde_json::from_str(json).map_err(|e| format!("GitHub sent something unreadable ({e})"))?;
    if v.get("draft").and_then(Value::as_bool) == Some(true) || v.get("prerelease").and_then(Value::as_bool) == Some(true) {
        return Ok(None);
    }
    let Some(tag) = v.get("tag_name").and_then(Value::as_str) else { return Ok(None) };
    let version = tag.trim_start_matches(['v', 'V']).to_owned();
    if parse_version(&version).is_none() {
        return Ok(None);
    }
    let assets = v.get("assets").and_then(Value::as_array).cloned().unwrap_or_default();
    let installer = assets.iter().find(|a| {
        a.get("name").and_then(Value::as_str).is_some_and(|n| n.starts_with("Orbit-Setup") && n.to_ascii_lowercase().ends_with(".exe"))
    });
    let Some(asset) = installer else { return Ok(None) };
    let text = |key: &str| asset.get(key).and_then(Value::as_str).map(str::to_owned);
    let url = text("browser_download_url").ok_or("the release has an installer with no address")?;
    let sha256 = text("digest").and_then(|d| d.strip_prefix("sha256:").map(str::to_owned)).filter(|h| is_sha256(h));
    Ok(Some(Release {
        version,
        file_name: text("name").unwrap_or_else(|| "Orbit-Setup.exe".to_owned()),
        url,
        sha256,
        size: asset.get("size").and_then(Value::as_u64).unwrap_or(0),
        page: v.get("html_url").and_then(Value::as_str).unwrap_or_default().to_owned(),
    }))
}

fn is_sha256(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|b| b.is_ascii_hexdigit())
}

/// The hash in the output of `certutil -hashfile <file> SHA256`: the first line that is 64 hex digits (spaces allowed between pairs).
pub fn hash_from_certutil(output: &str) -> Option<String> {
    output.lines().map(|l| l.split_whitespace().collect::<String>()).find(|l| is_sha256(l)).map(|h| h.to_ascii_lowercase())
}

// ---- doing it ---------------------------------------------------------------------------------------------------------

#[cfg(windows)]
fn hidden(command: &mut Command) -> &mut Command {
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x0800_0000)                      // CREATE_NO_WINDOW
}

#[cfg(not(windows))]
fn hidden(command: &mut Command) -> &mut Command {
    command
}

fn curl() -> Command {
    let mut command = Command::new("curl.exe");
    hidden(&mut command).args(["--silent", "--show-error", "--location", "--fail", "--proto", "=https", "--max-time", "600"]).arg("--user-agent").arg(format!("orbit-updater/{CURRENT}"));
    command
}

fn run(mut command: Command) -> Result<String, String> {
    let out = command.output().map_err(|e| format!("could not run curl.exe: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        let why = String::from_utf8_lossy(&out.stderr).trim().to_owned();
        Err(if why.is_empty() { format!("curl.exe ended with {}", out.status) } else { why })
    }
}

fn repo() -> String {
    std::env::var("ORBIT_UPDATE_REPO").ok().filter(|r| r.matches('/').count() == 1 && !r.contains(' ')).unwrap_or_else(|| REPO.to_owned())
}

/// Asks GitHub for the latest release. `Ok(None)`: there is none, or none with an installer.
pub fn latest() -> Result<Option<Release>, String> {
    let mut command = curl();
    command.args(["--max-time", "20", "--header", "Accept: application/vnd.github+json"]).arg(format!("https://api.github.com/repos/{}/releases/latest", repo()));
    match run(command) {
        Ok(json) => parse_release(&json),
        Err(why) if why.contains("404") => Ok(None),         // no release yet
        Err(why) => Err(why),
    }
}

/// Where downloaded installers wait.
pub fn updates_dir() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
    base.join("Orbit").join("updates")
}

pub fn sha256_of(file: &Path) -> Result<String, String> {
    let mut command = Command::new("certutil.exe");
    hidden(&mut command).arg("-hashfile").arg(file).arg("SHA256");
    hash_from_certutil(&run(command)?).ok_or_else(|| "could not read the download's checksum".to_owned())
}

/// Downloads the installer and checks it. Returns where it is.
pub fn download(release: &Release) -> Result<PathBuf, String> {
    let dir = updates_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("cannot make {}: {e}", dir.display()))?;
    let safe_name: String = release.file_name.chars().filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '.' | '_')).collect();
    let file = dir.join(safe_name);
    if file.is_file() && verified(release, &file).is_ok() {
        return Ok(file);
    }
    let part = file.with_extension("part");
    let mut command = curl();
    command.arg("--output").arg(&part).arg(&release.url);
    run(command)?;
    if let Err(why) = verified(release, &part) {
        let _ = std::fs::remove_file(&part);
        return Err(why);
    }
    std::fs::rename(&part, &file).map_err(|e| format!("cannot keep the download: {e}"))?;
    Ok(file)
}

fn verified(release: &Release, file: &Path) -> Result<(), String> {
    let size = std::fs::metadata(file).map_err(|e| e.to_string())?.len();
    if release.size != 0 && size != release.size {
        return Err("the download is not the size GitHub lists".to_owned());
    }
    match &release.sha256 {
        Some(expected) if sha256_of(file)? != expected.to_ascii_lowercase() => Err("the download does not match its checksum".to_owned()),
        _ => Ok(()),
    }
}

/// Starts the installer, which closes Orbit, replaces it, and opens it again. The caller quits straight after.
pub fn install(file: &Path) -> Result<(), String> {
    let mut command = Command::new(file);
    command.args(["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0000_0008 | 0x0000_0200);   // DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: it must outlive this window
    }
    command.spawn().map(|_| ()).map_err(|e| format!("could not start the installer: {e}"))
}

/// Removes installers that are no longer needed (everything in the updates folder except `keep`).
pub fn tidy(keep: Option<&Path>) {
    let Ok(entries) = std::fs::read_dir(updates_dir()) else { return };
    for entry in entries.flatten() {
        if Some(entry.path().as_path()) != keep {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

// ---- the background worker --------------------------------------------------------------------------------------------

/// Looks for updates in the background and keeps `Update` current. Cloning shares the same state.
#[derive(Clone, Default)]
pub struct Updater {
    state: Arc<Mutex<Update>>,
    waker: Option<Waker>,
}

impl Updater {
    pub fn new(waker: Waker) -> Updater {
        Updater { state: Arc::default(), waker: Some(waker) }
    }

    /// An updater that is already in `state` and never looks anything up (for the window's pictures and tests).
    pub fn with_state(state: Update) -> Updater {
        Updater { state: Arc::new(Mutex::new(state)), waker: None }
    }

    /// The same updater (the same state), woken through `waker` from now on.
    pub fn with_waker(&self, waker: Waker) -> Updater {
        Updater { state: self.state.clone(), waker: Some(waker) }
    }

    pub fn state(&self) -> Update {
        self.state.lock().map(|s| s.clone()).unwrap_or_default()
    }

    fn set(&self, update: Update) {
        if let Ok(mut state) = self.state.lock() {
            *state = update;
        }
        if let Some(wake) = &self.waker {
            wake();
        }
    }

    /// Updates only come to the installed app (or when `ORBIT_UPDATE_FORCE` is set), and never when `ORBIT_NO_UPDATE` is.
    pub fn enabled() -> bool {
        std::env::var_os("ORBIT_NO_UPDATE").is_none() && (crate::spawn::is_installed() || std::env::var_os("ORBIT_UPDATE_FORCE").is_some())
    }

    /// Looks now, on a thread of its own (does nothing if a look or a download is already going, or an update is waiting).
    pub fn check_now(&self) {
        if !Self::enabled() || matches!(self.state(), Update::Checking | Update::Downloading(_) | Update::Ready(..)) {
            return;
        }
        let me = self.clone();
        self.set(Update::Checking);
        let spawned = std::thread::Builder::new().name("updater".into()).spawn(move || me.work());
        if spawned.is_err() {
            self.set(Update::Failed("could not start the update check".into()));
        }
    }

    fn work(&self) {
        match latest() {
            Err(why) => self.set(Update::Failed(why)),
            Ok(Some(release)) if is_newer(&release.version, CURRENT) => {
                self.set(Update::Downloading(release.clone()));
                match download(&release) {
                    Ok(file) => {
                        tidy(Some(&file));
                        self.set(Update::Ready(release, file));
                    }
                    Err(why) => self.set(Update::Failed(why)),
                }
            }
            Ok(_) => {
                tidy(None);
                self.set(Update::UpToDate);
            }
        }
    }

    /// Looks shortly after start, then every few hours, for as long as the program runs.
    pub fn start_schedule(&self) {
        if !Self::enabled() {
            return;
        }
        let me = self.clone();
        let _ = std::thread::Builder::new().name("update-schedule".into()).spawn(move || {
            std::thread::sleep(FIRST_CHECK);
            loop {
                me.check_now();
                std::thread::sleep(EVERY);
            }
        });
    }

    /// Runs the downloaded installer. True if it started (the caller then quits so the installer can replace the program).
    pub fn install_ready(&self) -> bool {
        let Update::Ready(_, file) = self.state() else { return false };
        match install(&file) {
            Ok(()) => true,
            Err(why) => {
                self.set(Update::Failed(why));
                false
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RELEASE: &str = r#"{
        "tag_name": "v1.4.12", "draft": false, "prerelease": false, "html_url": "https://github.com/lennydany3/orbit/releases/tag/v1.4.12",
        "assets": [
            {"name": "Orbit-Setup-1.4.12.exe.sha256", "browser_download_url": "https://x/sha", "size": 90},
            {"name": "Orbit-Setup-1.4.12.exe", "browser_download_url": "https://github.com/lennydany3/orbit/releases/download/v1.4.12/Orbit-Setup-1.4.12.exe",
             "size": 123456, "digest": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"}
        ]}"#;

    #[test]
    fn versions_compare_as_numbers_not_as_text() {
        assert!(is_newer("1.0.10", "1.0.9"));
        assert!(is_newer("v2.0", "1.99.99"));
        assert!(!is_newer("1.0.9", "1.0.10"));
        assert!(!is_newer("1.2.3", "1.2.3"));
        assert!(is_newer("1.2.4-beta", "1.2.3"), "a tag after the dash is ignored");
        assert!(!is_newer("garbage", "1.0.0") && !is_newer("1.0.0", "garbage"));
        assert_eq!(parse_version("V3"), Some((3, 0, 0)));
    }

    #[test]
    fn the_installer_is_picked_out_of_a_release() {
        let release = parse_release(RELEASE).unwrap().unwrap();
        assert_eq!(release.version, "1.4.12");
        assert_eq!(release.file_name, "Orbit-Setup-1.4.12.exe");
        assert_eq!(release.size, 123456);
        assert_eq!(release.sha256.as_deref(), Some("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"));
        assert!(release.url.starts_with("https://github.com/lennydany3/orbit/releases/download/"));
    }

    #[test]
    fn drafts_prereleases_and_releases_without_an_installer_are_not_offered() {
        assert_eq!(parse_release(&RELEASE.replace("\"draft\": false", "\"draft\": true")), Ok(None));
        assert_eq!(parse_release(&RELEASE.replace("\"prerelease\": false", "\"prerelease\": true")), Ok(None));
        assert_eq!(parse_release(r#"{"tag_name":"v1.0.0","assets":[{"name":"notes.txt","browser_download_url":"https://x"}]}"#), Ok(None));
        assert_eq!(parse_release(r#"{"tag_name":"nightly","assets":[]}"#), Ok(None));
        assert!(parse_release("<html>").is_err());
    }

    #[test]
    fn a_checksum_that_is_not_64_hex_digits_is_not_trusted() {
        let bad = RELEASE.replace("sha256:9f86", "sha256:zz86");
        assert_eq!(parse_release(&bad).unwrap().unwrap().sha256, None);
    }

    #[test]
    fn the_hash_is_read_from_certutils_output() {
        let out = "SHA256 hash of Orbit-Setup.exe:\r\n9f 86 d0 81 88 4c 7d 65 9a 2f ea a0 c5 5a d0 15 a3 bf 4f 1b 2b 0b 82 2c d1 5d 6c 15 b0 f0 0a 08\r\nCertUtil: -hashfile command completed successfully.\r\n";
        assert_eq!(hash_from_certutil(out).as_deref(), Some("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"));
        assert_eq!(hash_from_certutil("CertUtil: failed"), None);
    }

    #[test]
    fn the_lines_say_what_is_going_on() {
        let release = parse_release(RELEASE).unwrap().unwrap();
        assert!(Update::Ready(release.clone(), PathBuf::from("x")).line().contains("1.4.12"));
        assert!(Update::Downloading(release).line().starts_with("Downloading"));
        assert!(Update::UpToDate.line().contains(CURRENT));
        assert!(Update::Ready(parse_release(RELEASE).unwrap().unwrap(), PathBuf::new()).ready().is_some());
    }

    #[test]
    fn a_sandbox_run_from_the_source_folder_never_updates() {
        // Not installed (there is no engine folder beside the test program) and nothing forces it.
        if std::env::var_os("ORBIT_UPDATE_FORCE").is_none() {
            assert!(!Updater::enabled());
            let updater = Updater::default();
            updater.check_now();
            assert_eq!(updater.state(), Update::Idle);
        }
    }
}

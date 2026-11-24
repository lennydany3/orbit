//! Opening something outside the window: a saved playlist in the Spotify app, a release's page on GitHub, Orbit's own folder, and the
//! file where the API keys go. Only a `spotify:` address made by `music::Link::uri` is ever handed to the system (letters, digits and
//! colons, nothing else), so what is opened can never be anything but a Spotify page; a web page is only ever one on github.com; a
//! folder or file is only ever Orbit's own.

use std::path::Path;
use std::process::{Command, Stdio};

/// The keys file Orbit starts with: the same text as `.env.example`, which says what each key is for.
const KEYS_TEMPLATE: &str = include_str!("../../.env.example");

fn launch(mut command: Command, what: &str) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);                               // CREATE_NO_WINDOW
    }
    command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().map(drop).map_err(|e| format!("could not open {what}: {e}"))
}

/// Is this a plain `https://github.com/...` address (letters, digits and a few punctuation marks, nothing that could end the argument)?
pub fn is_github_page(url: &str) -> bool {
    url.strip_prefix("https://github.com/").is_some_and(|rest| !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'/' | b'-' | b'_' | b'.')))
}

/// Opens a release's page in the browser.
pub fn open_release_page(url: &str) -> Result<(), String> {
    if !is_github_page(url) {
        return Err("Orbit only opens its own releases page.".to_owned());
    }
    let mut command = Command::new("explorer.exe");
    command.arg(url);
    launch(command, "the release page")
}

/// Opens a folder in Explorer (made first if it is not there yet).
pub fn open_folder(path: &Path) -> Result<(), String> {
    std::fs::create_dir_all(path).map_err(|e| format!("cannot make {}: {e}", path.display()))?;
    let mut command = Command::new("explorer.exe");
    command.arg(path);
    launch(command, "the folder")
}

/// Opens the keys file (`.env` in Orbit's folder) in Notepad, writing the template first if there is none. Orbit reads it when it starts.
pub fn open_keys_file() -> Result<(), String> {
    let dir = crate::spawn::data_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("cannot make {}: {e}", dir.display()))?;
    let file = dir.join(".env");
    if !file.exists() {
        std::fs::write(&file, KEYS_TEMPLATE).map_err(|e| format!("cannot write {}: {e}", file.display()))?;
    }
    let mut command = Command::new("notepad.exe");
    command.arg(&file);
    launch(command, "the keys file")
}

/// Is this a `spotify:kind:id` address and nothing more?
pub fn is_spotify_uri(uri: &str) -> bool {
    let parts: Vec<&str> = uri.split(':').collect();
    let [scheme, kind, id] = parts.as_slice() else { return false };
    *scheme == "spotify" && matches!(*kind, "playlist" | "album" | "artist" | "track" | "show" | "episode") && id.len() == 22 && id.bytes().all(|b| b.is_ascii_alphanumeric())
}

/// Opens the address in the Spotify app (Windows hands `spotify:` addresses to it; with no app installed nothing happens, and the
/// error says so).
pub fn open_uri(uri: &str) -> Result<(), String> {
    if !is_spotify_uri(uri) {
        return Err("Orbit only opens Spotify addresses.".to_owned());
    }
    #[cfg(windows)]
    let mut command = {
        use std::os::windows::process::CommandExt;
        let mut c = Command::new("explorer.exe");
        c.arg(uri).creation_flags(0x0800_0000);                            // CREATE_NO_WINDOW
        c
    };
    #[cfg(not(windows))]
    let mut command = {
        let mut c = Command::new("xdg-open");
        c.arg(uri);
        c
    };
    command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().map(drop).map_err(|e| format!("could not open Spotify: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_plain_spotify_address_is_ever_opened() {
        assert!(is_spotify_uri("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"));
        assert!(is_spotify_uri("spotify:album:4aawyAB9vmqN3uQ7FjRGTy"));
        for bad in ["", "spotify:", "spotify:playlist", "spotify:playlist:abc", "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "file:///c:/windows/notepad.exe",
                    "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M --flag", "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M:extra", "calc.exe", "spotify:a b:37i9dQZF1DXcBWIGoYBM5M",
                    "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M&calc"] {
            assert!(!is_spotify_uri(bad), "{bad:?}");
            assert!(open_uri(bad).is_err());
        }
    }

    #[test]
    fn only_a_github_page_is_opened_as_a_release_page() {
        assert!(is_github_page("https://github.com/lennydany3/orbit/releases/tag/v1.0.4"));
        for bad in ["", "https://github.com/", "http://github.com/x", "https://evil.com/github.com/x", "https://github.com/x y", "https://github.com/x&calc", "file:///c:/x", "https://github.com.evil.com/x"] {
            assert!(!is_github_page(bad), "{bad:?}");
            assert!(open_release_page(bad).is_err());
        }
    }

    #[test]
    fn the_keys_template_names_both_keys() {
        assert!(KEYS_TEMPLATE.contains("GROQ_API_KEY=") && KEYS_TEMPLATE.contains("SPOTIFY_CLIENT_ID="));
        assert!(!KEYS_TEMPLATE.contains("SARVAM"), "Sarvam is gone: speech runs on this PC");
    }

    #[test]
    fn a_link_made_by_the_music_page_is_always_one_that_may_be_opened() {
        for kind in crate::music::Kind::ALL {
            let link = crate::music::Link { kind, spotify_id: "37i9dQZF1DXcBWIGoYBM5M".to_owned() };
            assert!(is_spotify_uri(&link.uri()), "{kind:?}");
        }
    }
}

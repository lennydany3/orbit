//! Opening something outside the window: a saved playlist in the Spotify app. Only a `spotify:` address made by `music::Link::uri` is
//! ever handed to the system (letters, digits and colons, nothing else), so what is opened can never be anything but a Spotify page.

use std::process::{Command, Stdio};

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
    fn a_link_made_by_the_music_page_is_always_one_that_may_be_opened() {
        for kind in crate::music::Kind::ALL {
            let link = crate::music::Link { kind, spotify_id: "37i9dQZF1DXcBWIGoYBM5M".to_owned() };
            assert!(is_spotify_uri(&link.uri()), "{kind:?}");
        }
    }
}

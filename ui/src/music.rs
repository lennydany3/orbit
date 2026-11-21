//! Music: the Spotify links you save, so "play my gym playlist" (or the Play button) knows which one you mean. The file is `music.json`
//! next to the settings (see `userdata`, and `orbit/playlists.py` for the other side, which fills in each name from Spotify's public page
//! and plays them):
//!
//! ```json
//! {"version": 1,
//!  "playlists": [{"id": "p-3f9a1c2b", "kind": "playlist", "spotify_id": "37i9dQZF1DXcBWIGoYBM5M", "name": "Gym Mix", "added": "2026-11-22T10:15"}]}
//! ```
//!
//! The window only has to understand a link (`parse_link`, checked against the same examples as the Python side in
//! `tests/fixtures/spotify_links.json`). No drawing and no windowing, so all of it is tested; `music_view` only draws it.

use std::path::PathBuf;

use rune::TextEdit;
use serde_json::{Map, Value, json};

use crate::datetime::Stamp;
use crate::userdata;

pub const MAX_PLAYLISTS: usize = 200;
pub const MAX_NAME: usize = 100;

/// What a Spotify link points at.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Playlist,
    Album,
    Artist,
    Track,
    Show,
    Episode,
}

impl Kind {
    pub const ALL: [Kind; 6] = [Kind::Playlist, Kind::Album, Kind::Artist, Kind::Track, Kind::Show, Kind::Episode];

    pub fn name(self) -> &'static str {
        match self {
            Kind::Playlist => "playlist",
            Kind::Album => "album",
            Kind::Artist => "artist",
            Kind::Track => "track",
            Kind::Show => "show",
            Kind::Episode => "episode",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Kind::Playlist => "Playlist",
            Kind::Album => "Album",
            Kind::Artist => "Artist",
            Kind::Track => "Track",
            Kind::Show => "Podcast",
            Kind::Episode => "Episode",
        }
    }

    pub fn parse(name: &str) -> Option<Kind> {
        Kind::ALL.into_iter().find(|k| k.name() == name.to_lowercase())
    }
}

/// A Spotify link, understood.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Link {
    pub kind: Kind,
    pub spotify_id: String,
}

impl Link {
    /// `spotify:playlist:37i9...`: what the Spotify app opens.
    pub fn uri(&self) -> String {
        format!("spotify:{}:{}", self.kind.name(), self.spotify_id)
    }

    /// `https://open.spotify.com/playlist/37i9...`
    pub fn url(&self) -> String {
        format!("https://open.spotify.com/{}/{}", self.kind.name(), self.spotify_id)
    }
}

fn is_id(text: &str) -> bool {
    text.len() == 22 && text.bytes().all(|b| b.is_ascii_alphanumeric())
}

/// `text` without `prefix` at its start, if it starts with it (in any case).
fn strip_prefix_ci<'a>(text: &'a str, prefix: &str) -> Option<&'a str> {
    text.get(..prefix.len()).filter(|start| start.eq_ignore_ascii_case(prefix)).map(|_| &text[prefix.len()..])
}

/// `spotify:playlist:ID` (or `spotify:user:name:playlist:ID`).
fn parse_uri(raw: &str) -> Option<Link> {
    let rest = strip_prefix_ci(raw, "spotify:")?;
    let parts: Vec<&str> = rest.split(':').collect();
    let (kind, id) = match parts.as_slice() {
        [kind, id] => (*kind, *id),
        [user, _, kind, id] if user.eq_ignore_ascii_case("user") => (*kind, *id),
        _ => return None,
    };
    Some(Link { kind: Kind::parse(kind)?, spotify_id: id.to_owned() }).filter(|link| is_id(&link.spotify_id))
}

/// `https://open.spotify.com/[intl-xx/][embed/][user/name/]playlist/ID[/...][?...]`
fn parse_url(raw: &str) -> Option<Link> {
    let rest = strip_prefix_ci(raw, "https://").or_else(|| strip_prefix_ci(raw, "http://"))?;
    let (host, path) = rest.split_once('/')?;
    if !host.eq_ignore_ascii_case("open.spotify.com") && !host.eq_ignore_ascii_case("play.spotify.com") {
        return None;
    }
    let mut segments = path.split(['?', '#']).next().unwrap_or("").split('/').peekable();
    if segments.peek().is_some_and(|s| is_intl(s)) {
        segments.next();
    }
    if segments.peek().is_some_and(|s| s.eq_ignore_ascii_case("embed")) {
        segments.next();
    }
    if segments.peek().is_some_and(|s| s.eq_ignore_ascii_case("user")) {
        segments.next();
        if segments.next().is_none_or(str::is_empty) {
            return None;
        }
    }
    let kind = Kind::parse(segments.next()?)?;
    let id = segments.next().filter(|id| is_id(id))?;
    Some(Link { kind, spotify_id: id.to_owned() })
}

/// A Spotify link or `spotify:` address -> what it is, or a sentence saying why it is not usable. The same rules as
/// `parse_link` in `orbit/playlists.py`.
pub fn parse_link(text: &str) -> Result<Link, String> {
    let raw = text.trim().trim_matches(|c: char| matches!(c, '<' | '>' | '"' | '\'' | ' '));
    if raw.is_empty() {
        return Err("Paste a Spotify link first.".to_owned());
    }
    parse_uri(raw).or_else(|| parse_url(raw)).ok_or_else(|| not_spotify(&raw.to_lowercase()))
}

/// `intl-de`, `intl-pt-br`: the country Spotify puts in front of the kind.
fn is_intl(segment: &str) -> bool {
    let Some(rest) = segment.to_lowercase().strip_prefix("intl-").map(str::to_owned) else { return false };
    let mut parts = rest.split('-');
    let ok = |p: &str, lo: usize, hi: usize| (lo..=hi).contains(&p.len()) && p.bytes().all(|b| b.is_ascii_lowercase());
    parts.next().is_some_and(|p| ok(p, 2, 3)) && parts.next().is_none_or(|p| ok(p, 2, 4)) && parts.next().is_none()
}

fn not_spotify(low: &str) -> String {
    if low.contains("spotify.link") || low.contains("spotify.app.link") {
        return "That is one of Spotify's short links. Open it in your browser and copy the full link from the address bar.".to_owned();
    }
    if ["music.apple.com", "youtube.com", "youtu.be", "deezer.com", "soundcloud.com", "tidal.com", "music.amazon"].iter().any(|h| low.contains(h)) {
        return "Orbit plays your music on Spotify, so it needs a Spotify link (open.spotify.com/playlist/...).".to_owned();
    }
    "That is not a Spotify link. In Spotify, open the playlist, choose Share, then Copy link, and paste it here.".to_owned()
}

#[derive(Clone, Debug, PartialEq)]
pub struct Playlist {
    pub id: String,
    pub kind: Kind,
    pub spotify_id: String,
    /// Empty until Orbit has asked Spotify what it is called (or you rename it).
    pub name: String,
    pub added: Option<Stamp>,
    extra: Map<String, Value>,
}

const KNOWN: [&str; 5] = ["id", "kind", "spotify_id", "name", "added"];

impl Playlist {
    fn parse(raw: &Value) -> Option<Playlist> {
        let obj = raw.as_object()?;
        let kind = userdata::text(obj, "kind").and_then(|k| Kind::ALL.into_iter().find(|c| c.name() == k))?;
        let spotify_id = userdata::text(obj, "spotify_id").filter(|s| is_id(s))?.to_owned();
        let id = userdata::clean(userdata::text(obj, "id").unwrap_or_default(), 40);
        Some(Playlist {
            id: if id.is_empty() { userdata::new_id("p") } else { id },
            kind,
            spotify_id,
            name: userdata::clean(userdata::text(obj, "name").unwrap_or_default(), MAX_NAME),
            added: userdata::text(obj, "added").and_then(Stamp::parse),
            extra: userdata::rest(obj, &KNOWN),
        })
    }

    fn to_value(&self) -> Value {
        let mut obj = self.extra.clone();
        obj.insert("id".into(), json!(self.id));
        obj.insert("kind".into(), json!(self.kind.name()));
        obj.insert("spotify_id".into(), json!(self.spotify_id));
        obj.insert("name".into(), json!(self.name));
        if let Some(added) = self.added {
            obj.insert("added".into(), json!(added.to_text()));
        }
        Value::Object(obj)
    }

    pub fn link(&self) -> Link {
        Link { kind: self.kind, spotify_id: self.spotify_id.clone() }
    }

    /// What to call it on the page.
    pub fn title(&self) -> String {
        if self.name.is_empty() { format!("Spotify {}", self.kind.name()) } else { self.name.clone() }
    }
}

/// What `music.json` holds.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Music {
    pub playlists: Vec<Playlist>,
    rest: Map<String, Value>,
}

impl Music {
    pub fn from_map(data: &Map<String, Value>) -> Music {
        Music {
            playlists: data.get("playlists").and_then(Value::as_array).map(|a| a.iter().take(MAX_PLAYLISTS).filter_map(Playlist::parse).collect()).unwrap_or_default(),
            rest: userdata::rest(data, &["version", "playlists"]),
        }
    }

    pub fn to_map(&self) -> Map<String, Value> {
        let mut data = self.rest.clone();
        data.insert("playlists".into(), Value::Array(self.playlists.iter().map(Playlist::to_value).collect()));
        data
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Notice {
    pub text: String,
    pub good: bool,
}

/// Everything the Music page shows and edits.
pub struct MusicState {
    /// Where it is saved. `None` saves nothing (headless scenes, tests).
    pub path: Option<PathBuf>,
    pub data: Music,
    /// The link box.
    pub link: TextEdit,
    pub notice: Option<Notice>,
    /// The playlist being renamed, and its box.
    pub renaming: Option<String>,
    pub rename: TextEdit,
    pub confirm_delete: Option<String>,
    stamp: Option<(u128, u64)>,
}

impl Default for MusicState {
    fn default() -> Self {
        MusicState { path: None, data: Music::default(), link: TextEdit::new(""), notice: None, renaming: None, rename: TextEdit::new(""), confirm_delete: None, stamp: None }
    }
}

impl MusicState {
    pub fn from_json(json: &str) -> MusicState {
        let data = serde_json::from_str::<Value>(json).ok().and_then(|v| v.as_object().map(Music::from_map)).unwrap_or_default();
        MusicState { data, ..Default::default() }
    }

    pub fn open(path: PathBuf) -> MusicState {
        let data = Music::from_map(&userdata::read(&path));
        MusicState { stamp: userdata::stamp(&path), data, path: Some(path), ..Default::default() }
    }

    /// Reads the file again if the other side changed it (it names a new playlist, say). True if it did.
    pub fn reload_if_changed(&mut self) -> bool {
        let Some(path) = &self.path else { return false };
        let now = userdata::stamp(path);
        if now == self.stamp {
            return false;
        }
        self.stamp = now;
        self.data = Music::from_map(&userdata::read(path));
        true
    }

    pub fn good(&mut self, text: impl Into<String>) {
        self.notice = Some(Notice { text: text.into(), good: true });
    }

    pub fn bad(&mut self, text: impl Into<String>) {
        self.notice = Some(Notice { text: text.into(), good: false });
    }

    fn change<T>(&mut self, change: impl FnOnce(&mut Music) -> Result<T, String>) -> Result<T, String> {
        let mut next = match &self.path {
            Some(path) => Music::from_map(&userdata::read(path)),
            None => self.data.clone(),
        };
        let done = change(&mut next)?;
        if let Some(path) = &self.path {
            userdata::write(path, next.to_map())?;
            self.stamp = userdata::stamp(path);
        }
        self.data = next;
        Ok(done)
    }

    pub fn link_edit(&mut self) -> &mut TextEdit {
        &mut self.link
    }

    pub fn rename_edit(&mut self) -> &mut TextEdit {
        &mut self.rename
    }

    /// Saves the link in the box. True if it was saved (a link already saved is not an error: it is just not added again).
    pub fn import(&mut self, now: &Stamp) -> bool {
        let link = match parse_link(&self.link.text) {
            Ok(link) => link,
            Err(why) => {
                self.bad(why);
                return false;
            }
        };
        let made = Playlist { id: userdata::new_id("p"), kind: link.kind, spotify_id: link.spotify_id.clone(), name: String::new(), added: Some(Stamp { second: 0, ..*now }), extra: Map::new() };
        match self.change(|data| {
            if let Some(have) = data.playlists.iter().find(|p| p.link() == link) {
                return Ok((have.title(), false));
            }
            if data.playlists.len() >= MAX_PLAYLISTS {
                return Err(format!("That is as many playlists as Orbit keeps ({MAX_PLAYLISTS}). Remove one first."));
            }
            data.playlists.push(made);
            Ok((String::new(), true))
        }) {
            Ok((_, true)) => {
                self.link.replace_text("");
                self.good("Saved. Orbit looks up its name in a moment; then say \u{201c}Hey Orbit, play my \u{2026} playlist\u{201d}.");
                true
            }
            Ok((title, false)) => {
                self.link.replace_text("");
                self.good(format!("\u{201c}{title}\u{201d} is already saved."));
                true
            }
            Err(why) => {
                self.bad(why);
                false
            }
        }
    }

    pub fn begin_rename(&mut self, id: &str) {
        if let Some(p) = self.data.playlists.iter().find(|p| p.id == id) {
            self.rename.replace_text(p.name.clone());
            self.renaming = Some(id.to_owned());
            self.confirm_delete = None;
        }
    }

    pub fn cancel_rename(&mut self) {
        self.renaming = None;
    }

    /// Gives the playlist being renamed the name in the box. A name you give is the one Orbit listens for.
    pub fn finish_rename(&mut self) -> bool {
        let Some(id) = self.renaming.take() else { return false };
        let name = userdata::clean(&self.rename.text, MAX_NAME * 4);
        if name.chars().count() > MAX_NAME {
            self.renaming = Some(id);
            self.bad(format!("A name is at most {MAX_NAME} characters."));
            return false;
        }
        match self.change(|data| {
            let p = data.playlists.iter_mut().find(|p| p.id == id).ok_or("That playlist is gone.")?;
            p.name = name.clone();
            Ok(())
        }) {
            Ok(()) => {
                if name.is_empty() {
                    self.good("The name is cleared. Orbit will look it up again.");
                } else {
                    self.good(format!("It is called \u{201c}{name}\u{201d} now."));
                }
                true
            }
            Err(why) => {
                self.bad(why);
                false
            }
        }
    }

    pub fn ask_delete(&mut self, id: &str) {
        self.confirm_delete = self.data.playlists.iter().any(|p| p.id == id).then(|| id.to_owned());
        self.renaming = None;
    }

    pub fn delete(&mut self, id: &str) -> bool {
        self.confirm_delete = None;
        let id = id.to_owned();
        match self.change(|data| {
            let at = data.playlists.iter().position(|p| p.id == id).ok_or("That playlist is gone.")?;
            Ok(data.playlists.remove(at).title())
        }) {
            Ok(title) => {
                self.good(format!("\u{201c}{title}\u{201d} is removed from Orbit. (It is still in Spotify.)"));
                true
            }
            Err(why) => {
                self.bad(why);
                false
            }
        }
    }

    pub fn get(&self, id: &str) -> Option<&Playlist> {
        self.data.playlists.iter().find(|p| p.id == id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::tests::tempdir;

    const EXAMPLE: &str = include_str!("../../tests/fixtures/music_example.json");
    const LINKS: &str = include_str!("../../tests/fixtures/spotify_links.json");

    fn at(text: &str) -> Stamp {
        Stamp::parse(text).unwrap()
    }

    fn page() -> (MusicState, PathBuf) {
        let dir = tempdir("music");
        let file = dir.join("music.json");
        std::fs::write(&file, EXAMPLE).unwrap();
        (MusicState::open(file.clone()), file)
    }

    fn on_disk(file: &std::path::Path) -> Music {
        Music::from_map(&userdata::read(file))
    }

    #[test]
    fn a_link_is_understood_the_way_orbit_understands_it() {
        let cases: Vec<Value> = serde_json::from_str(LINKS).unwrap();
        assert!(cases.len() > 20);
        for case in cases {
            let input = case["in"].as_str().unwrap();
            match (parse_link(input), case.get("kind")) {
                (Ok(link), Some(kind)) => assert_eq!((link.kind.name(), link.spotify_id.as_str()), (kind.as_str().unwrap(), case["id"].as_str().unwrap()), "{input:?}"),
                (Err(why), None) => assert!(!why.is_empty(), "{input:?}"),
                (got, want) => panic!("{input:?}: got {got:?}, the fixture says {want:?}"),
            }
        }
    }

    #[test]
    fn a_link_knows_its_address_and_its_page_and_the_reasons_it_is_refused_help() {
        let link = parse_link("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=zzz").unwrap();
        assert_eq!((link.uri().as_str(), link.url().as_str()), ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"));
        assert!(parse_link("https://music.apple.com/us/playlist/x/pl.abc").unwrap_err().contains("Orbit plays your music on Spotify"));
        assert!(parse_link("https://spotify.link/abc").unwrap_err().contains("short links"));
        assert!(parse_link("gym mix").unwrap_err().contains("not a Spotify link"));
        assert!(parse_link("").unwrap_err().contains("Paste a Spotify link"));
    }

    #[test]
    fn the_example_file_is_read_as_written_and_written_back_the_same() {
        let data: Value = serde_json::from_str(EXAMPLE).unwrap();
        let m = Music::from_map(data.as_object().unwrap());
        assert_eq!(m.playlists.len(), 3);
        assert_eq!((m.playlists[0].name.as_str(), m.playlists[0].kind, m.playlists[2].name.as_str()), ("Gym Mix", Kind::Playlist, ""));
        assert_eq!(m.playlists[2].title(), "Spotify album");
        let mut back = m.to_map();
        back.insert("version".into(), json!(1));
        assert_eq!(Value::Object(back), data);
    }

    #[test]
    fn bad_entries_are_left_out() {
        let v = json!({"playlists": [{"kind": "playlist", "spotify_id": "short"}, {"kind": "genre", "spotify_id": "37i9dQZF1DXcBWIGoYBM5M"}, "x",
                                     {"kind": "album", "spotify_id": "4aawyAB9vmqN3uQ7FjRGTy", "name": "Ok", "color": "red"}]});
        let m = Music::from_map(v.as_object().unwrap());
        assert_eq!(m.playlists.iter().map(|p| p.name.as_str()).collect::<Vec<_>>(), ["Ok"]);
    }

    #[test]
    fn a_pasted_link_is_saved_once_and_the_box_is_emptied() {
        let (mut page, file) = page();
        page.link.replace_text("  https://open.spotify.com/playlist/37i9dQZF1DX0XUsuxWHRQd?si=abc  ");
        assert!(page.import(&at("2026-11-22T14:30")));
        assert_eq!(page.link.text, "");
        let saved = on_disk(&file);
        let made = saved.playlists.last().unwrap();
        assert_eq!((made.kind, made.spotify_id.as_str(), made.name.as_str(), made.added), (Kind::Playlist, "37i9dQZF1DX0XUsuxWHRQd", "", Some(at("2026-11-22T14:30"))));
        assert!(page.notice.as_ref().unwrap().good && page.notice.as_ref().unwrap().text.contains("Saved"));
        assert_eq!(saved.playlists.len(), 4);
        page.link.replace_text("spotify:playlist:37i9dQZF1DX0XUsuxWHRQd");
        assert!(page.import(&at("2026-11-22T14:31")));
        assert_eq!(on_disk(&file).playlists.len(), 4, "the same one is not added twice");
        assert!(page.notice.as_ref().unwrap().text.contains("already saved"));
    }

    #[test]
    fn a_link_that_is_not_spotifys_is_refused_with_the_reason_and_nothing_is_written() {
        let (mut page, file) = page();
        page.link.replace_text("https://www.youtube.com/playlist?list=PL123");
        assert!(!page.import(&at("2026-11-22T14:30")));
        assert!(!page.notice.as_ref().unwrap().good && page.notice.as_ref().unwrap().text.contains("Spotify link"));
        assert_eq!((on_disk(&file).playlists.len(), page.link.text.as_str()), (3, "https://www.youtube.com/playlist?list=PL123"), "what was pasted stays to be fixed");
    }

    #[test]
    fn a_playlist_can_be_renamed_and_a_name_you_give_stays() {
        let (mut page, file) = page();
        let id = page.data.playlists[2].id.clone();
        page.begin_rename(&id);
        assert_eq!((page.renaming.as_deref(), page.rename.text.as_str()), (Some(id.as_str()), ""));
        page.rename.replace_text("  My   Album ");
        assert!(page.finish_rename());
        assert_eq!(on_disk(&file).playlists[2].name, "My Album");
        assert!(page.renaming.is_none() && page.notice.as_ref().unwrap().good);
        page.begin_rename(&id);
        page.rename.replace_text("x".repeat(MAX_NAME + 1));
        assert!(!page.finish_rename() && page.renaming.is_some(), "too long: the box stays open to be shortened");
        page.cancel_rename();
        assert!(page.renaming.is_none() && !page.finish_rename());
    }

    #[test]
    fn removing_asks_first_and_only_forgets_the_link() {
        let (mut page, file) = page();
        let id = page.data.playlists[0].id.clone();
        page.ask_delete(&id);
        assert_eq!(page.confirm_delete.as_deref(), Some(id.as_str()));
        assert_eq!(on_disk(&file).playlists.len(), 3, "asking changes nothing");
        assert!(page.delete(&id));
        assert_eq!((page.confirm_delete.clone(), on_disk(&file).playlists.len()), (None, 2));
        assert!(page.notice.as_ref().unwrap().text.contains("still in Spotify"));
        page.ask_delete("nope");
        assert!(page.confirm_delete.is_none() && !page.delete("nope"));
    }

    #[test]
    fn what_orbit_did_to_the_file_is_noticed_and_not_lost() {
        let (mut page, file) = page();
        let mut theirs = userdata::read(&file);
        theirs["playlists"][2]["name"] = json!("Named by Spotify");
        userdata::write(&file, theirs).unwrap();
        assert!(page.reload_if_changed() && page.data.playlists[2].name == "Named by Spotify");
        assert!(!page.reload_if_changed());
        let id = page.data.playlists[0].id.clone();
        assert!(page.delete(&id));
        assert_eq!(on_disk(&file).playlists.iter().find(|p| p.spotify_id == page.data.playlists[1].spotify_id).map(|p| p.name.as_str()), Some("Named by Spotify"));
    }

    #[test]
    fn a_page_with_no_file_saves_by_creating_it() {
        let dir = tempdir("music-fresh");
        let file = dir.join("music.json");
        let mut page = MusicState::open(file.clone());
        page.link.replace_text("spotify:album:4aawyAB9vmqN3uQ7FjRGTy");
        assert!(page.import(&at("2026-11-22T14:30")) && file.exists());
        let mut loose = MusicState::default();
        loose.link.replace_text("spotify:album:4aawyAB9vmqN3uQ7FjRGTy");
        assert!(loose.import(&at("2026-11-22T14:30")) && loose.data.playlists.len() == 1);
    }
}

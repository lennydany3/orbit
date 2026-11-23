//! What the tray icon's menu says. The menu is Windows' own (Rune hands the labels to it), so the look is Windows'; what Orbit
//! decides is what is in it: a header that says what Orbit is doing right now (the "Connected" box that used to sit in the
//! sidebar lives here now), the two things you do most, the update item that changes with the update's state, the settings, and
//! the version. The menu is rebuilt only when what it says changes.

use rune::TrayItem;

use crate::updater::{CURRENT, Update};

/// Everything the menu and the tooltip depend on.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TrayView {
    /// "Orbit \u{b7} Listening", "Orbit \u{b7} starting\u{2026}": what the header says.
    pub header: String,
    /// The orb's chord as shown to people ("Ctrl + Alt + O"), if one is set.
    pub orb_chord: Option<String>,
    pub update: Update,
}

impl Default for TrayView {
    fn default() -> Self {
        TrayView { header: "Orbit".to_owned(), orb_chord: None, update: Update::Idle }
    }
}

impl TrayView {
    pub fn tooltip(&self) -> String {
        match &self.update {
            Update::Ready(release, _) => format!("{}\nUpdate {} ready to install", self.header, release.version),
            _ => self.header.clone(),
        }
    }
}

/// The ids the menu reports back (see `shell::make_tray`).
pub mod id {
    pub const ORB: &str = "orb";
    pub const WINDOW: &str = "window";
    pub const SETTINGS: &str = "settings";
    pub const KEYS: &str = "keys";
    pub const DATA: &str = "data";
    pub const CHECK: &str = "check-updates";
    pub const INSTALL: &str = "install-update";
    pub const NOTES: &str = "release-notes";
    pub const QUIT: &str = "quit";
}

pub fn menu(view: &TrayView) -> Vec<TrayItem> {
    let orb = match &view.orb_chord {
        Some(chord) => format!("Show or hide the orb  ({chord})"),
        None => "Show or hide the orb".to_owned(),
    };
    let mut items = vec![
        TrayItem::action("status", view.header.clone()).enabled(false),
        TrayItem::Separator,
        TrayItem::action(id::WINDOW, "Open Orbit"),
        TrayItem::action(id::ORB, orb),
        TrayItem::Separator,
    ];
    items.extend(match &view.update {
        Update::Ready(release, _) => vec![
            TrayItem::action(id::INSTALL, format!("Restart and update to {}", release.version)),
            TrayItem::action(id::NOTES, "What\u{2019}s new\u{2026}"),
        ],
        Update::Checking => vec![TrayItem::action(id::CHECK, "Checking for updates\u{2026}").enabled(false)],
        Update::Downloading(release) => vec![TrayItem::action(id::CHECK, format!("Downloading {}\u{2026}", release.version)).enabled(false)],
        Update::Failed(_) | Update::Idle | Update::UpToDate => vec![TrayItem::action(id::CHECK, "Check for updates\u{2026}")],
    });
    items.extend([
        TrayItem::action(id::SETTINGS, "Settings\u{2026}"),
        TrayItem::action(id::KEYS, "API keys\u{2026}"),
        TrayItem::action(id::DATA, "Open Orbit\u{2019}s folder"),
        TrayItem::Separator,
        TrayItem::action("version", format!("Orbit {CURRENT}")).enabled(false),
        TrayItem::action(id::QUIT, "Quit Orbit"),
    ]);
    items
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::updater::Release;

    fn labels(view: &TrayView) -> Vec<String> {
        menu(view).into_iter().filter_map(|i| match i { TrayItem::Action { label, .. } => Some(label), _ => None }).collect()
    }

    fn release() -> Release {
        Release { version: "1.2.3".into(), url: "https://x".into(), file_name: "Orbit-Setup-1.2.3.exe".into(), sha256: None, size: 1, page: String::new() }
    }

    #[test]
    fn the_header_says_what_orbit_is_doing_and_cannot_be_clicked() {
        let view = TrayView { header: "Orbit \u{b7} Listening".into(), ..TrayView::default() };
        assert!(matches!(&menu(&view)[0], TrayItem::Action { label, enabled: false, .. } if label == "Orbit \u{b7} Listening"));
    }

    #[test]
    fn the_orb_item_shows_its_chord() {
        let view = TrayView { orb_chord: Some("Ctrl + Alt + O".into()), ..TrayView::default() };
        assert!(labels(&view).contains(&"Show or hide the orb  (Ctrl + Alt + O)".to_owned()));
    }

    #[test]
    fn the_update_item_follows_the_update() {
        let ids = |view: &TrayView| menu(view).into_iter().filter_map(|i| match i { TrayItem::Action { id, enabled, .. } => Some((id, enabled)), _ => None }).collect::<Vec<_>>();
        let idle = ids(&TrayView::default());
        assert!(idle.contains(&(id::CHECK.to_owned(), true)));
        let ready = ids(&TrayView { update: Update::Ready(release(), "x".into()), ..TrayView::default() });
        assert!(ready.contains(&(id::INSTALL.to_owned(), true)) && !ready.iter().any(|(i, _)| i == id::CHECK));
        let busy = ids(&TrayView { update: Update::Checking, ..TrayView::default() });
        assert!(busy.contains(&(id::CHECK.to_owned(), false)), "no second look while one is going");
    }

    #[test]
    fn the_menu_always_ends_with_the_version_and_quit() {
        let all = labels(&TrayView::default());
        assert_eq!(all[all.len() - 1], "Quit Orbit");
        assert!(all[all.len() - 2].starts_with("Orbit "));
    }

    #[test]
    fn the_tooltip_mentions_a_waiting_update() {
        let view = TrayView { update: Update::Ready(release(), "x".into()), ..TrayView::default() };
        assert!(view.tooltip().contains("1.2.3"));
        assert_eq!(TrayView::default().tooltip(), "Orbit");
    }
}

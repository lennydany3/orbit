//! The sidebar: Orbit's mark and name with the notifications bell, the pages, Settings at the bottom, and below that a card only when
//! there is something to say: an update ready to install, what Orbit is busy getting ready, or that it is not running. When all is well
//! there is nothing (what Orbit is doing is on the sphere, and in the tray menu's header).

use rune::{Color, El, Icon, col, el, icon, row, text};

use crate::app::{App, Page};
use crate::icons;
use crate::link::Status;
use crate::updater::Update;
use crate::view::Ink;
use crate::widgets::{badge, caps, t};

pub const WIDTH: f32 = 232.0;

pub(crate) fn sidebar(s: &App, ink: &Ink) -> El {
    col()
        .key("sidebar")
        .w(WIDTH)
        .h_full()
        .shrink()
        .px(14.0)
        .pt(16.0)
        .pb(12.0)
        .gap(2.0)
        .child(brand(s, ink))
        .child(el().key("brand-gap").h(14.0))
        .child(item(s, "nav-home", &icons::HOME, Page::Home, None, ink))
        .child(el().key("assistant-gap").h(14.0))
        .child(row().key("assistant-label").px(12.0).pb(6.0).child(caps("ASSISTANT", ink)))
        .child(item(s, "nav-commands", &icons::WORKFLOW, Page::Commands, None, ink))
        .child(item(s, "nav-reminders", &icons::CLOCK, Page::Reminders, None, ink))
        .child(item(s, "nav-notes", &icons::NOTEBOOK, Page::Notes, Some("BETA"), ink))
        .child(item(s, "nav-music", &icons::MUSIC, Page::Music, Some("BETA"), ink))
        .child(el().key("sidebar-fill").grow(1.0))
        .child(item(s, "nav-settings", &icons::SETTINGS, Page::Settings, None, ink))
        .children(footer(s, ink))
}

/// The mark, the name, and the bell with the number of reminders that went off since it was last looked at.
fn brand(s: &App, ink: &Ink) -> El {
    let unread = s.unread();
    let mut bell = row()
        .key("bell")
        .role("button")
        .label(if unread == 0 { "Notifications".to_owned() } else { format!("Notifications, {unread} new") })
        .focusable(true)
        .size(32.0, 32.0)
        .center()
        .radius(8.0)
        .hover(Color::WHITE.alpha(0.07))
        .on_press(|s: &mut App| s.go(Page::Reminders))
        .child(icon(&icons::BELL, 17.0).color(if unread == 0 { ink.muted } else { ink.fg }));
    if unread > 0 {
        let shown = if unread > 9 { "9+".to_owned() } else { unread.to_string() };
        bell = bell.child(
            row()
                .key("bell-badge")
                .absolute(17.0, 1.0)
                .size(15.0, 15.0)
                .center()
                .radius(7.5)
                .fill(ink.accent)
                .child(text(shown).font(t(9.5, 700.0)).color(Color::WHITE)),
        );
    }
    row()
        .key("brand")
        .w_full()
        .px(6.0)
        .items_center()
        .justify_between()
        .child(
            row()
                .key("brand-name")
                .gap(10.0)
                .items_center()
                .child(crate::logo::mark(30.0, ink.fg))
                .child(text("ORBIT").font(t(14.0, 700.0).mono().tracking(2.2)).color(ink.fg)),
        )
        .child(bell)
}

/// One page in the list: its icon and name, filled when it is the one showing.
fn item(s: &App, key: &'static str, glyph: &'static Icon, page: Page, tag: Option<&str>, ink: &Ink) -> El {
    let on = s.page == page;
    let mut line = row()
        .key(key)
        .role("button")
        .label(page.label())
        .focusable(true)
        .w_full()
        .h(36.0)
        .px(12.0)
        .gap(11.0)
        .items_center()
        .radius(9.0)
        .fill(if on { ink.accent.alpha(0.20) } else { Color::TRANSPARENT })
        .hover(Color::WHITE.alpha(if on { 0.02 } else { 0.05 }))
        .on_press(move |s: &mut App| s.go(page))
        .child(icon(glyph, 16.0).color(if on { ink.accent_text } else { ink.muted }))
        .child(text(page.label()).font(t(13.0, if on { 600.0 } else { 400.0 })).color(if on { ink.fg } else { ink.muted }).grow(1.0).a11y_hidden());
    if let Some(tag) = tag {
        line = line.child(badge(tag, ink));
    }
    line
}

/// What goes under Settings, if anything: an update ready to install first, then what Orbit is getting ready, then a problem.
fn footer(s: &App, ink: &Ink) -> Vec<El> {
    let card = match s.updater.state() {
        Update::Ready(release, _) => Some(update_card(&release.version, ink)),
        Update::Downloading(release) => Some(note(format!("Downloading Orbit {}\u{2026}", release.version), hex_wait(), ink)),
        _ => None,
    }
    .or_else(|| s.live.setup_note.clone().map(|text| note(text, hex_wait(), ink)))
    .or_else(|| problem(s, ink));
    match card {
        Some(card) => vec![el().key("status-gap").h(8.0), card],
        None => Vec::new(),
    }
}

/// "Update ready", with the one button that installs it.
fn update_card(version: &str, ink: &Ink) -> El {
    col()
        .key("update-card")
        .w_full()
        .px(12.0)
        .py(12.0)
        .gap(8.0)
        .radius(10.0)
        .fill(ink.accent.alpha(0.14))
        .border(1.0, ink.accent.alpha(0.45))
        .child(
            row()
                .key("update-title")
                .gap(8.0)
                .items_center()
                .child(el().key("update-dot").size(8.0, 8.0).radius(4.0).fill(ink.accent_text))
                .child(text(format!("Update ready \u{b7} {version}")).font(t(12.0, 600.0)).color(ink.fg).truncate().grow(1.0).min_w(0.0)),
        )
        .child(
            row()
                .key("update-install")
                .role("button")
                .label("Restart and update Orbit")
                .focusable(true)
                .w_full()
                .h(30.0)
                .center()
                .radius(8.0)
                .fill(ink.accent)
                .hover(Color::WHITE.alpha(0.08))
                .on_press(|s: &mut App| s.install_update())
                .child(text("Restart and update").font(t(12.0, 600.0)).color(Color::WHITE).a11y_hidden()),
        )
}

/// A quiet one-line card with a coloured dot.
fn note(words: String, dot: Color, ink: &Ink) -> El {
    row()
        .key("status")
        .w_full()
        .min_h(40.0)
        .px(12.0)
        .py(8.0)
        .gap(9.0)
        .items_center()
        .radius(10.0)
        .fill(Color::WHITE.alpha(0.04))
        .border(1.0, ink.hair)
        .child(el().key("status-dot").size(8.0, 8.0).radius(4.0).fill(dot))
        .child(text(words).font(t(12.0, 400.0)).color(ink.fg).wrap().grow(1.0).min_w(0.0))
}

/// Only when something is wrong: Orbit stopped, or the window cannot reach it. (Connecting is not a problem, and connected says nothing.)
fn problem(s: &App, ink: &Ink) -> Option<El> {
    let (dot, words) = match s.live.status {
        Status::Open | Status::Connecting => return None,
        Status::Closed => (hex_wait(), "Orbit is not answering\u{2026}".to_owned()),
        Status::Error => (ink.bad, s.live.notice.clone().unwrap_or_else(|| "Could not connect".to_owned())),
        Status::Off => (ink.muted, s.live.notice.clone().unwrap_or_else(|| "Orbit is not running".to_owned())),
    };
    Some(note(words, dot, ink))
}

fn hex_wait() -> Color {
    crate::view::hex("#E0A94B")
}

//! The window: a sidebar of pages on the left, and the chosen page in a rounded panel on the right. Home is the black stage with the big
//! sphere, what was last said and, when Orbit needs a yes or no, the question.
//!
//! The states can still be tried by hand with the keys 1 to 9, 0 and M (on Home), and Esc answers a question with no.

use rune::tok::TextStyle;
use rune::{Align, Color, Cx, El, col, effect, icon, row, text};

use crate::app::{App, Ask, Page, Ring};
use crate::icons;
use crate::orbit::SphereConfig;

/// Marks that tell the compositor which sphere a slot is.
pub const STAGE_MARK: u32 = 1;
pub const OVERLAY_MARK: u32 = 2;

/// The big sphere: 5000 points, 1.4 px each, filling 60% of the stage's shorter side.
pub const STAGE: SphereConfig = SphereConfig { count: 5000, size: 1.4, scale: 0.6, fray: 1.0 };
/// The floating orb's sphere: a lighter one (1400 points, 1.05 px).
pub const OVERLAY: SphereConfig = SphereConfig { count: 1400, size: 1.05, scale: 0.7, fray: 1.0 };

pub(crate) fn hex(s: &str) -> Color {
    rune::color::hex(s).unwrap_or(Color::WHITE)
}

/// The window's colours: white on black, one violet for what is on or primary.
pub(crate) struct Ink {
    pub fg: Color,
    pub muted: Color,
    pub hair: Color,
    pub hair_strong: Color,
    /// Behind everything.
    pub bg: Color,
    /// The page's panel.
    pub panel: Color,
    /// A card on the panel.
    pub raised: Color,
    /// A text box on a card.
    pub field: Color,
    pub accent: Color,
    /// The violet as text on dark.
    pub accent_text: Color,
    /// Something that went wrong.
    pub bad: Color,
}

impl Ink {
    pub(crate) fn new() -> Ink {
        Ink {
            fg: hex("#F2F3F5"),
            muted: hex("#8A8F98"),
            hair: Color::WHITE.alpha(0.08),
            hair_strong: Color::WHITE.alpha(0.22),
            bg: hex("#060607"),
            panel: hex("#0C0C0E"),
            raised: hex("#111114"),
            field: hex("#0A0A0C"),
            accent: hex("#8B4FD0"),
            accent_text: hex("#C7A6F5"),
            bad: hex("#FF8F8F"),
        }
    }
}

fn t(size: f32, weight: f32) -> TextStyle {
    TextStyle::new(size, weight)
}

pub fn view(s: &App, cx: &mut Cx<App>) -> El {
    cx.reduced_motion(s.orbit.reduced());
    let ink = Ink::new();
    let body = match s.page {
        Page::Home => stage(s, &ink),
        Page::Commands => crate::commands_view::page(s, &ink),
        Page::Reminders => crate::reminders_view::page(s, &ink),
        Page::Notes => crate::notes_view::page(s, &ink),
        Page::Music => crate::music_view::page(s, &ink),
        Page::Settings => crate::settings_view::page(s, &ink),
    };
    row()
        .key("window")
        .w_full()
        .h_full()
        .fill(ink.bg)
        .child(crate::sidebar::sidebar(s, &ink))
        .child(
            col()
                .key("content")
                .grow(1.0)
                .min_w(0.0)
                .h_full()
                .padding(10.0)
                .gap(8.0)
                .children(s.live.ring.as_ref().map(|ring| ring_banner(ring, &ink)))
                .child(col().key("panel").w_full().grow(1.0).min_h(0.0).radius(14.0).fill(if s.page == Page::Home { Color::BLACK } else { ink.panel }).border(1.0, ink.hair).child(body)),
        )
}

/// A reminder or timer is ringing: a bar above whatever page is open, with the Stop button (saying "ok thanks" stops it too).
fn ring_banner(ring: &Ring, ink: &Ink) -> El {
    let stop = if ring.sent {
        text("Stopping\u{2026}").font(t(12.0, 400.0)).color(ink.muted)
    } else {
        row()
            .key("ring-stop")
            .role("button")
            .label("Stop")
            .focusable(true)
            .h(32.0)
            .w(96.0)
            .center()
            .radius(8.0)
            .fill(ink.fg)
            .border(1.0, ink.fg)
            .on_press(|s: &mut App| {
                s.stop_alarm();
            })
            .child(text("Stop").font(t(13.0, 500.0)).color(Color::BLACK).a11y_hidden())
    };
    row()
        .key("ring-banner")
        .role("dialog")
        .label(ring.heading())
        .w_full()
        .items_center()
        .gap(14.0)
        .padding(12.0)
        .radius(12.0)
        .fill(ink.raised)
        .border(1.0, ink.accent)
        .child(icon(&icons::BELL, 18.0).color(ink.accent_text))
        .child(
            col()
                .key("ring-text")
                .grow(1.0)
                .min_w(0.0)
                .gap(2.0)
                .child(text(ring.heading()).font(t(11.0, 400.0).tracking(1.54)).color(ink.muted))
                .child(text(&ring.text).font(t(15.0, 400.0)).color(ink.fg).wrap()),
        )
        .child(text("or say \u{201c}ok thanks\u{201d}").font(t(11.0, 400.0)).color(ink.muted))
        .child(stop)
}

// ---- the stage ----------------------------------------------------------------------------------------------------------------

fn stage(s: &App, ink: &Ink) -> El {
    let sphere = effect(STAGE_MARK).effect_data(STAGE.to_data()).key("sphere").w_full().h_full();

    // What was last said, under the sphere (and nothing else: the state it is in is on the sidebar).
    let mut footer = col().key("footer").items_center().gap(18.0);
    if let Some((who, said)) = s.caption() {
        footer = footer.child(
            col()
                .key("caption")
                .items_center()
                .gap(6.0)
                .child(text(who).font(t(11.0, 400.0).tracking(1.54)).color(ink.muted))
                .child(text(said).font(t(18.0, 400.0)).color(ink.fg).max_w(640.0).align(Align::Center).wrap()),
        );
    }

    col()
        .key("stage")
        .grow(1.0)
        .min_w(0.0)
        .h_full()
        .child(col().key("sphere-area").grow(1.0).min_h(0.0).child(sphere))
        .child(
            col()
                .key("overlay")
                .absolute(0.0, 0.0)
                .w_full()
                .h_full()
                .justify_end()
                .items_center()
                .pb(36.0)
                .gap(28.0)
                .children(s.live.confirm.as_ref().map(|ask| confirm_dialog(ask, ink)))
                .child(footer),
        )
}

/// Orbit asks before it does something risky; the buttons answer it, and so does Esc (no).
fn confirm_dialog(ask: &Ask, ink: &Ink) -> El {
    let answers = if ask.sent.is_some() {
        text("Sent. Waiting for Orbit\u{2026}").font(t(12.0, 400.0)).color(ink.muted)
    } else {
        row()
            .key("answers")
            .gap(8.0)
            .child(answer_button("confirm-no", "No", false, ink))
            .child(answer_button("confirm-yes", "Yes", true, ink))
    };
    col()
        .key("confirm")
        .role("dialog")
        .label("Orbit asks")
        .w(380.0)
        .padding(20.0)
        .gap(14.0)
        .items_center()
        .radius(14.0)
        .fill(hex("#0A0A0C").alpha(0.96))
        .border(1.0, ink.hair_strong)
        .child(text("Orbit asks").font(t(11.0, 400.0).tracking(1.54)).color(ink.muted))
        .child(text(format!("Do you want me to {}?", ask.action)).font(t(16.0, 400.0)).color(ink.fg).align(Align::Center).wrap().max_w(340.0))
        .child(answers)
        .child(text(if ask.sent.is_some() { "" } else { "Esc says no" }).font(t(11.0, 400.0)).color(ink.muted))
}

fn answer_button(key: &str, label: &str, yes: bool, ink: &Ink) -> El {
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .h(32.0)
        .w(84.0)
        .center()
        .radius(8.0)
        .fill(if yes { ink.fg } else { Color::TRANSPARENT })
        .border(1.0, if yes { ink.fg } else { ink.hair_strong })
        .hover(Color::WHITE.alpha(if yes { 0.0 } else { 0.06 }))
        .on_press(move |s: &mut App| {
            s.answer_confirm(yes);
        })
        .child(text(label).font(t(13.0, 500.0)).color(if yes { Color::BLACK } else { ink.fg }).a11y_hidden())
}

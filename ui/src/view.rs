//! The window: a black stage with the big sphere, what state it is in, what was last said, and, when Orbit needs a yes or no,
//! the question. A faithful copy of the design's stage: its layout, colours and type, built from Rune elements.
//!
//! The states can still be tried by hand with the keys 1 to 9, 0 and M, and Esc answers a question with no.

use rune::tok::TextStyle;
use rune::{Align, Color, Cx, El, col, effect, el, row, text};

use crate::app::{App, Ask, Page};
use crate::editor::Section;
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

/// The design's few colours.
pub(crate) struct Ink {
    pub fg: Color,
    pub muted: Color,
    pub hair: Color,
    pub hair_strong: Color,
}

impl Ink {
    pub(crate) fn new() -> Ink {
        Ink { fg: hex("#F2F3F5"), muted: hex("#8A8F98"), hair: Color::WHITE.alpha(0.08), hair_strong: Color::WHITE.alpha(0.22) }
    }
}

fn t(size: f32, weight: f32) -> TextStyle {
    TextStyle::new(size, weight)
}

pub fn view(s: &App, cx: &mut Cx<App>) -> El {
    cx.reduced_motion(s.orbit.reduced());
    let ink = Ink::new();
    let body = match s.page {
        Page::Stage => stage(s, &ink),
        Page::Settings => crate::settings_view::page(s, &ink),
    };
    row().key("window").w_full().h_full().fill(Color::BLACK).child(body)
}

// ---- the stage ----------------------------------------------------------------------------

fn stage(s: &App, ink: &Ink) -> El {
    let state = s.orbit.state;
    let label = if s.orbit.reduced() { format!("{} (reduced motion)", state.label()) } else { state.label().to_owned() };

    let sphere = effect(STAGE_MARK).effect_data(STAGE.to_data()).key("sphere").w_full().h_full();

    let header = row()
        .key("title")
        .absolute(28.0, 20.0)
        .gap(12.0)
        .items_center()
        .child(crate::logo::mark(46.0, ink.fg))
        .child(
            row()
                .key("title-text")
                .gap(12.0)
                .items_end()
                .child(text("Orbit").font(t(14.0, 600.0).tracking(0.56)).color(ink.fg))
                .child(text("Sphere states, motion review").font(t(12.0, 400.0).tracking(0.72)).color(ink.muted)),
        );

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
    footer = footer.child(
        col()
            .key("state-text")
            .items_center()
            .gap(6.0)
            .child(text(label).font(t(12.0, 400.0).tracking(1.92)).color(ink.fg))
            .child(
                text(state.description())
                    .font(t(13.0, 400.0))
                    .color(ink.muted)
                    .max_w(440.0)
                    .align(Align::Center)
                    .wrap(),
            ),
    );

    // A way in to the settings, top right.
    let settings_button = row()
        .key("top-right")
        .absolute(0.0, 18.0)
        .w_full()
        .px(24.0)
        .justify_end()
        .child(
            row()
                .key("open-settings")
                .role("button")
                .label("Settings")
                .focusable(true)
                .h(32.0)
                .px(14.0)
                .center()
                .radius(8.0)
                .fill(Color::WHITE.alpha(0.06))
                .border(1.0, Color::WHITE.alpha(0.12))
                .hover(Color::WHITE.alpha(0.05))
                .on_press(|s: &mut App| s.open_settings(Section::Hotkeys))
                .child(text("Settings").font(t(12.0, 400.0)).color(ink.fg).a11y_hidden()),
        );

    col()
        .key("stage")
        .grow(1.0)
        .min_w(0.0)
        .h_full()
        .fill(Color::BLACK)
        // The sphere takes everything above a 72 px strip, as in the design.
        .child(col().key("sphere-area").grow(1.0).min_h(0.0).child(sphere))
        .child(el().key("strip").h(72.0))
        .child(header)
        .child(settings_button)
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

// ---- controls -----------------------------------------------------------------------------

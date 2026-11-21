//! The pieces every page is built from: buttons, switches, chips, cards, fields, the page header. They only draw; what a click does is
//! attached by the page (`.on_press(..)`). The look is one dark surface with thin lines, and one violet for what is on or primary.

use rune::tok::TextStyle;
use rune::{Color, El, Icon, col, el, icon, row, text, text_input};

use crate::view::{Ink, hex};

pub(crate) fn t(size: f32, weight: f32) -> TextStyle {
    TextStyle::new(size, weight)
}

/// How a button looks.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Look {
    /// An outline.
    Plain,
    /// Filled with the violet: the one thing to do on the page.
    Primary,
    /// Quiet: no outline until the pointer is over it.
    Ghost,
}

fn look_colors(look: Look, ink: &Ink) -> (Color, Color, Color, Color) {
    // (fill, border, label, hover)
    match look {
        Look::Primary => (ink.accent, ink.accent, Color::WHITE, Color::WHITE.alpha(0.12)),
        Look::Plain => (Color::WHITE.alpha(0.05), Color::WHITE.alpha(0.11), ink.fg, Color::WHITE.alpha(0.06)),
        Look::Ghost => (Color::TRANSPARENT, Color::TRANSPARENT, ink.fg, Color::WHITE.alpha(0.07)),
    }
}

/// A 32 px button with a label.
pub(crate) fn button(key: &str, label: &str, ink: &Ink, look: Look) -> El {
    let (fill, border, label_color, hover) = look_colors(look, ink);
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .h(32.0)
        .px(14.0)
        .center()
        .radius(8.0)
        .fill(fill)
        .border(1.0, border)
        .hover(hover)
        .child(text(label).font(t(12.5, if look == Look::Primary { 600.0 } else { 500.0 })).color(label_color).a11y_hidden())
}

/// A 32 px button with an icon in front of its label.
pub(crate) fn icon_label_button(key: &str, glyph: &'static Icon, label: &str, ink: &Ink, look: Look) -> El {
    let (fill, border, label_color, hover) = look_colors(look, ink);
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .h(32.0)
        .px(13.0)
        .gap(7.0)
        .center()
        .radius(8.0)
        .fill(fill)
        .border(1.0, border)
        .hover(hover)
        .child(icon(glyph, 14.0).color(label_color))
        .child(text(label).font(t(12.5, if look == Look::Primary { 600.0 } else { 500.0 })).color(label_color).a11y_hidden())
}

/// A small square button with one icon (edit, delete, refresh).
pub(crate) fn icon_button(key: impl Into<String>, glyph: &'static Icon, label: &str, tone: Color) -> El {
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .size(30.0, 30.0)
        .center()
        .radius(8.0)
        .hover(Color::WHITE.alpha(0.08))
        .help(label.to_owned())
        .child(icon(glyph, 15.0).color(tone))
}

/// A choice among several (a repeat, a size): the chosen one is filled.
pub(crate) fn chip(key: impl Into<String>, label: &str, on: bool, ink: &Ink) -> El {
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .h(28.0)
        .px(12.0)
        .center()
        .radius(14.0)
        .fill(if on { ink.accent.alpha(0.22) } else { Color::TRANSPARENT })
        .border(1.0, if on { ink.accent } else { Color::WHITE.alpha(0.14) })
        .hover(Color::WHITE.alpha(if on { 0.0 } else { 0.06 }))
        .child(text(label).font(t(12.0, if on { 600.0 } else { 400.0 })).color(if on { Color::WHITE } else { ink.fg }).a11y_hidden())
}

/// A switch: violet when on.
pub(crate) fn switch(key: impl Into<String>, on: bool, label: &str, ink: &Ink) -> El {
    let mut track = row()
        .key(key)
        .role("switch")
        .label(label)
        .checked(on)
        .focusable(true)
        .w(38.0)
        .h(22.0)
        .px(3.0)
        .items_center()
        .radius(11.0)
        .fill(if on { ink.accent } else { Color::WHITE.alpha(0.16) });
    track = if on { track.justify_end() } else { track.justify_start() };
    track.child(el().size(16.0, 16.0).radius(8.0).fill(if on { Color::WHITE } else { Color::WHITE.alpha(0.75) }))
}

pub(crate) fn divider(ink: &Ink) -> El {
    el().w_full().h(1.0).fill(ink.hair)
}

/// A small label in capitals ("ASSISTANT", "BETA").
pub(crate) fn caps(label: &str, ink: &Ink) -> El {
    text(label).font(t(10.5, 500.0).tracking(1.2)).color(ink.muted)
}

/// A tiny tag next to a name.
pub(crate) fn badge(label: &str, ink: &Ink) -> El {
    row().px(6.0).h(18.0).center().radius(5.0).fill(Color::WHITE.alpha(0.07)).child(text(label).font(t(9.5, 600.0).tracking(0.9)).color(ink.muted))
}

/// A card: a raised box with a thin line round it.
pub(crate) fn card(key: &str, ink: &Ink) -> El {
    col().key(key).w_full().radius(12.0).fill(ink.raised).border(1.0, ink.hair)
}

/// A line of what just happened, or what went wrong.
pub(crate) fn notice(text_of: Option<(&str, bool)>, ink: &Ink) -> El {
    match text_of {
        Some((words, good)) => text(words).font(t(12.0, 400.0)).color(if good { ink.fg } else { ink.bad }).wrap().max_w(640.0).key("notice"),
        None => el().key("notice").h(0.0),
    }
}

/// A single-line text box. Bind it with `.on_edit(..)` on the returned element's input (`edit_access`), as `field` does.
pub(crate) fn field<F>(key: String, edit: &rune::TextEdit, placeholder: &str, access: F, ink: &Ink) -> El
where
    F: for<'a> Fn(&'a mut crate::app::App) -> &'a mut rune::TextEdit + 'static,
{
    let input = text_input(edit, t(13.0, 400.0)).key(format!("{key}-input")).single_line().grow(1.0).min_w(0.0).color(ink.fg).placeholder(placeholder, ink.muted).on_edit(access);
    row().key(key).grow(1.0).min_w(0.0).h(36.0).px(12.0).items_center().radius(8.0).fill(ink.field).border(1.0, Color::WHITE.alpha(0.12)).child(input)
}

/// A box that opens a menu of choices when pressed (attach `.on_press_tx(..)` that opens the menu anchored to `key`).
pub(crate) fn select_box(key: &str, shown: &str, muted: bool, width: f32, ink: &Ink) -> El {
    row()
        .key(key)
        .role("combobox")
        .label(shown.to_owned())
        .focusable(true)
        .w(width)
        .h(36.0)
        .px(12.0)
        .gap(8.0)
        .items_center()
        .justify_between()
        .radius(8.0)
        .fill(ink.field)
        .border(1.0, Color::WHITE.alpha(0.12))
        .hover(Color::WHITE.alpha(0.05))
        .child(text(shown).font(t(13.0, 400.0)).color(if muted { ink.muted } else { ink.fg }).truncate().grow(1.0).min_w(0.0).a11y_hidden())
        .child(icon(&crate::icons::CHEVRON_DOWN, 14.0).color(ink.muted))
}

/// A row of a settings card: a name and what it does on the left, its control on the right.
pub(crate) fn setting_row(key: &str, title: &str, detail: &str, control: El, ink: &Ink) -> El {
    row()
        .key(key.to_owned())
        .w_full()
        .px(18.0)
        .py(14.0)
        .gap(16.0)
        .items_center()
        .justify_between()
        .child(
            col()
                .key(format!("{key}-text"))
                .grow(1.0)
                .min_w(0.0)
                .gap(3.0)
                .child(text(title.to_owned()).font(t(13.5, 600.0)).color(ink.fg))
                .child(text(detail.to_owned()).font(t(12.0, 400.0)).color(ink.muted).wrap()),
        )
        .child(control)
}

/// One tab of a strip (attach `.on_press(..)`): the chosen one is lit. `width` of `None` is as wide as its label.
pub(crate) fn tab(key: impl Into<String>, label: &str, on: bool, width: Option<f32>, ink: &Ink) -> El {
    let mut tab = row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .h(26.0)
        .px(14.0)
        .center()
        .radius(7.0)
        .fill(if on { Color::WHITE.alpha(0.10) } else { Color::TRANSPARENT })
        .hover(Color::WHITE.alpha(0.05))
        .child(text(label.to_owned()).font(t(12.5, if on { 600.0 } else { 400.0 })).color(if on { ink.fg } else { ink.muted }).a11y_hidden());
    if let Some(w) = width {
        tab = tab.grow(1.0).min_w(w);
    }
    tab
}

/// Tabs along the top of a page, as one rounded strip.
pub(crate) fn tab_strip(key: &str, tabs: Vec<El>, ink: &Ink) -> El {
    row().key(key.to_owned()).h(34.0).padding(3.0).gap(2.0).radius(10.0).fill(Color::WHITE.alpha(0.04)).border(1.0, ink.hair).children(tabs)
}

/// The top of a page: its name (and a tag), what it is for, and the one thing to do on it at the right; a thin line under it.
pub(crate) fn page_header(title: &str, tag: Option<&str>, subtitle: &str, action: Option<El>, ink: &Ink) -> El {
    let mut name = row().key("page-name").gap(10.0).items_center().child(text(title.to_owned()).font(t(21.0, 650.0).tracking(-0.2)).color(ink.fg));
    if let Some(tag) = tag {
        name = name.child(badge(tag, ink));
    }
    let mut top = row().key("page-top").w_full().px(28.0).py(22.0).gap(16.0).items_center().justify_between().child(
        col().key("page-title").gap(5.0).grow(1.0).min_w(0.0).child(name).child(text(subtitle.to_owned()).font(t(12.5, 400.0)).color(ink.muted).wrap().max_w(640.0)),
    );
    if let Some(action) = action {
        top = top.child(action);
    }
    col().key("page-header").w_full().child(top).child(divider(ink))
}

/// A slider track with a knob, 0 to 100 (the page attaches `.on_drag(..)`: pressing anywhere moves the knob there).
pub(crate) fn slider(key: &str, label: &str, percent: u8, active: bool, width: f32, ink: &Ink) -> El {
    const KNOB: f32 = 14.0;
    let frac = f32::from(percent.min(100)) / 100.0;
    let knob_x = (width - KNOB) * frac;
    let tint = if active { ink.accent } else { Color::WHITE.alpha(0.35) };
    el()
        .key(key.to_owned())
        .role("slider")
        .label(label.to_owned())
        .focusable(true)
        .w(width)
        .h(28.0)
        .child(el().key(format!("{key}-track")).absolute(0.0, 12.0).w(width).h(4.0).radius(2.0).fill(Color::WHITE.alpha(0.16)))
        .child(el().key(format!("{key}-fill")).absolute(0.0, 12.0).w(knob_x + KNOB / 2.0).h(4.0).radius(2.0).fill(tint))
        .child(el().key(format!("{key}-knob")).absolute(knob_x, 7.0).size(KNOB, KNOB).radius(KNOB / 2.0).fill(if active { Color::WHITE } else { Color::WHITE.alpha(0.6) }))
}

/// The percent under the pointer on a slider that was pressed or dragged (`Drag::x` over its width).
pub(crate) fn slider_percent(x: f32, width: f32) -> u8 {
    ((x / width.max(1.0)).clamp(0.0, 1.0) * 100.0).round() as u8
}

/// A coloured tile for a playlist, the same every time for the same id: two hues from its letters.
pub(crate) fn tile_colors(seed: &str) -> (Color, Color) {
    let hash = seed.bytes().fold(2166136261u32, |h, b| (h ^ u32::from(b)).wrapping_mul(16777619));
    let pick = |n: u32| {
        const PALETTE: [&str; 8] = ["#6D4AA8", "#2F6B8F", "#A8484E", "#3E7D5B", "#9A6A2B", "#5F5AA8", "#8F3F7A", "#3B6F7F"];
        hex(PALETTE[(n % 8) as usize])
    };
    (pick(hash), pick(hash >> 7))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_slider_percent_comes_from_where_the_pointer_is_and_stays_in_range() {
        assert_eq!((slider_percent(0.0, 220.0), slider_percent(110.0, 220.0), slider_percent(220.0, 220.0)), (0, 50, 100));
        assert_eq!((slider_percent(-30.0, 220.0), slider_percent(900.0, 220.0), slider_percent(5.0, 0.0)), (0, 100, 100));
    }

    #[test]
    fn a_tile_is_the_same_colours_every_time_for_the_same_playlist() {
        let a = tile_colors("p-3f9a1c2b");
        assert_eq!(a, tile_colors("p-3f9a1c2b"));
        let differ = ["p-1", "p-2", "p-3", "p-4", "p-5", "p-6"].iter().map(|s| tile_colors(s)).collect::<Vec<_>>();
        assert!(differ.windows(2).any(|w| w[0] != w[1]), "different playlists do not all look alike");
    }
}

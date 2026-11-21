//! The Settings page, drawn: a column of sections on the left (Hotkeys, Commands, Sounds) and the chosen one on the right. All the
//! rules live in `editor`; this only shows its state and turns clicks into calls on it.
//!
//! More sections (the model and its keys, the voice) slot in as another `Section`, another nav entry and another
//! function here; nothing else has to change.

use rune::tok::TextStyle;
use rune::{Color, Drag, El, Phase, ScrollSpec, Tx, col, edit_access, el, row, text, text_input};

use crate::app::App;
use crate::editor::{Draft, Field, SPOTS, Section, StepKind, describe_field, spot_at};
use crate::layout;
use crate::settings::{self, Command, OrbSize, TALK_KEYS};
use crate::view::{Ink, hex};

fn t(size: f32, weight: f32) -> TextStyle {
    TextStyle::new(size, weight)
}

const BAD: &str = "#FF8F8F";

pub(crate) fn page(s: &App, ink: &Ink) -> El {
    let content = match s.prefs.section {
        Section::Hotkeys => hotkeys(s, ink),
        Section::Commands => commands(s, ink),
        Section::Sounds => sounds(s, ink),
        Section::Customization => customization(s, ink),
    };
    col()
        .key("settings")
        .w_full()
        .h_full()
        .fill(Color::BLACK)
        .child(top_bar(ink))
        .child(el().key("top-rule").w_full().h(1.0).fill(ink.hair))
        .child(
            row()
                .key("settings-body")
                .grow(1.0)
                .min_h(0.0)
                .child(nav(s, ink))
                .child(el().key("nav-rule").w(1.0).h_full().fill(ink.hair))
                .child(
                    col()
                        .key("settings-scroll")
                        .grow(1.0)
                        .min_w(0.0)
                        .h_full()
                        .scroll(ScrollSpec { stick_bottom: false })
                        .child(col().key("settings-content").w_full().max_w(780.0).px(32.0).py(28.0).gap(18.0).child(content)),
                ),
        )
}

fn top_bar(ink: &Ink) -> El {
    row()
        .key("settings-top")
        .w_full()
        .h(56.0)
        .px(20.0)
        .items_center()
        .gap(16.0)
        .child(button("back", "\u{2190} Back", ink, false).on_press(|s: &mut App| s.close_settings()))
        .child(text("Settings").font(t(15.0, 600.0).tracking(0.3)).color(ink.fg))
}

fn nav(s: &App, ink: &Ink) -> El {
    let item = |key: &'static str, label: &'static str, section: Section| {
        let on = s.prefs.section == section;
        row()
            .key(key)
            .role("button")
            .label(label)
            .focusable(true)
            .w_full()
            .h(36.0)
            .px(12.0)
            .items_center()
            .radius(8.0)
            .fill(if on { Color::WHITE.alpha(0.08) } else { Color::TRANSPARENT })
            .hover(Color::WHITE.alpha(0.05))
            .on_press(move |s: &mut App| s.prefs.select(section))
            .child(text(label).font(t(13.0, if on { 600.0 } else { 400.0 })).color(if on { ink.fg } else { ink.muted }).a11y_hidden())
    };
    col()
        .key("settings-nav")
        .w(190.0)
        .h_full()
        .shrink()
        .px(12.0)
        .py(16.0)
        .gap(4.0)
        .child(item("nav-hotkeys", "Hotkeys", Section::Hotkeys))
        .child(item("nav-commands", "Commands", Section::Commands))
        .child(item("nav-sounds", "Sounds", Section::Sounds))
        .child(item("nav-customization", "Customization", Section::Customization))
}

// ---- pieces ---------------------------------------------------------------------------------------------------------

fn heading(ink: &Ink, title: &str, subtitle: &str) -> El {
    col()
        .key("heading")
        .gap(6.0)
        .child(text(title).font(t(20.0, 600.0)).color(ink.fg))
        .child(text(subtitle).font(t(13.0, 400.0)).color(ink.muted).wrap().max_w(560.0))
}

fn card(key: &str, ink: &Ink) -> El {
    col().key(key).w_full().radius(12.0).fill(hex("#0D0D10")).border(1.0, ink.hair)
}

/// An outlined 32 px button; `primary` is the filled one.
fn button(key: &str, label: &str, ink: &Ink, primary: bool) -> El {
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .h(32.0)
        .px(14.0)
        .center()
        .radius(8.0)
        .fill(if primary { ink.fg } else { Color::WHITE.alpha(0.06) })
        .border(1.0, if primary { ink.fg } else { Color::WHITE.alpha(0.12) })
        .hover(Color::WHITE.alpha(if primary { 0.0 } else { 0.05 }))
        .child(text(label).font(t(12.0, if primary { 600.0 } else { 400.0 })).color(if primary { Color::BLACK } else { ink.fg }).a11y_hidden())
}

/// A small square button with one symbol (move up, move down, remove).
fn glyph_button(key: String, glyph: &str, label: &str, ink: &Ink) -> El {
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .size(28.0, 28.0)
        .center()
        .radius(7.0)
        .hover(Color::WHITE.alpha(0.08))
        .child(text(glyph).font(t(14.0, 400.0)).color(ink.muted).a11y_hidden())
}

/// A choice among several (a kind of step, a talk key): the chosen one is filled.
fn chip(key: String, label: &str, on: bool, ink: &Ink) -> El {
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .h(28.0)
        .px(11.0)
        .center()
        .radius(14.0)
        .fill(if on { ink.fg } else { Color::TRANSPARENT })
        .border(1.0, if on { ink.fg } else { Color::WHITE.alpha(0.16) })
        .hover(Color::WHITE.alpha(if on { 0.0 } else { 0.06 }))
        .child(text(label).font(t(12.0, if on { 600.0 } else { 400.0 })).color(if on { Color::BLACK } else { ink.fg }).a11y_hidden())
}

fn switch(key: String, on: bool, label: &str, ink: &Ink) -> El {
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
        .fill(if on { ink.fg } else { Color::WHITE.alpha(0.16) });
    track = if on { track.justify_end() } else { track.justify_start() };
    track.child(el().size(16.0, 16.0).radius(8.0).fill(if on { Color::BLACK } else { Color::WHITE.alpha(0.75) }))
}

fn divider(ink: &Ink) -> El {
    el().w_full().h(1.0).fill(ink.hair)
}

/// A line of what just happened, or what went wrong.
fn notice(s: &App, ink: &Ink) -> El {
    match &s.prefs.notice {
        Some(n) => text(n.text.clone()).font(t(12.0, 400.0)).color(if n.good { ink.fg } else { hex(BAD) }).wrap().max_w(620.0).key("notice"),
        None => el().key("notice").h(0.0),
    }
}

/// A single-line text box bound to one of the page's edits.
fn field<F>(key: String, edit: &rune::TextEdit, placeholder: &str, access: F, ink: &Ink) -> El
where
    F: for<'a> Fn(&'a mut App) -> &'a mut rune::TextEdit + 'static,
{
    let input = text_input(edit, t(13.0, 400.0)).key(format!("{key}-input")).single_line().grow(1.0).min_w(0.0).color(ink.fg).placeholder(placeholder, ink.muted).on_edit(access);
    row().key(key).grow(1.0).min_w(0.0).h(36.0).px(12.0).items_center().radius(8.0).fill(hex("#111114")).border(1.0, Color::WHITE.alpha(0.12)).child(input)
}

// ---- hotkeys --------------------------------------------------------------------------------------------------------

fn hotkeys(s: &App, ink: &Ink) -> El {
    let prefs = &s.prefs;
    let mut rows: Vec<El> = Vec::new();
    let describe = |f: Field| match f {
        Field::Orb => "Pins the orb on screen, or lets it go again.",
        Field::Yes => "Answers an open question from any app.",
        Field::No => "Says no to an open question.",
    };
    for (i, f) in Field::ALL.into_iter().enumerate() {
        if i > 0 {
            rows.push(divider(ink));
        }
        rows.push(chord_row(s, f, describe(f), ink));
    }
    rows.push(divider(ink));
    rows.push(talk_row(s, ink));
    let mut body = col().key("hotkeys").w_full().gap(18.0).child(heading(
        ink,
        "Hotkeys",
        "Keys that work from any app. Pick a new one and Orbit uses it straight away.",
    ));
    body = body.child(card("hotkey-card", ink).children(rows));
    body = body.child(
        row()
            .key("hotkey-actions")
            .gap(10.0)
            .items_center()
            .child(button("reset-hotkeys", "Reset all to the defaults", ink, false).on_press(|s: &mut App| s.reset_hotkeys())),
    );
    if let Some(why) = &prefs.orb_error {
        body = body.child(text(why.clone()).font(t(12.0, 400.0)).color(hex(BAD)).wrap().max_w(620.0).key("orb-error"));
    }
    body.child(notice(s, ink))
}

fn chord_row(s: &App, f: Field, detail: &str, ink: &Ink) -> El {
    let prefs = &s.prefs;
    let recording = prefs.recording == Some(f);
    let chord = prefs.hotkey(f).to_owned();
    let key = format!("chord-{}", f.name());
    let pill = row()
        .key(key.clone())
        .role("button")
        .label(format!("{}: {}", describe_field(f), settings::pretty(&chord)))
        .focusable(true)
        .h(34.0)
        .px(14.0)
        .min_w(150.0)
        .center()
        .radius(8.0)
        .fill(if recording { Color::WHITE.alpha(0.12) } else { Color::WHITE.alpha(0.06) })
        .border(1.0, if recording { ink.fg } else { Color::WHITE.alpha(0.12) })
        .hover(Color::WHITE.alpha(0.05))
        .on_press(move |s: &mut App| s.prefs.begin_record(f))
        .child(
            text(if recording { "Press the new keys\u{2026}".to_owned() } else { settings::pretty(&chord) })
                .font(t(12.0, 500.0).mono())
                .color(ink.fg)
                .a11y_hidden(),
        );
    let mut right = row().key(format!("{key}-controls")).gap(8.0).items_center().child(pill);
    if recording {
        right = right.child(button(&format!("{key}-cancel"), "Cancel", ink, false).on_press(|s: &mut App| s.prefs.cancel_record()));
    } else if chord != f.default_chord() {
        right = right.child(button(&format!("{key}-reset"), "Reset", ink, false).on_press(move |s: &mut App| {
            s.change_hotkey(f, f.default_chord());
        }));
    }
    let hint = if recording { "Hold Ctrl, Alt or Win and press a key. Esc cancels." } else { detail };
    row()
        .key(format!("{key}-row"))
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
                .gap(4.0)
                .child(text(describe_field(f)).font(t(14.0, 500.0)).color(ink.fg))
                .child(text(hint).font(t(12.0, 400.0)).color(ink.muted).wrap()),
        )
        .child(right)
}

fn talk_row(s: &App, ink: &Ink) -> El {
    let current = s.prefs.hotkeys().talk.clone();
    let mut chips = row().key("talk-chips").flex_wrap().gaps(8.0, 8.0);
    for (name, label) in TALK_KEYS {
        let name = *name;
        chips = chips.child(chip(format!("talk-{name}"), label, current == name, ink).on_press(move |s: &mut App| {
            s.prefs.set_talk(name);
        }));
    }
    if !TALK_KEYS.iter().any(|(n, _)| *n == current) {
        chips = chips.child(chip("talk-custom".to_owned(), &settings::talk_label(&current), true, ink));
    }
    col()
        .key("talk-row")
        .w_full()
        .px(18.0)
        .py(14.0)
        .gap(12.0)
        .child(
            col()
                .key("talk-text")
                .gap(4.0)
                .child(text("Hold to talk").font(t(14.0, 500.0)).color(ink.fg))
                .child(text("Hold this key while you speak, as an alternative to saying \"Hey Orbit\".").font(t(12.0, 400.0)).color(ink.muted).wrap()),
        )
        .child(chips)
}

// ---- sounds ---------------------------------------------------------------------------------------------------------

/// The slider's track, in points (the buttons beside it move it by ten).
const SLIDER_W: f32 = 220.0;
const KNOB: f32 = 14.0;

fn sounds(s: &App, ink: &Ink) -> El {
    let sounds = s.prefs.sounds();
    let on = sounds.enabled;
    let switch_row = row()
        .key("sounds-switch-row")
        .w_full()
        .px(18.0)
        .py(14.0)
        .gap(16.0)
        .items_center()
        .justify_between()
        .child(
            col()
                .key("sounds-switch-text")
                .grow(1.0)
                .min_w(0.0)
                .gap(4.0)
                .child(text("Orb sounds").font(t(14.0, 500.0)).color(ink.fg))
                .child(text("A soft tone as the orb fades in and another as it fades away. Never while it is only updating what it says.").font(t(12.0, 400.0)).color(ink.muted).wrap()),
        )
        .child(switch("sounds-on".to_owned(), on, "Orb sounds", ink).on_press(move |s: &mut App| s.set_sounds_enabled(!on)));
    let hint = if on { "Drag the slider, or use the buttons. You hear the pop-up tone at the new volume." } else { "Switch the sounds on to hear a change." };
    let controls = row()
        .key("volume-controls")
        .gap(8.0)
        .items_center()
        .child(glyph_button("volume-down".to_owned(), "\u{2212}", "Quieter", ink).on_press(|s: &mut App| s.step_volume(-1)))
        .child(volume_slider(sounds.volume, on, ink))
        .child(glyph_button("volume-up".to_owned(), "+", "Louder", ink).on_press(|s: &mut App| s.step_volume(1)))
        .child(text(format!("{}%", sounds.volume)).key("volume-readout").font(t(12.0, 500.0).mono()).color(if on { ink.fg } else { ink.muted }).w(40.0));
    let volume_row = row()
        .key("volume-row")
        .w_full()
        .px(18.0)
        .py(14.0)
        .gap(16.0)
        .items_center()
        .justify_between()
        .child(
            col()
                .key("volume-text")
                .grow(1.0)
                .min_w(0.0)
                .gap(4.0)
                .child(text("Volume").font(t(14.0, 500.0)).color(ink.fg))
                .child(text(hint).font(t(12.0, 400.0)).color(ink.muted).wrap()),
        )
        .child(controls);
    col()
        .key("sounds")
        .w_full()
        .gap(18.0)
        .child(heading(ink, "Sounds", "Orbit makes a quiet sound when the orb appears and when it goes away."))
        .child(card("sounds-card", ink).child(switch_row).child(divider(ink)).child(volume_row))
        .child(row().key("sounds-actions").gap(10.0).items_center().child(button("reset-sounds", "Reset to the defaults", ink, false).on_press(|s: &mut App| s.reset_sounds())))
        .child(notice(s, ink))
}

/// A horizontal slider, 0 to 100. Pressing anywhere on it moves the knob there, and dragging follows the pointer; the volume is
/// saved where it is let go (Esc during the drag puts it back).
fn volume_slider(percent: u8, on: bool, ink: &Ink) -> El {
    let frac = f32::from(percent.min(100)) / 100.0;
    let knob_x = (SLIDER_W - KNOB) * frac;
    let tint = if on { ink.fg } else { Color::WHITE.alpha(0.4) };
    let at = |g: &Drag| ((g.x / g.w.max(1.0)).clamp(0.0, 1.0) * 100.0).round() as u8;
    el()
        .key("volume-slider")
        .role("slider")
        .label("Volume")
        .focusable(true)
        .w(SLIDER_W)
        .h(28.0)
        .on_drag(move |s: &mut App, g: &mut Drag, tx: &mut Tx| {
            tx.instant();
            match g.phase {
                Phase::Down | Phase::Move => s.prefs.drag_volume(at(g)),
                Phase::Up => s.finish_volume(!g.cancelled()),
            }
        })
        .child(el().key("volume-track").absolute(0.0, 12.0).w(SLIDER_W).h(4.0).radius(2.0).fill(Color::WHITE.alpha(0.16)))
        .child(el().key("volume-fill").absolute(0.0, 12.0).w(knob_x + KNOB / 2.0).h(4.0).radius(2.0).fill(tint))
        .child(el().key("volume-knob").absolute(knob_x, 7.0).size(KNOB, KNOB).radius(KNOB / 2.0).fill(tint))
}

// ---- customization --------------------------------------------------------------------------------------------------

/// The little screen the orb is dragged about on, in points.
const PREVIEW_W: f32 = 300.0;
const TILE_W: f32 = 46.0;
const TILE_H: f32 = 34.0;
const DOT: f32 = 8.0;

fn customization(s: &App, ink: &Ink) -> El {
    let look = s.prefs.look();
    let spot = s.prefs.spot_name().unwrap_or("Your own spot");
    let position = row()
        .key("position-row")
        .w_full()
        .px(18.0)
        .py(16.0)
        .gap(28.0)
        .items_start()
        .justify_between()
        .child(
            col()
                .key("position-text")
                .grow(1.0)
                .min_w(0.0)
                .gap(14.0)
                .child(
                    col()
                        .key("position-head")
                        .gap(4.0)
                        .child(text("Position").font(t(14.0, 500.0)).color(ink.fg))
                        .child(text("Drag the orb on the little screen, or pick a spot. The real orb shows on your screen while this page is open.").font(t(12.0, 400.0)).color(ink.muted).wrap())
                        .child(text(format!("Now: {spot}")).key("position-now").font(t(12.0, 500.0)).color(ink.fg)),
                )
                .child(spot_grid(look.x, look.y, ink)),
        )
        .child(screen_preview(look.x, look.y, look.size, ink));
    let mut chips = row().key("size-chips").gap(8.0).items_center();
    for size in OrbSize::ALL {
        chips = chips.child(chip(format!("size-{}", size.name()), size.label(), look.size == size, ink).on_press(move |s: &mut App| {
            s.prefs.set_orb_size(size);
        }));
    }
    let size = row()
        .key("size-row")
        .w_full()
        .px(18.0)
        .py(14.0)
        .gap(16.0)
        .items_center()
        .justify_between()
        .child(
            col()
                .key("size-text")
                .grow(1.0)
                .min_w(0.0)
                .gap(4.0)
                .child(text("Size").font(t(14.0, 500.0)).color(ink.fg))
                .child(text("How big the orb is when it floats over your work.").font(t(12.0, 400.0)).color(ink.muted).wrap()),
        )
        .child(chips);
    col()
        .key("customization")
        .w_full()
        .gap(18.0)
        .child(heading(ink, "Customization", "Choose where the orb floats on your screen and how big it is. It stays where you put it."))
        .child(card("orb-card", ink).child(position).child(divider(ink)).child(size))
        .child(row().key("orb-actions").gap(10.0).items_center().child(button("reset-orb", "Reset to the defaults", ink, false).on_press(|s: &mut App| {
            s.prefs.reset_orb();
        })))
        .child(notice(s, ink))
}

/// The nine named spots: press one and the orb goes there.
fn spot_grid(x: f32, y: f32, ink: &Ink) -> El {
    let mut grid = col().key("spot-grid").gap(6.0);
    for (r, names) in SPOTS.iter().enumerate() {
        let mut line = row().key(format!("spot-row-{r}")).gap(6.0);
        for (c, name) in names.iter().enumerate() {
            let (sx, sy) = spot_at(r, c);
            let on = (x - sx).abs() < 0.0005 && (y - sy).abs() < 0.0005;
            let dot = el()
                .key(format!("spot-{r}{c}-dot"))
                .absolute(5.0 + sx * (TILE_W - 10.0 - DOT), 5.0 + sy * (TILE_H - 10.0 - DOT))
                .size(DOT, DOT)
                .radius(DOT / 2.0)
                .fill(if on { Color::BLACK } else { ink.fg.alpha(0.8) });
            line = line.child(
                el()
                    .key(format!("spot-{r}{c}"))
                    .role("button")
                    .label(*name)
                    .focusable(true)
                    .size(TILE_W, TILE_H)
                    .radius(7.0)
                    .fill(if on { ink.fg } else { Color::WHITE.alpha(0.06) })
                    .border(1.0, if on { ink.fg } else { Color::WHITE.alpha(0.12) })
                    .hover(Color::WHITE.alpha(if on { 0.0 } else { 0.06 }))
                    .on_press(move |s: &mut App| {
                        s.prefs.place_orb(sx, sy);
                    })
                    .child(dot),
            );
        }
        grid = grid.child(line);
    }
    grid
}

/// A small copy of the screen with the orb on it. Pressing anywhere puts the orb there and dragging carries it; it is saved where
/// it is let go (Esc during the drag puts it back). The orb on the real screen follows as it moves.
fn screen_preview(x: f32, y: f32, size: OrbSize, ink: &Ink) -> El {
    let screen = layout::screen();
    let (kx, ky) = (PREVIEW_W / screen.w.max(1.0), (PREVIEW_W * screen.h / screen.w.max(1.0)).clamp(110.0, 260.0) / screen.h.max(1.0));
    let height = screen.h * ky;
    let dot = (size.diameter() * kx).max(12.0);
    let (ix, iy) = (layout::EDGE_X * kx, layout::EDGE_Y * ky);
    let (travel_x, travel_y) = ((PREVIEW_W - 2.0 * ix - dot).max(1.0), (height - 2.0 * iy - dot).max(1.0));
    let at = move |g: &Drag| {
        let (px, py) = (g.x / g.w.max(1.0) * PREVIEW_W, g.y / g.h.max(1.0) * height);
        (((px - ix - dot / 2.0) / travel_x).clamp(0.0, 1.0), ((py - iy - dot / 2.0) / travel_y).clamp(0.0, 1.0))
    };
    el()
        .key("orb-preview")
        .role("slider")
        .label("Orb position")
        .focusable(true)
        .w(PREVIEW_W)
        .h(height)
        .radius(10.0)
        .fill(hex("#08080A"))
        .border(1.0, ink.hair_strong)
        .on_drag(move |s: &mut App, g: &mut Drag, tx: &mut Tx| {
            tx.instant();
            match g.phase {
                Phase::Down | Phase::Move => {
                    let (x, y) = at(g);
                    s.prefs.drag_orb(x, y);
                }
                Phase::Up => {
                    s.prefs.finish_orb(!g.cancelled());
                }
            }
        })
        .child(el().key("guide-v").absolute(PREVIEW_W / 2.0, 0.0).size(1.0, height).fill(Color::WHITE.alpha(0.05)))
        .child(el().key("guide-h").absolute(0.0, height / 2.0).size(PREVIEW_W, 1.0).fill(Color::WHITE.alpha(0.05)))
        .child(el().key("preview-orb").absolute(ix + x * travel_x, iy + y * travel_y).size(dot, dot).radius(dot / 2.0).fill(ink.fg.alpha(0.92)))
}

// ---- commands -------------------------------------------------------------------------------------------------------

fn commands(s: &App, ink: &Ink) -> El {
    match &s.prefs.draft {
        Some(d) => editor(d, ink),
        None => list(s, ink),
    }
}

fn list(s: &App, ink: &Ink) -> El {
    let prefs = &s.prefs;
    let mut body = col().key("commands").w_full().gap(18.0);
    body = body.child(
        row()
            .key("commands-head")
            .w_full()
            .items_start()
            .justify_between()
            .gap(16.0)
            .child(heading(ink, "Commands", "Say a phrase and Orbit runs a list of steps for you: open apps, websites and folders, press keys, type text."))
            .child(button("new-command", "New command", ink, true).on_press(|s: &mut App| s.prefs.new_command())),
    );
    if !prefs.problems.is_empty() {
        body = body.child(
            col()
                .key("problems")
                .w_full()
                .padding(14.0)
                .gap(4.0)
                .radius(10.0)
                .border(1.0, hex(BAD).alpha(0.5))
                .child(text("Orbit could not read part of the settings file:").font(t(12.0, 600.0)).color(hex(BAD)))
                .children(prefs.problems.iter().enumerate().map(|(i, p)| text(p.clone()).key(format!("problem-{i}")).font(t(12.0, 400.0)).color(ink.muted).wrap())),
        );
    }
    let commands = &prefs.settings.commands;
    if commands.is_empty() {
        body = body.child(
            col()
                .key("no-commands")
                .w_full()
                .padding(24.0)
                .gap(6.0)
                .radius(12.0)
                .border(1.0, ink.hair)
                .child(text("No commands yet").font(t(14.0, 500.0)).color(ink.fg))
                .child(text("For example: say \"work mode\" and Orbit opens Spotify and your project folder.").font(t(12.0, 400.0)).color(ink.muted).wrap()),
        );
    } else {
        let mut rows: Vec<El> = Vec::new();
        for (i, c) in commands.iter().enumerate() {
            if i > 0 {
                rows.push(divider(ink));
            }
            rows.push(command_row(s, i, c, ink));
        }
        body = body.child(card("command-card", ink).children(rows));
    }
    body.child(notice(s, ink))
}

fn command_row(s: &App, i: usize, c: &Command, ink: &Ink) -> El {
    let phrases = c.phrases.iter().map(|p| format!("\u{201c}{p}\u{201d}")).collect::<Vec<_>>().join("  \u{b7}  ");
    let tone = if c.enabled { ink.fg } else { ink.muted };
    let mut summary = col().key(format!("cmd-{i}-text")).grow(1.0).min_w(0.0).gap(4.0);
    summary = summary
        .child(text(c.name.clone()).font(t(14.0, 600.0)).color(tone).truncate())
        .child(text(phrases).font(t(12.0, 400.0)).color(ink.muted).truncate())
        .child(text(c.describe()).font(t(12.0, 400.0)).color(Color::WHITE.alpha(0.38)).truncate());
    let mut right = row().key(format!("cmd-{i}-controls")).gap(10.0).items_center();
    if s.prefs.confirm_delete == Some(i) {
        right = right
            .child(text("Delete it?").font(t(12.0, 400.0)).color(ink.muted))
            .child(button(&format!("cmd-{i}-delete-yes"), "Delete", ink, true).on_press(move |s: &mut App| {
                s.prefs.delete(i);
            }))
            .child(button(&format!("cmd-{i}-delete-no"), "Keep", ink, false).on_press(|s: &mut App| s.prefs.confirm_delete = None));
    } else {
        right = right
            .child(switch(format!("cmd-{i}-on"), c.enabled, &format!("{} is on", c.name), ink).on_press(move |s: &mut App| {
                s.prefs.toggle(i);
            }))
            .child(button(&format!("cmd-{i}-edit"), "Edit", ink, false).on_press(move |s: &mut App| s.prefs.edit_command(i)))
            .child(button(&format!("cmd-{i}-delete"), "Delete", ink, false).on_press(move |s: &mut App| s.prefs.ask_delete(i)));
    }
    row().key(format!("cmd-{i}")).w_full().px(18.0).py(14.0).gap(16.0).items_center().justify_between().child(summary).child(right)
}

fn editor(d: &Draft, ink: &Ink) -> El {
    let title = if d.index.is_some() { "Edit command" } else { "New command" };
    let mut body = col().key("editor").w_full().gap(20.0).child(heading(ink, title, "Pick what to say, then what Orbit should do."));

    // the name
    let name = {
        let input = field("name".to_owned(), &d.name, "Work mode", edit_access(|s: &mut App| s.prefs.name_edit()), ink);
        col().key("name-block").w_full().gap(8.0).child(label_text("Name", ink)).child(input)
    };
    body = body.child(name);

    // what to say
    let mut phrases = col().key("phrases-block").w_full().gap(8.0).child(label_text("When you say", ink));
    for i in 0..d.phrases.len() {
        let input = field(format!("phrase-{i}"), &d.phrases[i], "work mode", edit_access(move |s: &mut App| s.prefs.phrase_edit(i)), ink);
        let mut line = row().key(format!("phrase-line-{i}")).w_full().gap(6.0).items_center().child(input);
        if d.phrases.len() > 1 {
            line = line.child(glyph_button(format!("phrase-{i}-remove"), "\u{d7}", "Remove this phrase", ink).on_press(move |s: &mut App| s.prefs.remove_phrase(i)));
        }
        phrases = phrases.child(line);
    }
    phrases = phrases
        .child(button("add-phrase", "Add another phrase", ink, false).on_press(|s: &mut App| s.prefs.add_phrase()).self_start())
        .child(text("Say it just like this. \u{201c}Run work mode\u{201d} and \u{201c}please work mode\u{201d} work too.").font(t(11.0, 400.0)).color(ink.muted));
    body = body.child(phrases);

    // what to do
    let mut steps = col().key("steps-block").w_full().gap(10.0).child(label_text("Then Orbit will", ink));
    for (i, step) in d.steps.iter().enumerate() {
        steps = steps.child(step_card(i, step.kind, &step.edit, d.steps.len(), ink));
    }
    let mut adds = row().key("add-steps").gap(8.0).flex_wrap().gaps(8.0, 8.0);
    for kind in StepKind::ALL {
        adds = adds.child(button(&format!("add-{}", kind.label()), &format!("+ {}", kind.label()), ink, false).on_press(move |s: &mut App| s.prefs.add_step(kind)));
    }
    steps = steps.child(adds);
    if d.sends_keys() {
        steps = steps.child(
            text("This presses keys or types, so Orbit asks you \u{201c}yes or no\u{201d} before it runs, listing exactly what it will do.")
                .font(t(11.0, 400.0))
                .color(ink.muted)
                .wrap()
                .key("asks-first"),
        );
    }
    body = body.child(steps);

    if !d.errors.is_empty() {
        body = body.child(
            col()
                .key("errors")
                .w_full()
                .padding(14.0)
                .gap(4.0)
                .radius(10.0)
                .border(1.0, hex(BAD).alpha(0.5))
                .children(d.errors.iter().enumerate().map(|(i, e)| text(e.clone()).key(format!("error-{i}")).font(t(12.0, 400.0)).color(hex(BAD)).wrap())),
        );
    }
    body.child(
        row()
            .key("editor-actions")
            .gap(10.0)
            .child(button("save-command", "Save", ink, true).on_press(|s: &mut App| {
                s.prefs.save_draft();
            }))
            .child(button("cancel-command", "Cancel", ink, false).on_press(|s: &mut App| s.prefs.cancel_draft())),
    )
}

fn label_text(label: &str, ink: &Ink) -> El {
    text(label).font(t(11.0, 500.0).tracking(1.1)).color(ink.muted)
}

fn step_card(i: usize, kind: StepKind, edit: &rune::TextEdit, count: usize, ink: &Ink) -> El {
    let mut kinds = row().key(format!("step-{i}-kinds")).gap(6.0).flex_wrap().gaps(6.0, 6.0);
    for k in StepKind::ALL {
        kinds = kinds.child(chip(format!("step-{i}-kind-{}", k.label()), k.label(), k == kind, ink).on_press(move |s: &mut App| s.prefs.set_step_kind(i, k)));
    }
    let mut tools = row().key(format!("step-{i}-tools")).gap(2.0).items_center();
    if i > 0 {
        tools = tools.child(glyph_button(format!("step-{i}-up"), "\u{2191}", "Move this step up", ink).on_press(move |s: &mut App| s.prefs.move_step(i, -1)));
    }
    if i + 1 < count {
        tools = tools.child(glyph_button(format!("step-{i}-down"), "\u{2193}", "Move this step down", ink).on_press(move |s: &mut App| s.prefs.move_step(i, 1)));
    }
    if count > 1 {
        tools = tools.child(glyph_button(format!("step-{i}-remove"), "\u{d7}", "Remove this step", ink).on_press(move |s: &mut App| s.prefs.remove_step(i)));
    }
    col()
        .key(format!("step-{i}"))
        .w_full()
        .padding(14.0)
        .gap(10.0)
        .radius(10.0)
        .fill(hex("#0D0D10"))
        .border(1.0, ink.hair)
        .child(
            row()
                .key(format!("step-{i}-head"))
                .w_full()
                .gap(12.0)
                .items_center()
                .justify_between()
                .child(row().key(format!("step-{i}-title")).gap(10.0).items_center().child(text(format!("{}", i + 1)).font(t(12.0, 600.0).mono()).color(ink.muted)).child(kinds))
                .child(tools),
        )
        .child(field(format!("step-{i}-value"), edit, kind.example(), edit_access(move |s: &mut App| s.prefs.step_edit(i)), ink))
        .child(text(kind.hint()).font(t(11.0, 400.0)).color(ink.muted).wrap())
}

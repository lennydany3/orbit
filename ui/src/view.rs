//! The window: a black stage with the sphere, and the dev panel on the right. A faithful copy of the
//! design's layout, colours and type, built from Rune elements.

use rune::tok::{TextStyle, motion};
use rune::{Align, Color, Cx, Drag, El, Phase, col, effect, el, row, text};

use crate::app::{App, DEMO};
use crate::orbit::{OrbState, STATES, SphereConfig};

/// Marks that tell the compositor which sphere a slot is.
pub const STAGE_MARK: u32 = 1;
pub const OVERLAY_MARK: u32 = 2;

/// The big sphere: 5000 points, 1.4 px each, filling 60% of the stage's shorter side.
pub const STAGE: SphereConfig = SphereConfig { count: 5000, size: 1.4, scale: 0.6, fray: 1.0 };
/// The 72 px overlay orb: a lighter sphere (1400 points, 1.05 px).
pub const OVERLAY: SphereConfig = SphereConfig { count: 1400, size: 1.05, scale: 0.7, fray: 1.0 };

const PANEL_W: f32 = 320.0;

fn hex(s: &str) -> Color {
    rune::color::hex(s).unwrap_or(Color::WHITE)
}

/// The design's few colours.
struct Ink {
    fg: Color,
    soft: Color,
    muted: Color,
    panel: Color,
    hair: Color,
    hair_strong: Color,
}

impl Ink {
    fn new() -> Ink {
        Ink {
            fg: hex("#F2F3F5"),
            soft: hex("#B4B8BF"),
            muted: hex("#8A8F98"),
            panel: hex("#0A0A0C"),
            hair: Color::WHITE.alpha(0.08),
            hair_strong: Color::WHITE.alpha(0.22),
        }
    }
}

fn t(size: f32, weight: f32) -> TextStyle {
    TextStyle::new(size, weight)
}

pub fn view(s: &App, cx: &mut Cx<App>) -> El {
    cx.reduced_motion(s.orbit.reduced());
    let ink = Ink::new();
    row().key("window").w_full().h_full().fill(Color::BLACK).child(stage(s, &ink)).child(panel(s, &ink))
}

// ---- the stage ----------------------------------------------------------------------------

fn stage(s: &App, ink: &Ink) -> El {
    let state = s.orbit.state;
    let label = if s.orbit.reduced() { format!("{} (reduced motion)", state.label()) } else { state.label().to_owned() };

    let sphere = effect(STAGE_MARK).effect_data(STAGE.to_data()).key("sphere").w_full().h_full();

    let header = row()
        .key("title")
        .absolute(28.0, 24.0)
        .gap(12.0)
        .items_end()
        .child(text("Orbit").font(t(14.0, 600.0).tracking(0.56)).color(ink.fg))
        .child(text("Sphere states, motion review").font(t(12.0, 400.0).tracking(0.72)).color(ink.muted));

    let mut footer = col().key("footer").items_center().gap(18.0);
    if let Some(step) = s.demo_step().filter(|d| !d.cap.is_empty()) {
        footer = footer.child(
            col()
                .key("caption")
                .items_center()
                .gap(6.0)
                .child(text(step.who).font(t(11.0, 400.0).tracking(1.54)).color(ink.muted))
                .child(text(step.cap).font(t(18.0, 400.0)).color(ink.fg)),
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
        .child(
            col().key("overlay").absolute(0.0, 0.0).w_full().h_full().justify_end().items_center().pb(36.0).child(footer),
        )
}

// ---- the dev panel ------------------------------------------------------------------------

fn panel(s: &App, ink: &Ink) -> El {
    let body = col()
        .key("panel-body")
        .grow(1.0)
        .min_w(0.0)
        .px(20.0)
        .pt(22.0)
        .pb(28.0)
        .gap(24.0)
        .child(state_section(s, ink))
        .child(section(ink, "level", level_section(s, ink)))
        .child(section(ink, "demo", demo_section(s, ink)))
        .child(section(ink, "overlay", overlay_section(s, ink)))
        .child(section(ink, "reduced", reduced_section(s, ink)));

    row()
        .key("panel")
        .w(PANEL_W)
        .h_full()
        .fill(ink.panel)
        .child(el().key("edge").w(1.0).h_full().fill(ink.hair))
        .child(body)
}

/// A section after the first: a hairline above it and 20 px of room under the line.
fn section(ink: &Ink, key: &str, content: El) -> El {
    col().key(key).gap(0.0).child(el().key("rule").h(1.0).w_full().fill(ink.hair)).child(col().pt(20.0).child(content))
}

fn caption(ink: &Ink, label: &str) -> El {
    text(label).font(t(11.0, 400.0).tracking(1.32)).color(ink.muted)
}

fn state_section(s: &App, ink: &Ink) -> El {
    let mut grid = col().key("grid").gap(6.0).w_full();
    for pair in STATES.chunks(2) {
        let mut r = row().gap(6.0).w_full();
        for st in pair {
            r = r.child(state_button(*st, *st == s.orbit.state, ink));
        }
        if pair.len() == 1 {
            r = r.child(el().grow(1.0).min_w(0.0));
        }
        grid = grid.child(r);
    }
    col()
        .key("states")
        .gap(10.0)
        .child(
            row()
                .justify_between()
                .items_center()
                .child(caption(ink, "State"))
                .child(text("Keys 1\u{2013}0, M").font(t(11.0, 400.0).tracking(0.66)).color(ink.muted)),
        )
        .child(grid)
}

fn state_button(st: OrbState, on: bool, ink: &Ink) -> El {
    row()
        .key(format!("state-{}", st.id()))
        .role("button")
        .label(st.label())
        .focusable(true)
        .grow(1.0)
        .min_w(0.0)
        .h(34.0)
        .px(10.0)
        .items_center()
        .justify_between()
        .radius(8.0)
        .fill(if on { Color::WHITE.alpha(0.10) } else { Color::WHITE.alpha(0.02) })
        .border(1.0, if on { Color::WHITE.alpha(0.26) } else { ink.hair })
        .hover(Color::WHITE.alpha(0.04))
        .motion(motion::SNAPPY)
        .on_press_tx(move |s: &mut App, tx| s.pick(st, tx.clock()))
        .child(text(st.label()).font(t(13.0, 400.0)).color(if on { ink.fg } else { ink.soft }).a11y_hidden())
        .child(text(st.key()).font(t(11.0, 400.0).features("'tnum' 1")).color(ink.muted).a11y_hidden())
}

fn level_section(s: &App, ink: &Ink) -> El {
    col()
        .key("level-body")
        .gap(12.0)
        .child(
            row()
                .justify_between()
                .items_center()
                .child(caption(ink, "Level"))
                .child(text(format!("{:.2}", s.shown_level)).font(t(13.0, 400.0).features("'tnum' 1")).color(ink.fg)),
        )
        .child(slider(s.shown_level, ink))
        .child(switch_row(
            ink,
            "voice",
            "Simulate voice",
            "Speech-like level for listening and speaking",
            s.voice,
            |s: &mut App, _| s.voice = !s.voice,
        ))
}

fn demo_section(s: &App, ink: &Ink) -> El {
    let running = s.demo.is_some();
    let mut chips = row().key("chips").flex_wrap().gap(4.0);
    for (i, step) in DEMO.iter().filter(|d| !d.chip.is_empty()).enumerate() {
        let on = s.demo.is_some_and(|d| d.step == i);
        chips = chips.child(
            el().key(format!("chip-{i}"))
                .px(8.0)
                .py(3.0)
                .radius(999.0)
                .border(1.0, if on { ink.fg } else { Color::WHITE.alpha(0.10) })
                .fill(if on { ink.fg } else { Color::TRANSPARENT })
                .motion(motion::SNAPPY)
                .child(
                    text(step.chip)
                        .font(t(11.0, 400.0).tracking(0.22))
                        .color(if on {
                            Color::BLACK
                        } else if running {
                            ink.soft
                        } else {
                            ink.muted
                        }),
                ),
        );
    }
    col()
        .key("demo-body")
        .gap(12.0)
        .child(switch_row(
            ink,
            "demo-switch",
            "Auto demo",
            "One full turn, looping",
            running,
            |s: &mut App, t| if s.demo.is_some() { s.stop_demo() } else { s.start_demo(t) },
        ))
        .child(chips)
}

fn overlay_section(_s: &App, ink: &Ink) -> El {
    let orb = el()
        .key("orb")
        .size(72.0, 72.0)
        .radius(36.0)
        .clip_rounded(36.0)
        .fill(hex("#0A0A0C").alpha(0.72))
        .border(1.0, ink.hair)
        .child(effect(OVERLAY_MARK).effect_data(OVERLAY.to_data()).key("overlay-sphere").size(70.0, 70.0));
    row()
        .key("overlay-body")
        .items_center()
        .gap(16.0)
        .child(orb)
        .child(
            col()
                .gap(2.0)
                .child(text("Overlay orb").font(t(13.0, 400.0)).color(ink.fg))
                .child(text("72 \u{d7} 72, mirrors the current state").font(t(12.0, 400.0)).color(ink.muted)),
        )
}

fn reduced_section(s: &App, ink: &Ink) -> El {
    switch_row(
        ink,
        "reduced-switch",
        "Reduced motion",
        "Static sphere, states shown by brightness only",
        s.orbit.reduced(),
        |s: &mut App, t| s.set_reduced(!s.orbit.reduced(), t),
    )
}

// ---- controls -----------------------------------------------------------------------------

/// A label with a hint underneath and a 34 x 20 switch on the right.
fn switch_row(ink: &Ink, key: &str, title: &str, hint: &str, on: bool, flip: fn(&mut App, f64)) -> El {
    let (w, h, k) = (34.0, 20.0, 12.0);
    let knob_x = if on { 18.0 } else { 4.0 };
    let track = el()
        .key("track")
        .size(w, h)
        .radius(h / 2.0)
        .fill(if on { ink.fg } else { Color::WHITE.alpha(0.04) })
        .border(1.0, if on { ink.fg } else { ink.hair_strong })
        .motion(motion::SNAPPY)
        .child(
            el().key("knob")
                .size(k, k)
                .absolute(knob_x, 4.0)
                .radius(k / 2.0)
                .fill(if on { Color::BLACK } else { ink.muted })
                .a11y_hidden(),
        )
        .a11y_hidden();
    row()
        .key(key)
        .role("switch")
        .label(title)
        .checked(on)
        .focusable(true)
        .w_full()
        .items_center()
        .justify_between()
        .gap(12.0)
        .on_press_tx(move |s: &mut App, tx| flip(s, tx.clock()))
        .child(
            col()
                .gap(2.0)
                .grow(1.0)
                .min_w(0.0)
                .child(text(title).font(t(13.0, 400.0)).color(ink.fg).a11y_hidden())
                .child(text(hint).font(t(12.0, 400.0)).color(ink.muted).wrap().a11y_hidden()),
        )
        .child(track)
}

/// The level slider: a 4 px track filled to the value, with a 14 px knob.
fn slider(v: f32, ink: &Ink) -> El {
    let (w, h, k, bar) = (280.0, 20.0, 14.0, 4.0);
    let travel = w - k;
    let to_value = move |x: f32| ((x - k / 2.0) / travel).clamp(0.0, 1.0);
    let knob_x = v.clamp(0.0, 1.0) * travel;
    el().key("slider")
        .role("slider")
        .label("Level")
        .range_value(f64::from(v), 0.0, 1.0)
        .range_step(0.01)
        .a11y_value(format!("{v:.2}"))
        .on_step(|s: &mut App, steps, _| {
            let v = (s.shown_level + steps as f32 * 0.01).clamp(0.0, 1.0);
            s.set_manual_level(v);
        })
        .size(w, h)
        .on_drag(move |s: &mut App, d: &mut Drag, tx| match d.phase {
            Phase::Down | Phase::Move => {
                s.set_manual_level(to_value(d.x));
                tx.instant();
            }
            Phase::Up => {}
        })
        .child(el().key("track").size(w, bar).absolute(0.0, (h - bar) / 2.0).radius(bar / 2.0).fill(Color::WHITE.alpha(0.18)).a11y_hidden())
        .child(el().key("fill").size(knob_x + k / 2.0, bar).absolute(0.0, (h - bar) / 2.0).radius(bar / 2.0).fill(ink.fg).a11y_hidden())
        .child(el().key("knob").size(k, k).absolute(knob_x, (h - k) / 2.0).radius(k / 2.0).fill(ink.fg).a11y_hidden())
}

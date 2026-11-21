//! The floating orb: what shows over your work while Orbit is doing something. A small dark circle with the sphere in it and,
//! when Orbit needs a yes or no, the question above it, or a card with a Stop button when a reminder or timer is ringing.
//! What was said is not shown here (the window has it).
//!
//! This is its own window (a Rune overlay, so it never takes focus and clicks pass straight through it), so it has its own
//! state: a copy of what the hub knows, pushed here whenever it changes. The only part that takes clicks is the question's
//! buttons.

use rune::tok::TextStyle;
use rune::{Align, AppHandle, Color, Cx, El, col, effect, el, row, text};

use crate::app::{App, Ask, Ring};
use crate::layout::{CARD_W, Place, SPARE};
use crate::orbit::{HasOrbit, Orbit};
use crate::settings;
use crate::view::{Ink, OVERLAY, OVERLAY_MARK, hex};

/// The overlay's size in points: wide enough for the question's card, and `HEIGHT` tall for the orb alone at the middle size.
/// (The extra height while a question is open.)
pub use crate::layout::{ASK_EXTRA, WIDTH};
pub const HEIGHT: f32 = 112.0 + SPARE;

/// Whether the orb may show up in screenshots and screen recordings. By default it may not: when you tell Orbit to take a screenshot, the
/// picture should show what you were looking at, not Orbit's own status orb in the corner. `ORBIT_ORB_CAPTURABLE=1` puts the orb back in
/// (for a screen recording of Orbit, say).
pub fn orb_capturable() -> bool {
    capturable_from(std::env::var("ORBIT_ORB_CAPTURABLE").ok().as_deref())
}

fn capturable_from(value: Option<&str>) -> bool {
    value.is_some_and(|v| matches!(v.trim().to_lowercase().as_str(), "1" | "on" | "true" | "yes"))
}

#[derive(Clone)]
pub struct Face {
    pub orbit: Orbit,
    pub ask: Option<Ask>,
    /// A reminder or timer that is ringing (shown when there is no question: a question is the more urgent).
    pub ring: Option<Ring>,
    /// Whether the yes and no chords are listening for the answer (they only are while a question is open).
    pub keys: bool,
    /// Those two chords, as written in the settings file.
    pub yes: String,
    pub no: String,
    /// How the buttons reach the hub.
    pub hub: Option<AppHandle<App>>,
    /// Where the window is on the screen, how big the orb is and where it sits in the window (see `layout`).
    pub place: Place,
}

impl HasOrbit for Face {
    fn orbit(&self) -> &Orbit {
        &self.orbit
    }
}

impl Face {
    pub fn new(hub: Option<AppHandle<App>>) -> Face {
        Face { orbit: Orbit::new(), ask: None, ring: None, keys: false, yes: settings::DEFAULT_YES.into(), no: settings::DEFAULT_NO.into(), hub, place: Place::default() }
    }

    /// The overlay's size for what it shows now.
    pub fn size(&self) -> (f32, f32) {
        (WIDTH, self.place.diameter + SPARE + if self.ask.is_some() || self.ring.is_some() { ASK_EXTRA } else { 0.0 })
    }

    /// What decides whether the overlay needs redrawing: everything the view reads, with the level in whole percents (it
    /// changes every few milliseconds and the sphere smooths it anyway).
    pub fn key(&self) -> FaceKey {
        FaceKey {
            epoch: self.orbit.epoch,
            level: (self.orbit.level * 50.0).round() as i32,
            ask: self.ask.as_ref().map(|a| (a.id, a.sent)),
            ring: self.ring.as_ref().map(|r| (r.id.clone(), r.sent)),
            keys: self.keys,
            chords: (self.yes.clone(), self.no.clone()),
            place: self.place,
        }
    }

    /// Does this face need the answer chords listening? (A question that is open and not yet answered.)
    pub fn wants_answer_keys(&self) -> bool {
        self.ask.as_ref().is_some_and(|a| a.sent.is_none())
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct FaceKey {
    epoch: u32,
    level: i32,
    ask: Option<(i64, Option<bool>)>,
    ring: Option<(String, bool)>,
    keys: bool,
    chords: (String, String),
    place: Place,
}

fn t(size: f32, weight: f32) -> TextStyle {
    TextStyle::new(size, weight)
}

pub fn view(f: &Face, cx: &mut Cx<Face>) -> El {
    cx.reduced_motion(f.orbit.reduced());
    let ink = Ink::new();

    let d = f.place.diameter;
    let orb = el()
        .key("orb")
        .size(d, d)
        .radius(d / 2.0)
        .clip_rounded(d / 2.0)
        .fill(hex("#0A0A0C").alpha(0.80))
        .border(1.0, ink.hair_strong)
        .child(effect(OVERLAY_MARK).effect_data(OVERLAY.to_data()).key("sphere").size(d - 2.0, d - 2.0));

    let line = row().key("line").w_full().items_center().pl(f.place.orb_x).child(orb);

    // The orb is at the bottom of its window with the card above it, or at the top with the card below it (see `layout`).
    let mut root = col().key("face").w_full().h_full().items_start().gap(14.0);
    root = if f.place.flip { root.justify_start() } else { root.justify_end() };
    let card = match (&f.ask, &f.ring) {
        (Some(ask), _) => {
            let keys = f.keys.then(|| format!("{} yes \u{b7} {} no", settings::compact(&f.yes), settings::compact(&f.no)));
            Some(row().key("ask-line").w_full().pl(f.place.card_x).child(ask_card(ask, keys, &ink)))
        }
        (None, Some(ring)) => Some(row().key("ring-line").w_full().pl(f.place.card_x).child(ring_card(ring, &ink))),
        (None, None) => None,
    };
    match (card, f.place.flip) {
        (Some(card), false) => root.child(card).child(line),
        (Some(card), true) => root.child(line).child(card),
        (None, _) => root.child(line),
    }
}

/// Orbit is asking before it does something risky. The card and its two buttons are the only parts that take clicks.
fn ask_card(ask: &Ask, keys: Option<String>, ink: &Ink) -> El {
    let waiting = ask.sent.is_some();
    let answers = if waiting {
        text("Sent. Waiting for Orbit\u{2026}").font(t(12.0, 400.0)).color(ink.muted)
    } else {
        row().key("answers").gap(8.0).child(answer("ask-no", "No", false, ink)).child(answer("ask-yes", "Yes", true, ink))
    };
    col()
        .key("ask")
        .role("dialog")
        .label("Orbit asks")
        .hit()
        .w(CARD_W)
        .padding(18.0)
        .gap(12.0)
        .items_center()
        .radius(14.0)
        .fill(hex("#0A0A0C").alpha(0.96))
        .border(1.0, ink.hair_strong)
        .child(text("Orbit asks").font(t(11.0, 400.0).tracking(1.54)).color(ink.muted))
        .child(
            text(format!("Do you want me to {}?", ask.action))
                .font(t(15.0, 400.0))
                .color(ink.fg)
                .align(Align::Center)
                .wrap()
                .max_w(CARD_W - 40.0),
        )
        .child(answers)
        .child(text(keys.unwrap_or_default()).font(t(11.0, 400.0)).color(ink.muted))
}

/// A reminder or timer is ringing. The card and its Stop button are the only parts that take clicks.
fn ring_card(ring: &Ring, ink: &Ink) -> El {
    let stop = if ring.sent {
        text("Stopping\u{2026}").font(t(12.0, 400.0)).color(ink.muted)
    } else {
        row()
            .key("ring-stop")
            .role("button")
            .label("Stop")
            .hit()
            .h(32.0)
            .w(120.0)
            .center()
            .radius(8.0)
            .fill(ink.fg)
            .border(1.0, ink.fg)
            .on_press(|f: &mut Face| {
                if let Some(hub) = f.hub.clone() {
                    hub.update(move |app, tx| {
                        app.stop_alarm();
                        app.sync_shell(crate::clock::now(tx.clock()));
                    });
                }
            })
            .child(text("Stop").font(t(13.0, 500.0)).color(Color::BLACK).a11y_hidden())
    };
    col()
        .key("ring")
        .role("dialog")
        .label(ring.heading())
        .hit()
        .w(CARD_W)
        .padding(18.0)
        .gap(12.0)
        .items_center()
        .radius(14.0)
        .fill(hex("#0A0A0C").alpha(0.96))
        .border(1.0, ink.hair_strong)
        .child(text(ring.heading()).font(t(11.0, 400.0).tracking(1.54)).color(ink.muted))
        .child(text(&ring.text).font(t(15.0, 400.0)).color(ink.fg).align(Align::Center).wrap().max_w(CARD_W - 40.0))
        .child(stop)
        .child(text("or say \u{201c}ok thanks\u{201d}").font(t(11.0, 400.0)).color(ink.muted))
}

fn answer(key: &str, label: &str, yes: bool, ink: &Ink) -> El {
    row()
        .key(key)
        .role("button")
        .label(label)
        .hit()
        .h(32.0)
        .w(84.0)
        .center()
        .radius(8.0)
        .fill(if yes { ink.fg } else { Color::TRANSPARENT })
        .border(1.0, if yes { ink.fg } else { ink.hair_strong })
        .hover(Color::WHITE.alpha(if yes { 0.0 } else { 0.06 }))
        .on_press(move |f: &mut Face| {
            if let Some(hub) = f.hub.clone() {
                hub.update(move |app, tx| {
                    app.answer_confirm(yes);
                    app.sync_shell(crate::clock::now(tx.clock()));
                });
            }
        })
        .child(text(label).font(t(13.0, 500.0)).color(if yes { Color::BLACK } else { ink.fg }).a11y_hidden())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::orbit::OrbState;

    fn ask() -> Ask {
        Ask { id: 3, action: "close window Notepad".into(), question: "q".into(), sent: None }
    }

    #[test]
    fn the_orb_stays_out_of_screenshots_unless_asked() {
        assert!(!capturable_from(None), "by default Orbit's own screenshots do not show its orb");
        for on in ["1", "on", "TRUE", " yes "] {
            assert!(capturable_from(Some(on)), "{on:?}");
        }
        for off in ["", "0", "off", "false", "no", "maybe"] {
            assert!(!capturable_from(Some(off)), "{off:?}");
        }
    }

    #[test]
    fn the_overlay_grows_to_hold_a_question() {
        let mut f = Face::new(None);
        assert_eq!(f.size(), (WIDTH, HEIGHT));
        f.ask = Some(ask());
        assert_eq!(f.size(), (WIDTH, HEIGHT + ASK_EXTRA));
    }

    #[test]
    fn the_overlay_follows_the_size_of_the_orb() {
        let mut f = Face::new(None);
        f.place.diameter = 152.0;
        assert_eq!(f.size(), (WIDTH, 152.0 + SPARE));
        f.ask = Some(ask());
        assert_eq!(f.size(), (WIDTH, 152.0 + SPARE + ASK_EXTRA));
    }

    #[test]
    fn moving_or_resizing_the_orb_redraws_it() {
        let base = Face::new(None).key();
        let mut moved = Face::new(None);
        moved.place = crate::layout::place(crate::settings::OrbLook::default().at(0.2, 0.3), crate::layout::Screen::default(), false);
        assert_ne!(moved.key(), base);
        let mut bigger = Face::new(None);
        bigger.place.diameter = 152.0;
        assert_ne!(bigger.key(), base);
    }

    #[test]
    fn the_key_ignores_the_level_jitter_but_sees_everything_the_view_reads() {
        let mut f = Face::new(None);
        let base = f.key();
        f.orbit.level = 0.004;
        assert_eq!(f.key(), base, "a hair of level is not a redraw");
        f.orbit.level = 0.5;
        assert_ne!(f.key(), base);

        let mut g = Face::new(None);
        g.orbit.set_state(OrbState::Thinking, 1.0);
        assert_ne!(g.key(), base, "a new state");

        let mut a = Face::new(None);
        a.ask = Some(ask());
        let asked = a.key();
        a.ask.as_mut().unwrap().sent = Some(true);
        assert_ne!(a.key(), asked, "answered: the buttons go away");
    }

    #[test]
    fn the_answer_keys_listen_only_for_a_question_that_is_open_and_unanswered() {
        let mut f = Face::new(None);
        assert!(!f.wants_answer_keys(), "no question");
        f.ask = Some(ask());
        assert!(f.wants_answer_keys());
        f.ask.as_mut().unwrap().sent = Some(false);
        assert!(!f.wants_answer_keys(), "already answered: release the keys");
        f.ask = None;
        assert!(!f.wants_answer_keys());
    }

    fn ring() -> Ring {
        Ring { id: "r-1".into(), title: "Pasta".into(), kind: "timer".into(), text: "Your timer is up.".into(), sent: false }
    }

    #[test]
    fn the_overlay_grows_to_hold_a_ringing_reminder_and_redraws_when_stop_is_pressed() {
        let mut f = Face::new(None);
        let calm = f.key();
        f.ring = Some(ring());
        assert_eq!(f.size(), (WIDTH, HEIGHT + ASK_EXTRA));
        let ringing = f.key();
        assert_ne!(ringing, calm);
        f.ring.as_mut().unwrap().sent = true;
        assert_ne!(f.key(), ringing, "Stop pressed: the button gives way to the waiting line");
        assert!(!f.wants_answer_keys(), "the yes and no chords belong to a question, not to a ringing reminder");
    }

    #[test]
    fn the_hint_appearing_redraws_the_card() {
        let mut f = Face::new(None);
        f.ask = Some(ask());
        let without = f.key();
        f.keys = true;
        assert_ne!(f.key(), without);
    }
}

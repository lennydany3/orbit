//! The installer's screens: a title bar you can drag, the stage on the left (the particle sphere inside Orbit's ring) and the
//! words and buttons on the right. Same colours, type and buttons as Orbit's own window.

use rune::{Align, Color, Cx, El, col, edit_access, effect, el, icon, row, text, text_input};

use super::install::Phase;
use super::ring::{self, Geometry, Look};
use super::{Page, Setup, VERSION};
use crate::icons;
use crate::orbit::SphereConfig;
use crate::view::Ink;
use crate::widgets::{Look as ButtonLook, button, switch, t};

/// The installer's sphere: fewer points than the app's big stage, sized to sit inside the ring.
const SPHERE: SphereConfig = SphereConfig { count: 3200, size: 1.25, scale: 0.47, fray: 1.0 };
const SPHERE_MARK: u32 = 7;
/// The stage's side, and the ring's (the sphere fills its middle).
const STAGE_W: f32 = 340.0;
const RING: f32 = 330.0;

pub fn view(s: &Setup, _cx: &mut Cx<Setup>) -> El {
    let ink = Ink::new();
    col()
        .key("setup")
        .w_full()
        .h_full()
        .fill(ink.bg)
        .border(1.0, ink.hair_strong)
        .child(title_bar(s, &ink))
        .child(row().key("body").w_full().grow(1.0).min_h(0.0).child(stage(s)).child(panel(s, &ink)))
}

// ---- the title bar ------------------------------------------------------------------------------------------------------------

fn title_bar(s: &Setup, ink: &Ink) -> El {
    let mut bar = row()
        .key("title-bar")
        .w_full()
        .h(44.0)
        .pl(16.0)
        .pr(6.0)
        .gap(10.0)
        .items_center()
        .window_drag()
        .child(ring::logo(18.0, ink.fg))
        .child(text("Orbit").font(t(13.0, 600.0)).color(ink.fg))
        .child(text(if s.updating() { "Update" } else { "Setup" }).font(t(13.0, 400.0)).color(ink.muted))
        .child(el().grow(1.0))
        .child(caption_button("minimize", &icons::MINUS, "Minimize", ink).on_press(|s: &mut Setup| s.minimize()));
    if s.can_close() {
        bar = bar.child(caption_button("close", &icons::CLOSE, "Close", ink).on_press(|s: &mut Setup| s.quit()));
    }
    bar
}

fn caption_button(key: &str, glyph: &'static rune::Icon, label: &str, ink: &Ink) -> El {
    row()
        .key(key)
        .role("button")
        .label(label)
        .focusable(true)
        .size(34.0, 30.0)
        .center()
        .radius(7.0)
        .hover(Color::WHITE.alpha(0.08))
        .child(icon(glyph, 14.0).color(ink.muted))
}

// ---- the stage: the sphere inside the ring --------------------------------------------------------------------------------------

/// How the ring looks on each screen: the satellite orbits while waiting, rides the lit edge while installing, and settles where
/// the logo has it once Orbit is in.
fn ring_look(s: &Setup, ink: &Ink) -> Look {
    let dim = Color::WHITE.alpha(0.16);
    match s.page {
        Page::Installing => Look { lit: s.shown, satellite: ring::edge(s.shown), trail: 1.0, on: ink.fg, off: dim },
        Page::Done => {
            // From the lit edge (full circle: twelve o'clock) round to the logo's place, then still.
            let k = s.done_at.map_or(1.0, |d| ((s.t - d) / 0.9).clamp(0.0, 1.0) as f32);
            let ease = 1.0 - (1.0 - k).powi(3);
            let from = ring::edge(1.0);
            let to = ring::logo_angle() + std::f32::consts::TAU;
            Look { lit: 1.0, satellite: from + (to - from) * ease, trail: 1.0 - k, on: ink.fg, off: dim }
        }
        Page::Failed => Look { lit: s.shown, satellite: ring::edge(s.shown), trail: 0.0, on: ink.bad, off: dim },
        _ => Look { lit: 1.0, satellite: ring::orbiting(s.t), trail: 0.5, on: Color::WHITE.alpha(0.9), off: dim },
    }
}

fn stage(s: &Setup) -> El {
    let ink = Ink::new();
    let sphere = effect(SPHERE_MARK).effect_data(SPHERE.to_data()).key("sphere").w_full().h_full();
    let ring_layer = col()
        .key("ring-layer")
        .absolute(0.0, 0.0)
        .w_full()
        .h_full()
        .center()
        .child(ring::mark(RING, Geometry::STAGE, ring_look(s, &ink)).key("ring"));
    col()
        .key("stage")
        .w(STAGE_W)
        .h_full()
        .mx(10.0)
        .mb(10.0)
        .radius(14.0)
        .fill(Color::BLACK)
        .border(1.0, ink.hair)
        .child(sphere)
        .child(ring_layer)
}

// ---- the right-hand panel ---------------------------------------------------------------------------------------------------------

fn panel(s: &Setup, ink: &Ink) -> El {
    let body = match s.page {
        Page::Welcome => welcome(s, ink),
        Page::Options => options(s, ink),
        Page::Keys => keys(s, ink),
        Page::Installing => installing(s, ink),
        Page::Done => done(s, ink),
        Page::Failed => failed(s, ink),
    };
    col()
        .key("panel")
        .grow(1.0)
        .min_w(0.0)
        .h_full()
        .px(36.0)
        .pt(30.0)
        .pb(28.0)
        .child(body)
        .children(s.note.as_ref().map(|n| text(n).font(t(12.0, 400.0)).color(ink.bad).wrap().key("note")))
}

fn caps(label: &str, ink: &Ink) -> El {
    text(label).font(t(11.0, 500.0).tracking(1.54)).color(ink.accent_text)
}

fn heading(words: &str, ink: &Ink) -> El {
    text(words).font(t(30.0, 650.0)).color(ink.fg).wrap()
}

fn body(words: &str, ink: &Ink) -> El {
    text(words).font(t(14.0, 400.0).lh(21.0)).color(ink.muted).wrap()
}

/// A screen: a caps label, a heading and words at the top; the buttons at the bottom right.
fn screen(key: &str, top: Vec<El>, buttons: Vec<El>) -> El {
    col()
        .key(key)
        .w_full()
        .grow(1.0)
        .min_h(0.0)
        .child(col().key("top").w_full().gap(12.0).children(top))
        .child(el().grow(1.0))
        .child(row().key("buttons").w_full().gap(8.0).justify_end().items_center().children(buttons))
}

fn primary(key: &str, label: &str, ink: &Ink) -> El {
    button(key, label, ink, ButtonLook::Primary).h(38.0).px(22.0)
}

fn ghost(key: &str, label: &str, ink: &Ink) -> El {
    button(key, label, ink, ButtonLook::Plain).h(38.0).px(18.0)
}

fn fine_print(words: &str, ink: &Ink) -> El {
    text(words).font(t(11.5, 400.0)).color(ink.muted.alpha(0.8)).wrap()
}

fn welcome(s: &Setup, ink: &Ink) -> El {
    let (label, title, words, go) = match &s.installed {
        Some(old) => (
            "UPDATE".to_owned(),
            "A new Orbit is here".to_owned(),
            format!(
                "Orbit {} is on this PC. This brings it up to {VERSION}. Your settings, notes, reminders and keys stay just as they are.",
                if old.is_empty() { "(an older version)" } else { old }
            ),
            "Update",
        ),
        None => (
            "WELCOME".to_owned(),
            "Meet Orbit".to_owned(),
            "Your voice assistant for this PC. Say \u{201c}hey orbit\u{201d} or hold Right Ctrl, and it opens apps, plays your music, sets reminders and timers, takes notes, and chats with you.".to_owned(),
            "Install",
        ),
    };
    let mut buttons = Vec::new();
    if s.installed.is_none() {
        buttons.push(ghost("to-options", "Options", ink).on_press(|s: &mut Setup| s.page = Page::Options));
    }
    buttons.push(primary("install", go, ink).on_press(|s: &mut Setup| s.next_from_welcome()));
    screen(
        "welcome",
        vec![
            caps(&label, ink),
            heading(&title, ink),
            body(&words, ink),
            el().h(6.0),
            fact_row(ink),
            el().h(2.0),
            fine_print(&format!("Version {VERSION} \u{00b7} into {}", s.dir.text), ink),
        ],
        buttons,
    )
}

fn fact_row(ink: &Ink) -> El {
    let fact = |key: &str, words: &str| {
        row()
            .key(key)
            .gap(6.0)
            .items_center()
            .child(icon(&icons::CHECK, 13.0).color(ink.accent_text))
            .child(text(words).font(t(12.0, 400.0)).color(ink.fg))
    };
    row()
        .key("facts")
        .gap(16.0)
        .child(fact("fact-account", "Just for you"))
        .child(fact("fact-admin", "No admin rights"))
        .child(fact("fact-voice", "Voice stays local"))
}

fn options(s: &Setup, ink: &Ink) -> El {
    let folder = text_input(&s.dir, t(13.0, 400.0))
        .key("dir-input")
        .single_line()
        .grow(1.0)
        .min_w(0.0)
        .color(ink.fg)
        .on_edit(edit_access(|s: &mut Setup| &mut s.dir));
    let folder_box = row().key("dir").w_full().h(38.0).px(12.0).items_center().radius(8.0).fill(ink.field).border(1.0, Color::WHITE.alpha(0.12)).child(folder);
    let toggle = |key: &'static str, on: bool, title: &str, detail: &str, flip: fn(&mut Setup)| {
        row()
            .key(key)
            .w_full()
            .gap(14.0)
            .items_center()
            .py(4.0)
            .child(
                col()
                    .key(format!("{key}-words"))
                    .grow(1.0)
                    .min_w(0.0)
                    .gap(2.0)
                    .child(text(title).font(t(13.5, 500.0)).color(ink.fg))
                    .child(text(detail).font(t(12.0, 400.0)).color(ink.muted).wrap()),
            )
            .child(switch(format!("{key}-switch"), on, title, ink).on_press(move |s: &mut Setup| flip(s)))
    };
    screen(
        "options",
        vec![
            caps("OPTIONS", ink),
            heading("Make it yours", ink),
            el().h(2.0),
            text("Install folder").font(t(12.0, 500.0)).color(ink.muted),
            folder_box,
            el().h(6.0),
            toggle("startup", s.startup, "Start when I sign in", "Orbit waits in the tray, ready for \u{201c}hey orbit\u{201d}.", |s| s.startup = !s.startup),
            toggle("desktop", s.desktop, "Desktop shortcut", "An Orbit icon on your desktop.", |s| s.desktop = !s.desktop),
        ],
        vec![
            ghost("back", "Back", ink).on_press(|s: &mut Setup| s.page = Page::Welcome),
            primary("install", "Install", ink).on_press(|s: &mut Setup| s.next_from_welcome()),
        ],
    )
}

fn keys(s: &Setup, ink: &Ink) -> El {
    let input = text_input(&s.key, t(13.0, 400.0))
        .key("key-input")
        .single_line()
        .grow(1.0)
        .min_w(0.0)
        .color(ink.fg)
        .placeholder("gsk_\u{2026}", ink.muted)
        .on_edit(edit_access(|s: &mut Setup| &mut s.key));
    let field = row().key("key").w_full().h(38.0).px(12.0).items_center().radius(8.0).fill(ink.field).border(1.0, Color::WHITE.alpha(0.12)).child(input);
    let has_key = !s.key.text.trim().is_empty();
    screen(
        "keys",
        vec![
            caps("OPTIONAL", ink),
            heading("Connect Orbit's brain", ink),
            body("Orbit chats and handles harder requests with Groq, a free service. Paste a key from console.groq.com/keys, or skip this and add it later from the tray menu (API keys).", ink),
            el().h(4.0),
            text("Groq key").font(t(12.0, 500.0)).color(ink.muted),
            field,
            fine_print("Saved only on this PC, in your Orbit folder.", ink),
        ],
        vec![
            ghost("skip", "Skip", ink).on_press(|s: &mut Setup| {
                s.key = rune::TextEdit::new("");
                s.start();
            }),
            primary("continue", if has_key { "Install" } else { "Install without it" }, ink).on_press(|s: &mut Setup| s.start()),
        ],
    )
}

fn installing(s: &Setup, ink: &Ink) -> El {
    let p = s.progress();
    let status = match p.phase {
        Phase::Unpacking => "Unpacking Orbit\u{2026}".to_owned(),
        Phase::Preparing => "Getting your PC ready\u{2026}".to_owned(),
        Phase::Copying if p.files_total > 0 => format!("Copying files \u{00b7} {} of {}", p.files_done.min(p.files_total), p.files_total),
        Phase::Copying => "Copying files\u{2026}".to_owned(),
        Phase::Finishing | Phase::Done => "Finishing up\u{2026}".to_owned(),
        Phase::Failed => "Stopping\u{2026}".to_owned(),
    };
    let percent = (s.shown * 100.0).round().clamp(0.0, 100.0) as u32;
    let bar = row()
        .key("bar")
        .w_full()
        .h(3.0)
        .radius(2.0)
        .fill(Color::WHITE.alpha(0.1))
        .child(el().key("bar-fill").h_full().w_frac(s.shown.clamp(0.0, 1.0)).radius(2.0).fill(ink.fg));
    screen(
        "installing",
        vec![
            caps(if s.updating() { "UPDATING" } else { "INSTALLING" }, ink),
            text(format!("{percent}%")).font(t(56.0, 300.0)).color(ink.fg).key("percent"),
            body(&status, ink),
            el().h(8.0),
            bar,
        ],
        vec![fine_print(
            if s.updating() { "Orbit starts again on its own when this is done." } else { "The voice downloads the first time Orbit starts, once." },
            ink,
        )
        .align(Align::End)],
    )
}

fn done(s: &Setup, ink: &Ink) -> El {
    if s.mode != super::Mode::Interactive {
        return screen("done", vec![caps("UPDATED", ink), heading("Orbit is up to date", ink), body("Starting Orbit again\u{2026}", ink)], vec![]);
    }
    screen(
        "done",
        vec![
            caps("ALL SET", ink),
            heading("Orbit is ready", ink),
            body("It lives in the tray, by the clock. Say \u{201c}hey orbit\u{201d}, or hold Right Ctrl and talk.", ink),
        ],
        vec![
            ghost("close-done", "Close", ink).on_press(|s: &mut Setup| s.quit()),
            primary("start", "Start Orbit", ink).on_press(|s: &mut Setup| s.launch()),
        ],
    )
}

fn failed(s: &Setup, ink: &Ink) -> El {
    let p = s.progress();
    let why = p.error.unwrap_or_else(|| "something went wrong".into());
    let mut buttons = vec![ghost("close-failed", "Close", ink).on_press(|s: &mut Setup| s.quit())];
    if p.log.is_some() {
        buttons.insert(0, ghost("log", "Open the log", ink).on_press(|s: &mut Setup| s.open_log()));
    }
    buttons.push(primary("retry", "Try again", ink).on_press(|s: &mut Setup| {
        s.page = Page::Welcome;
        s.start();
    }));
    screen(
        "failed",
        vec![
            caps("SOMETHING WENT WRONG", ink),
            heading("Orbit wasn't installed", ink),
            body(&format!("{}{}.", why[..1].to_uppercase(), &why[1..]), ink),
            fine_print("Nothing was changed that could stop Orbit from being installed again.", ink),
        ],
        buttons,
    )
}

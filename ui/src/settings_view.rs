//! The Settings page, drawn: tabs along the top (General, Shortcuts, Sounds, Orb) and the chosen one below. All the rules live in `editor`;
//! this only shows its state and turns clicks into calls on it. (Your own commands are a page of their own: `commands_view`.)

use rune::{Color, Drag, El, Phase, PointerButton, PopupMenu, ScrollSpec, Tx, col, el, icon, row, text};

use crate::app::App;
use crate::devices::Scan;
use crate::editor::{Field, SPOTS, Section, describe_field, spot_at};
use crate::icons;
use crate::layout;
use crate::settings::{self, OrbSize};
use crate::view::Ink;
use crate::widgets::{Look, button, card, chip, divider, icon_button, notice, page_header, select_box, setting_row, slider, slider_percent, switch, t, tab, tab_strip};

pub(crate) fn page(s: &App, ink: &Ink) -> El {
    let tabs: Vec<El> = Section::ALL
        .into_iter()
        .map(|sec| tab(format!("tab-{}", sec.label().to_lowercase()), sec.label(), s.prefs.section == sec, None, ink).on_press(move |s: &mut App| s.prefs.select(sec)))
        .collect();
    let strip = tab_strip("tabs", tabs, ink);
    let content = match s.prefs.section {
        Section::General => general(s, ink),
        Section::Hotkeys => hotkeys(s, ink),
        Section::Sounds => sounds(s, ink),
        Section::Customization => customization(s, ink),
    };
    let mut root = col().key("settings").w_full().h_full().child(page_header("Settings", None, "Orbit's audio, the way you talk to it, and its shortcuts.", None, ink)).child(
        col()
            .key("settings-scroll")
            .grow(1.0)
            .min_h(0.0)
            .w_full()
            .scroll(ScrollSpec { stick_bottom: false })
            .child(col().key("settings-content").w_full().max_w(1000.0).px(28.0).py(22.0).gap(20.0).child(row().key("tabs-row").child(strip)).child(content)),
    );
    if s.prefs.recording_talk {
        root = root.child(talk_capture(s, ink));
    }
    root
}

// ---- general: audio, and how you talk to Orbit -------------------------------------------------------------------------------------

fn section_title(title: &str, ink: &Ink) -> El {
    text(title.to_owned()).font(t(15.0, 600.0)).color(ink.fg)
}

fn general(s: &App, ink: &Ink) -> El {
    let prefs = &s.prefs;
    let volume = prefs.speaking();

    let mic = device_row(s, true, ink);
    let speaker = device_row(s, false, ink);
    let loudness = row()
        .key("speaking-controls")
        .gap(10.0)
        .items_center()
        .child(icon(&icons::VOLUME, 15.0).color(ink.muted))
        .child(
            slider("speaking-slider", "Speaking volume", volume, true, 210.0, ink).on_drag(|s: &mut App, g: &mut Drag, tx: &mut Tx| {
                tx.instant();
                match g.phase {
                    Phase::Down | Phase::Move => s.prefs.drag_speaking(slider_percent(g.x, g.w)),
                    Phase::Up => {
                        s.prefs.finish_speaking(!g.cancelled());
                    }
                }
            }),
        )
        .child(text(format!("{volume}%")).key("speaking-readout").font(t(12.0, 500.0).mono()).color(ink.fg).w(40.0));
    let audio_card = card("audio-card", ink)
        .child(setting_row("mic-row", "Microphone", "What Orbit listens with. It switches between turns.", mic, ink))
        .child(divider(ink))
        .child(setting_row("speaker-row", "Output device", "Where Orbit's voice comes out.", speaker, ink))
        .child(divider(ink))
        .child(setting_row("speaking-row", "Speaking volume", "How loud Orbit's own voice is. Your PC's volume is separate.", loudness, ink));

    // how you talk to Orbit
    let talk = prefs.hotkeys().talk.clone();
    let label = settings::talk_label(&talk);
    let key_box = row()
        .key("talk-box")
        .w(210.0)
        .h(36.0)
        .px(12.0)
        .gap(8.0)
        .items_center()
        .radius(8.0)
        .fill(ink.field)
        .border(1.0, Color::WHITE.alpha(0.12))
        .child(icon(if settings::is_mouse_talk(&talk) { &icons::MOUSE } else { &icons::KEYBOARD }, 15.0).color(ink.muted))
        .child(text(label.clone()).font(t(13.0, 500.0).mono()).color(ink.fg).truncate().grow(1.0).min_w(0.0));
    let controls = row()
        .key("talk-controls")
        .gap(8.0)
        .items_center()
        .child(key_box)
        .child(button("talk-change", "Change", ink, Look::Plain).on_press(|s: &mut App| s.prefs.begin_record_talk()))
        .child(button("talk-pick", "More\u{2026}", ink, Look::Ghost).on_press_tx(|s: &mut App, tx: &mut Tx| {
            let current = s.prefs.hotkeys().talk.clone();
            let menu = PopupMenu::picker(settings::TALK_KEYS.iter().map(|(name, label)| (*name, *label)), &current);
            tx.popup_menu(menu, "talk-pick", |s: &mut App, picked, _| {
                if let Some(name) = picked {
                    s.prefs.set_talk(name);
                }
            });
        }));
    let hint = if settings::is_mouse_talk(&talk) && matches!(talk.as_str(), "mouse1" | "mouse2") {
        "Orbit only starts listening once you have held the button for a moment, so an ordinary click does nothing. It still clicks as usual."
    } else {
        "Hold it while you speak, then let go."
    };
    let key_row = col()
        .key("talk-key-row")
        .w_full()
        .px(18.0)
        .py(14.0)
        .gap(10.0)
        .child(
            row()
                .key("talk-key-head")
                .w_full()
                .gap(16.0)
                .items_center()
                .justify_between()
                .child(
                    col()
                        .key("talk-key-text")
                        .grow(1.0)
                        .min_w(0.0)
                        .gap(3.0)
                        .child(text("Hotkey").font(t(13.5, 600.0)).color(ink.fg))
                        .child(text("Hold it to talk, as well as saying \u{201c}Hey Orbit\u{201d}.").font(t(12.0, 400.0)).color(ink.muted).wrap()),
                )
                .child(controls),
        )
        .child(text(format!("Active \u{2014} hold {label} to talk")).key("talk-active").font(t(12.0, 500.0)).color(ink.accent_text))
        .child(text(hint).key("talk-hint").font(t(11.5, 400.0)).color(ink.muted).wrap().max_w(620.0));
    let on = prefs.settings.voice.wake_word;
    let keep = prefs.settings.voice.keep_listening;
    let talk_card = card("talk-card", ink)
        .child(key_row)
        .child(divider(ink))
        .child(setting_row(
            "wake-row",
            "Wake word",
            "Say \u{201c}Hey Orbit\u{201d} to start, with no key to hold. Off: only the hotkey starts a turn.",
            switch("wake-switch", on, "Wake word", ink).on_press(move |s: &mut App| {
                s.prefs.set_wake_word(!on);
            }),
            ink,
        ))
        .child(divider(ink))
        .child(setting_row(
            "keep-row",
            "Keep listening after a reply",
            "Orbit stays ready for a moment after it answers, so you can carry on without saying the wake word.",
            switch("keep-switch", keep, "Keep listening after a reply", ink).on_press(move |s: &mut App| {
                s.prefs.set_keep_listening(!keep);
            }),
            ink,
        ));
    col()
        .key("general")
        .w_full()
        .gap(14.0)
        .child(section_title("Audio", ink))
        .child(audio_card)
        .children(scan_problem(s).map(|why| text(format!("Orbit could not list your audio devices: {why}")).key("scan-problem").font(t(12.0, 400.0)).color(ink.bad).wrap().max_w(620.0)))
        .child(el().key("general-gap").h(6.0))
        .child(section_title("Talk to Orbit", ink))
        .child(talk_card)
        .child(row().key("general-actions").gap(10.0).items_center().child(button("reset-general", "Reset to the defaults", ink, Look::Plain).on_press(|s: &mut App| {
            s.prefs.reset_general();
        })))
        .child(prefs_notice(s, ink))
}

fn prefs_notice(s: &App, ink: &Ink) -> El {
    notice(s.prefs.notice.as_ref().map(|n| (n.text.as_str(), n.good)), ink)
}

/// A microphone or speaker: a box that opens the list, and a button that looks again.
fn device_row(s: &App, mic: bool, ink: &Ink) -> El {
    let (key, chosen) = if mic { ("mic-select", s.prefs.settings.audio.mic.clone()) } else { ("speaker-select", s.prefs.settings.audio.speaker.clone()) };
    let names: Vec<String> = if mic { s.devices.inputs.clone() } else { s.devices.outputs.clone() };
    let scanned = !(s.devices.inputs.is_empty() && s.devices.outputs.is_empty());
    let shown = if chosen.is_empty() {
        "System default".to_owned()
    } else if scanned && !names.contains(&chosen) {
        format!("{chosen} (not connected)")
    } else {
        chosen.clone()
    };
    let mut options: Vec<(String, String)> = vec![(DEFAULT_ID.to_owned(), "System default".to_owned())];
    if !chosen.is_empty() && !names.contains(&chosen) {
        options.push((chosen.clone(), format!("{chosen} (not connected)")));
    }
    options.extend(names.iter().map(|n| (n.clone(), n.clone())));
    let selected = if chosen.is_empty() { DEFAULT_ID.to_owned() } else { chosen.clone() };
    let busy = s.scanning();
    row()
        .key(format!("{key}-row"))
        .gap(8.0)
        .items_center()
        .child(select_box(key, &shown, chosen.is_empty(), 300.0, ink).on_press_tx(move |_: &mut App, tx: &mut Tx| {
            let menu = PopupMenu::picker(options.iter().map(|(id, title)| (id.as_str(), title.as_str())), &selected);
            tx.popup_menu(menu, key, move |s: &mut App, picked, _| {
                if let Some(id) = picked {
                    let name = if id == DEFAULT_ID { "" } else { id };
                    if mic {
                        s.prefs.set_mic(name);
                    } else {
                        s.prefs.set_speaker(name);
                    }
                }
            });
        }))
        .child(
            icon_button(format!("{key}-refresh"), &icons::REFRESH, if busy { "Looking for devices\u{2026}" } else { "Look for devices again" }, if busy { ink.accent_text } else { ink.muted })
                .on_press(|s: &mut App| s.scan_devices()),
        )
}

/// The id of "System default" in a device menu (a device is never called this).
const DEFAULT_ID: &str = "\u{1}default";

/// While the hold-to-talk key is being recorded: the whole page is for it. A key, or a click of any mouse button anywhere on the page
/// (outside Cancel), is taken as the talk key.
fn talk_capture(s: &App, ink: &Ink) -> El {
    let failed = s.prefs.notice.as_ref().filter(|n| !n.good).map(|n| n.text.clone());
    let mut card = col()
        .key("capture-card")
        .items_center()
        .gap(12.0)
        .padding(28.0)
        .radius(16.0)
        .fill(ink.raised)
        .border(1.0, ink.hair_strong)
        .max_w(460.0)
        .child(icon(&icons::MOUSE, 28.0).color(ink.accent_text))
        .child(text("Press a key, or click a mouse button").font(t(17.0, 600.0)).color(ink.fg))
        .child(
            text("Mouse 1 is the left button, 2 the right, 3 the wheel, and 4 and 5 the buttons on the side of your mouse. Esc cancels.")
                .font(t(12.5, 400.0))
                .color(ink.muted)
                .align(rune::Align::Center)
                .wrap()
                .max_w(400.0),
        );
    if let Some(why) = failed {
        card = card.child(text(why).key("capture-why").font(t(12.5, 500.0)).color(ink.bad).align(rune::Align::Center).wrap().max_w(400.0));
    }
    card = card.child(button("capture-cancel", "Cancel", ink, Look::Plain).on_press(|s: &mut App| s.prefs.cancel_record()));
    el()
        .key("capture")
        .absolute(0.0, 0.0)
        .w_full()
        .h_full()
        .center()
        .fill(ink.panel.alpha(0.93))
        .radius(14.0)
        .pointer_buttons([PointerButton::Middle, PointerButton::Back, PointerButton::Forward])
        .on_press_tx(|s: &mut App, tx: &mut Tx| {
            s.record_button(tx.button());
        })
        .on_secondary_press(|s: &mut App, _, _: &mut Tx| {
            s.record_button(PointerButton::Secondary);
        })
        .child(card)
}

// ---- shortcuts ----------------------------------------------------------------------------------------------------------------

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
    let mut body = col().key("hotkeys").w_full().gap(14.0).child(section_title("Shortcuts", ink)).child(
        text("Keys that work from any app. Pick a new one and Orbit uses it straight away. (The key you hold to talk is on the General tab.)").font(t(12.5, 400.0)).color(ink.muted).wrap().max_w(600.0),
    );
    body = body.child(card("hotkey-card", ink).children(rows));
    body = body.child(row().key("hotkey-actions").gap(10.0).items_center().child(button("reset-hotkeys", "Reset all to the defaults", ink, Look::Plain).on_press(|s: &mut App| s.reset_hotkeys())));
    if let Some(why) = &prefs.orb_error {
        body = body.child(text(why.clone()).font(t(12.0, 400.0)).color(ink.bad).wrap().max_w(620.0).key("orb-error"));
    }
    body.child(prefs_notice(s, ink))
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
        .fill(if recording { ink.accent.alpha(0.2) } else { Color::WHITE.alpha(0.06) })
        .border(1.0, if recording { ink.accent } else { Color::WHITE.alpha(0.12) })
        .hover(Color::WHITE.alpha(0.05))
        .on_press(move |s: &mut App| s.prefs.begin_record(f))
        .child(text(if recording { "Press the new keys\u{2026}".to_owned() } else { settings::pretty(&chord) }).font(t(12.0, 500.0).mono()).color(ink.fg).a11y_hidden());
    let mut right = row().key(format!("{key}-controls")).gap(8.0).items_center().child(pill);
    if recording {
        right = right.child(button(&format!("{key}-cancel"), "Cancel", ink, Look::Plain).on_press(|s: &mut App| s.prefs.cancel_record()));
    } else if chord != f.default_chord() {
        right = right.child(button(&format!("{key}-reset"), "Reset", ink, Look::Ghost).on_press(move |s: &mut App| {
            s.change_hotkey(f, f.default_chord());
        }));
    }
    let hint = if recording { "Hold Ctrl, Alt or Win and press a key. Esc cancels." } else { detail };
    setting_row(&format!("{key}-row"), describe_field(f), hint, right, ink)
}

// ---- sounds ---------------------------------------------------------------------------------------------------------------------

const SLIDER_W: f32 = 220.0;

fn sounds(s: &App, ink: &Ink) -> El {
    let sounds = s.prefs.sounds();
    let on = sounds.enabled;
    let switch_row = setting_row(
        "sounds-switch-row",
        "Orb sounds",
        "A soft tone as the orb fades in and another as it fades away. Never while it is only updating what it says.",
        switch("sounds-on", on, "Orb sounds", ink).on_press(move |s: &mut App| s.set_sounds_enabled(!on)),
        ink,
    );
    let hint = if on { "Drag the slider, or use the buttons. You hear the pop-up tone at the new volume." } else { "Switch the sounds on to hear a change." };
    let controls = row()
        .key("volume-controls")
        .gap(8.0)
        .items_center()
        .child(icon_button("volume-down", &icons::MINUS, "Quieter", ink.muted).on_press(|s: &mut App| s.step_volume(-1)))
        .child(
            slider("volume-slider", "Volume", sounds.volume, on, SLIDER_W, ink).on_drag(|s: &mut App, g: &mut Drag, tx: &mut Tx| {
                tx.instant();
                match g.phase {
                    Phase::Down | Phase::Move => s.prefs.drag_volume(slider_percent(g.x, g.w)),
                    Phase::Up => s.finish_volume(!g.cancelled()),
                }
            }),
        )
        .child(icon_button("volume-up", &icons::PLUS, "Louder", ink.muted).on_press(|s: &mut App| s.step_volume(1)))
        .child(text(format!("{}%", sounds.volume)).key("volume-readout").font(t(12.0, 500.0).mono()).color(if on { ink.fg } else { ink.muted }).w(40.0));
    col()
        .key("sounds")
        .w_full()
        .gap(14.0)
        .child(section_title("Sounds", ink))
        .child(text("Orbit makes a quiet sound when the orb appears and when it goes away.").font(t(12.5, 400.0)).color(ink.muted).wrap().max_w(600.0))
        .child(card("sounds-card", ink).child(switch_row).child(divider(ink)).child(setting_row("volume-row", "Volume", hint, controls, ink)))
        .child(row().key("sounds-actions").gap(10.0).items_center().child(button("reset-sounds", "Reset to the defaults", ink, Look::Plain).on_press(|s: &mut App| s.reset_sounds())))
        .child(prefs_notice(s, ink))
}

// ---- the orb --------------------------------------------------------------------------------------------------------------------

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
                        .child(text("Position").font(t(13.5, 600.0)).color(ink.fg))
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
    let size = setting_row("size-row", "Size", "How big the orb is when it floats over your work.", chips, ink);
    col()
        .key("customization")
        .w_full()
        .gap(14.0)
        .child(section_title("Orb", ink))
        .child(text("Choose where the orb floats on your screen and how big it is. It stays where you put it.").font(t(12.5, 400.0)).color(ink.muted).wrap().max_w(600.0))
        .child(card("orb-card", ink).child(position).child(divider(ink)).child(size))
        .child(row().key("orb-actions").gap(10.0).items_center().child(button("reset-orb", "Reset to the defaults", ink, Look::Plain).on_press(|s: &mut App| {
            s.prefs.reset_orb();
        })))
        .child(prefs_notice(s, ink))
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
                .fill(if on { Color::WHITE } else { ink.fg.alpha(0.8) });
            line = line.child(
                el()
                    .key(format!("spot-{r}{c}"))
                    .role("button")
                    .label(*name)
                    .focusable(true)
                    .size(TILE_W, TILE_H)
                    .radius(7.0)
                    .fill(if on { ink.accent } else { Color::WHITE.alpha(0.06) })
                    .border(1.0, if on { ink.accent } else { Color::WHITE.alpha(0.12) })
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
        .fill(ink.field)
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
        .child(el().key("preview-orb").absolute(ix + x * travel_x, iy + y * travel_y).size(dot, dot).radius(dot / 2.0).fill(ink.accent_text.alpha(0.92)))
}

/// What the page says about a scan that went wrong, if one did.
pub(crate) fn scan_problem(s: &App) -> Option<&str> {
    match &s.scan {
        Scan::Failed(why) => Some(why.as_str()),
        _ => None,
    }
}

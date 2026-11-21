//! The Music page, drawn: paste a Spotify link and Orbit keeps it by name, so you can say "play my gym playlist" (or press Play). With
//! nothing saved yet it is an invitation to; after that, the playlists as a list. All the rules live in `music`; this only shows its
//! state and turns clicks into calls on it.

use rune::{Align, Color, El, ScrollSpec, col, edit_access, el, icon, row, text, text_input};

use crate::app::App;
use crate::icons;
use crate::music::Playlist;
use crate::view::Ink;
use crate::widgets::{Look, button, card, divider, icon_button, icon_label_button, notice, page_header, t, tile_colors};

use crate::datetime::Stamp;

pub(crate) fn page(s: &App, ink: &Ink) -> El {
    let body = if s.music.data.playlists.is_empty() { invitation(s, ink) } else { library(s, ink) };
    col()
        .key("music-page")
        .w_full()
        .h_full()
        .child(page_header("Music", Some("BETA"), "Playlists for Orbit's music player, played on Spotify.", None, ink))
        .child(col().key("music-scroll").grow(1.0).min_h(0.0).w_full().scroll(ScrollSpec { stick_bottom: false }).child(body))
}

/// The box that takes a link, and the button that saves it.
fn import_row(s: &App, ink: &Ink) -> El {
    let input = text_input(&s.music.link, t(13.0, 400.0))
        .key("link-input")
        .single_line()
        .grow(1.0)
        .min_w(0.0)
        .color(ink.fg)
        .placeholder("Paste a Spotify link", ink.muted)
        .on_edit(edit_access(|s: &mut App| s.music.link_edit()))
        .on_submit(|s: &mut App, _| {
            s.music.import(&Stamp::now());
        });
    row()
        .key("import-row")
        .w_full()
        .max_w(520.0)
        .h(46.0)
        .pl(16.0)
        .pr(6.0)
        .gap(10.0)
        .items_center()
        .radius(23.0)
        .fill(ink.field)
        .border(1.0, Color::WHITE.alpha(0.12))
        .child(icon(&icons::LINK, 15.0).color(ink.muted))
        .child(input)
        .child(button("import", "Import", ink, Look::Primary).radius(17.0).h(34.0).px(18.0).on_press(|s: &mut App| {
            s.music.import(&Stamp::now());
        }))
}

fn music_notice(s: &App, ink: &Ink) -> El {
    notice(s.music.notice.as_ref().map(|n| (n.text.as_str(), n.good)), ink)
}

// ---- nothing saved yet ----------------------------------------------------------------------------------------------------------

/// Three tilted covers.
fn covers() -> El {
    let cover = |key: &str, size: f32, x: f32, y: f32, turn: f32, top: &str, bottom: &str| {
        el().key(key.to_owned()).absolute(x, y).size(size, size).radius(20.0).rotate(turn).gradient(crate::view::hex(top), crate::view::hex(bottom))
    };
    el()
        .key("covers")
        .w(360.0)
        .h(210.0)
        .child(cover("cover-left", 150.0, 18.0, 36.0, -0.2, "#5B606B", "#2C3037"))
        .child(cover("cover-right", 150.0, 192.0, 36.0, 0.17, "#9A4338", "#5A211C"))
        .child(cover("cover-front", 158.0, 101.0, 14.0, 0.0, "#8A5099", "#4B2A60"))
}

fn invitation(s: &App, ink: &Ink) -> El {
    col()
        .key("invitation")
        .w_full()
        .items_center()
        .px(28.0)
        .py(34.0)
        .gap(14.0)
        .child(covers())
        .child(text("Bring your playlists to Orbit").font(t(24.0, 650.0).tracking(-0.2)).color(ink.fg))
        .child(
            text("Paste a link from Spotify. Orbit saves it by name, then plays it on Spotify whenever you ask.")
                .font(t(13.0, 400.0))
                .color(ink.muted)
                .align(Align::Center)
                .wrap()
                .max_w(460.0),
        )
        .child(el().key("invite-gap").h(4.0))
        .child(import_row(s, ink))
        .child(music_notice(s, ink))
        .child(
            row()
                .key("say-it")
                .gap(8.0)
                .items_center()
                .child(icon(&icons::MIC, 14.0).color(ink.muted))
                .child(text("After that, just say \u{201c}Hey Orbit, play my gym playlist.\u{201d}").font(t(12.0, 400.0)).color(ink.muted)),
        )
        .child(text("In Spotify, open a playlist, choose Share, then Copy link.").font(t(11.5, 400.0)).color(ink.muted.alpha(0.8)))
}

// ---- the saved playlists -----------------------------------------------------------------------------------------------------------

fn library(s: &App, ink: &Ink) -> El {
    let mut rows: Vec<El> = Vec::new();
    for (i, p) in s.music.data.playlists.iter().enumerate() {
        if i > 0 {
            rows.push(divider(ink));
        }
        rows.push(playlist_row(s, p, ink));
    }
    col()
        .key("library")
        .w_full()
        .max_w(1000.0)
        .px(28.0)
        .py(22.0)
        .gap(14.0)
        .child(
            col()
                .key("library-head")
                .gap(4.0)
                .child(text("Your playlists").font(t(15.0, 600.0)).color(ink.fg))
                .child(text("Say \u{201c}Hey Orbit, play my \u{2026} playlist\u{201d}, or press Play. Orbit plays them on Spotify.").font(t(12.5, 400.0)).color(ink.muted).wrap()),
        )
        .child(import_row(s, ink))
        .child(music_notice(s, ink))
        .child(card("playlist-card", ink).children(rows))
}

fn playlist_row(s: &App, p: &Playlist, ink: &Ink) -> El {
    let id = p.id.clone();
    let (top, bottom) = tile_colors(&p.id);
    let named = !p.name.is_empty();
    let renaming = s.music.renaming.as_deref() == Some(p.id.as_str());

    let tile = el()
        .key(format!("pl-{id}-tile"))
        .size(46.0, 46.0)
        .radius(9.0)
        .center()
        .gradient(top, bottom)
        .child(icon(&icons::MUSIC, 18.0).color(Color::WHITE.alpha(0.85)));

    let text_block = if renaming {
        let (save, cancel) = (id.clone(), id.clone());
        let _ = (&save, &cancel);
        row()
            .key(format!("pl-{id}-rename"))
            .grow(1.0)
            .min_w(0.0)
            .gap(8.0)
            .items_center()
            .child(
                row().key(format!("pl-{id}-rename-box")).grow(1.0).min_w(0.0).h(34.0).px(11.0).items_center().radius(8.0).fill(ink.field).border(1.0, ink.accent).child(
                    text_input(&s.music.rename, t(13.0, 400.0))
                        .key(format!("pl-{id}-rename-input"))
                        .single_line()
                        .grow(1.0)
                        .min_w(0.0)
                        .color(ink.fg)
                        .placeholder("Name it", ink.muted)
                        .on_edit(edit_access(|s: &mut App| s.music.rename_edit()))
                        .on_submit(|s: &mut App, _| {
                            s.music.finish_rename();
                        }),
                ),
            )
            .child(button(&format!("pl-{id}-rename-save"), "Save", ink, Look::Primary).on_press(|s: &mut App| {
                s.music.finish_rename();
            }))
            .child(button(&format!("pl-{id}-rename-cancel"), "Cancel", ink, Look::Plain).on_press(|s: &mut App| s.music.cancel_rename()))
    } else {
        col()
            .key(format!("pl-{id}-text"))
            .grow(1.0)
            .min_w(0.0)
            .gap(3.0)
            .child(text(p.title()).font(t(14.0, 600.0)).color(if named { ink.fg } else { ink.muted }).truncate())
            .child(text(if named { p.kind.label().to_owned() } else { format!("{} \u{b7} looking up its name\u{2026}", p.kind.label()) }).font(t(12.0, 400.0)).color(ink.muted).truncate())
    };

    let controls = if s.music.confirm_delete.as_deref() == Some(p.id.as_str()) {
        let yes = id.clone();
        row()
            .key(format!("pl-{id}-confirm"))
            .gap(6.0)
            .items_center()
            .child(text("Remove it?").font(t(12.0, 400.0)).color(ink.muted))
            .child(button(&format!("pl-{id}-delete-yes"), "Remove", ink, Look::Primary).on_press(move |s: &mut App| {
                s.music.delete(&yes);
            }))
            .child(button(&format!("pl-{id}-delete-no"), "Keep", ink, Look::Plain).on_press(|s: &mut App| s.music.confirm_delete = None))
    } else if renaming {
        row().key(format!("pl-{id}-nothing"))
    } else {
        let (play, open, rename, delete) = (id.clone(), id.clone(), id.clone(), id.clone());
        row()
            .key(format!("pl-{id}-controls"))
            .gap(4.0)
            .items_center()
            .child(icon_label_button(&format!("pl-{id}-play"), &icons::PLAY, "Play", ink, Look::Primary).on_press(move |s: &mut App| {
                s.play_playlist(&play);
            }))
            .child(icon_button(format!("pl-{id}-open"), &icons::EXTERNAL, "Open in Spotify", ink.muted).on_press(move |s: &mut App| {
                s.open_in_spotify(&open);
            }))
            .child(icon_button(format!("pl-{id}-rename-btn"), &icons::PENCIL, "Rename", ink.muted).on_press(move |s: &mut App| s.music.begin_rename(&rename)))
            .child(icon_button(format!("pl-{id}-delete"), &icons::TRASH, "Remove", ink.bad).on_press(move |s: &mut App| s.music.ask_delete(&delete)))
    };

    row().key(format!("pl-{id}")).w_full().px(16.0).py(12.0).gap(14.0).items_center().justify_between().child(tile).child(text_block).child(controls)
}

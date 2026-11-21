//! The Reminders page, drawn: the list of what Orbit will tell you and when, the form that makes or changes one, and what went off lately.
//! All the rules live in `reminders`; this only shows its state and turns clicks into calls on it.

use rune::{Color, El, ScrollSpec, col, edit_access, el, icon, row, text};

use crate::app::App;
use crate::datetime::Stamp;
use crate::icons;
use crate::reminders::{Draft, Quick, Reminder, Repeat, clock_text, day_text, when_text};
use crate::view::Ink;
use crate::widgets::{Look, button, card, chip, divider, field, icon_button, icon_label_button, notice, page_header, switch, t};

pub(crate) fn page(s: &App, ink: &Ink) -> El {
    let now = Stamp::now();
    let data = &s.reminders;
    let mut body = col().key("reminders").w_full().gap(16.0);

    let add = icon_label_button("add-reminder", &icons::PLUS, "Add a reminder", ink, Look::Plain).on_press(|s: &mut App| s.reminders.new_draft(&Stamp::now()));
    let head = row()
        .key("scheduled-head")
        .w_full()
        .gap(16.0)
        .items_center()
        .justify_between()
        .child(
            col()
                .key("scheduled-title")
                .gap(4.0)
                .grow(1.0)
                .min_w(0.0)
                .child(text("Scheduled reminders").font(t(15.0, 600.0)).color(ink.fg))
                .child(text("Orbit tells you when one of these goes off, out loud and here.").font(t(12.5, 400.0)).color(ink.muted).wrap()),
        )
        .child(add);
    body = body.child(head);

    if let Some(draft) = &data.draft {
        body = body.child(form(draft, &now, ink));
    }

    let ordered = data.data.ordered();
    if ordered.is_empty() {
        if data.draft.is_none() {
            body = body.child(text("No active reminders yet. Create one to schedule a reminder.").key("none-yet").font(t(13.0, 400.0)).color(ink.muted));
        }
    } else {
        let mut rows: Vec<El> = Vec::new();
        for (i, r) in ordered.iter().enumerate() {
            if i > 0 {
                rows.push(divider(ink));
            }
            rows.push(reminder_row(s, r, &now, ink));
        }
        body = body.child(card("reminder-card", ink).children(rows));
    }
    body = body.child(notice(data.notice.as_ref().map(|n| (n.text.as_str(), n.good)), ink));

    if !data.data.history.is_empty() {
        let mut lately = col().key("lately").w_full().gap(10.0).child(text("Went off lately").font(t(15.0, 600.0)).color(ink.fg));
        let mut rows: Vec<El> = Vec::new();
        for (i, h) in data.data.history.iter().take(6).enumerate() {
            if i > 0 {
                rows.push(divider(ink));
            }
            rows.push(
                row()
                    .key(format!("fired-{i}"))
                    .w_full()
                    .px(18.0)
                    .py(11.0)
                    .gap(12.0)
                    .items_center()
                    .child(icon(&icons::CLOCK, 15.0).color(ink.muted))
                    .child(text(h.title.clone()).font(t(13.0, 500.0)).color(ink.fg).truncate().grow(1.0).min_w(0.0))
                    .child(text(format!("{} \u{b7} {}", day_text(&h.at, &now), clock_text(&h.at))).font(t(12.0, 400.0)).color(ink.muted)),
            );
        }
        lately = lately.child(card("fired-card", ink).children(rows));
        body = body.child(el().key("lately-gap").h(8.0)).child(lately);
    }

    col().key("reminders-page").w_full().h_full().child(page_header("Reminders", None, "Say \u{201c}remind me to call mom at 5\u{201d}, or make one here.", None, ink)).child(
        col()
            .key("reminders-scroll")
            .grow(1.0)
            .min_h(0.0)
            .w_full()
            .scroll(ScrollSpec { stick_bottom: false })
            .child(col().key("reminders-content").w_full().max_w(1000.0).px(28.0).py(22.0).child(body)),
    )
}

fn reminder_row(s: &App, r: &Reminder, now: &Stamp, ink: &Ink) -> El {
    let id = r.id.clone();
    let overdue = r.enabled && r.due <= *now;
    let when = when_text(r, now);
    let tone = if r.enabled { ink.fg } else { ink.muted };
    let detail = if overdue { format!("Overdue \u{b7} {when}") } else if r.enabled { when } else { format!("Off \u{b7} {when}") };
    let controls = if s.reminders.confirm_delete.as_deref() == Some(r.id.as_str()) {
        let yes = id.clone();
        row()
            .key(format!("rem-{id}-confirm"))
            .gap(6.0)
            .items_center()
            .child(text("Delete it?").font(t(12.0, 400.0)).color(ink.muted))
            .child(button(&format!("rem-{id}-delete-yes"), "Delete", ink, Look::Primary).on_press(move |s: &mut App| {
                s.reminders.delete(&yes);
            }))
            .child(button(&format!("rem-{id}-delete-no"), "Keep", ink, Look::Plain).on_press(|s: &mut App| s.reminders.confirm_delete = None))
    } else {
        let (toggle, edit, delete) = (id.clone(), id.clone(), id.clone());
        row()
            .key(format!("rem-{id}-controls"))
            .gap(6.0)
            .items_center()
            .child(switch(format!("rem-{id}-on"), r.enabled, &format!("{} is on", r.title), ink).on_press(move |s: &mut App| {
                s.reminders.toggle(&toggle, &Stamp::now());
            }))
            .child(icon_button(format!("rem-{id}-edit"), &icons::PENCIL, "Edit", ink.muted).on_press(move |s: &mut App| s.reminders.edit(&edit, &Stamp::now())))
            .child(icon_button(format!("rem-{id}-delete"), &icons::TRASH, "Delete", ink.bad).on_press(move |s: &mut App| s.reminders.ask_delete(&delete)))
    };
    row()
        .key(format!("rem-{id}"))
        .w_full()
        .px(18.0)
        .py(13.0)
        .gap(16.0)
        .items_center()
        .justify_between()
        .child(
            col()
                .key(format!("rem-{id}-text"))
                .grow(1.0)
                .min_w(0.0)
                .gap(3.0)
                .child(text(r.title.clone()).font(t(14.0, 600.0)).color(tone).truncate())
                .child(text(detail).font(t(12.0, 400.0)).color(if overdue { crate::view::hex("#E0A94B") } else { ink.muted }).truncate()),
        )
        .child(controls)
}

fn label_text(label: &str, ink: &Ink) -> El {
    text(label.to_owned()).font(t(11.0, 500.0).tracking(1.1)).color(ink.muted)
}

/// Making or changing a reminder: what, when, and how often.
fn form(d: &Draft, now: &Stamp, ink: &Ink) -> El {
    let title = if d.id.is_some() { "Change the reminder" } else { "New reminder" };
    let mut quick = row().key("quick").gap(8.0).flex_wrap().gaps(8.0, 8.0);
    for (i, q) in Quick::ALL.into_iter().enumerate() {
        let now = *now;
        quick = quick.child(chip(format!("quick-{i}"), &q.label(), false, ink).on_press(move |s: &mut App| s.reminders.quick(q, &now)));
    }
    let mut repeats = row().key("repeats").gap(8.0).flex_wrap().gaps(8.0, 8.0);
    for r in Repeat::ALL {
        repeats = repeats.child(chip(format!("repeat-{}", r.name()), r.label(), d.repeat == r, ink).on_press(move |s: &mut App| s.reminders.set_repeat(r)));
    }
    let mut form = col()
        .key("reminder-form")
        .w_full()
        .padding(18.0)
        .gap(14.0)
        .radius(12.0)
        .fill(ink.raised)
        .border(1.0, ink.hair_strong)
        .child(text(title).font(t(14.0, 600.0)).color(ink.fg))
        .child(
            col()
                .key("what-block")
                .w_full()
                .gap(7.0)
                .child(label_text("WHAT SHOULD ORBIT REMIND YOU ABOUT?", ink))
                .child(field("rem-title".to_owned(), &d.title, "Call mom", edit_access(|s: &mut App| s.reminders.title_edit()), ink)),
        )
        .child(
            row()
                .key("when-block")
                .w_full()
                .gap(12.0)
                .child(col().key("day-block").grow(1.0).min_w(0.0).gap(7.0).child(label_text("DAY", ink)).child(field("rem-day".to_owned(), &d.day, "Today, Tomorrow, Friday, 25 Dec", edit_access(|s: &mut App| s.reminders.day_edit()), ink)))
                .child(col().key("time-block").w(170.0).gap(7.0).child(label_text("TIME", ink)).child(field("rem-time".to_owned(), &d.time, "5:00 PM", edit_access(|s: &mut App| s.reminders.time_edit()), ink))),
        )
        .child(quick)
        .child(col().key("repeat-block").gap(7.0).child(label_text("REPEAT", ink)).child(repeats));
    if !d.errors.is_empty() {
        form = form.child(
            col()
                .key("form-errors")
                .w_full()
                .gap(4.0)
                .children(d.errors.iter().enumerate().map(|(i, e)| text(e.clone()).key(format!("form-error-{i}")).font(t(12.0, 400.0)).color(ink.bad).wrap())),
        );
    }
    form.child(
        row()
            .key("form-actions")
            .gap(10.0)
            .child(button("save-reminder", "Save", ink, Look::Primary).on_press(|s: &mut App| {
                s.reminders.save_draft(&Stamp::now());
            }))
            .child(button("cancel-reminder", "Cancel", ink, Look::Plain).on_press(|s: &mut App| s.reminders.cancel_draft())),
    )
}

#[allow(dead_code)]
const _UNUSED: Option<Color> = None;

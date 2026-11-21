//! The Notes page, drawn: a column with the search box, the Notes and Tasks tabs and the list, and beside it the note being written (or the
//! tasks, summed up). All the rules live in `notes`; this only shows its state and turns clicks into calls on it.

use rune::{Color, El, ScrollSpec, auto_grow, col, edit_access, el, icon, row, text, text_input};

use crate::app::App;
use crate::datetime::Stamp;
use crate::icons;
use crate::notes::{Note, Tab, Task, list_title};
use crate::reminders::{clock_text, day_text};
use crate::view::Ink;
use crate::widgets::{Look, badge, button, caps, chip, icon_button, icon_label_button, notice, t, tab, tab_strip};

pub const LIST_W: f32 = 312.0;

pub(crate) fn page(s: &App, ink: &Ink) -> El {
    row().key("notes-page").w_full().h_full().child(left(s, ink)).child(el().key("notes-rule").w(1.0).h_full().fill(ink.hair)).child(match s.notes.tab {
        Tab::Notes => editor(s, ink),
        Tab::Tasks => tasks_summary(s, ink),
    })
}

// ---- the column on the left ---------------------------------------------------------------------------------------------------------

fn search_box(s: &App, ink: &Ink) -> El {
    let placeholder = if s.notes.tab == Tab::Notes { "Search notes" } else { "Search tasks" };
    let input = text_input(&s.notes.search, t(13.0, 400.0))
        .key("note-search-input")
        .label(placeholder)
        .single_line()
        .grow(1.0)
        .min_w(0.0)
        .color(ink.fg)
        .placeholder(placeholder, ink.muted)
        .on_edit(edit_access(|s: &mut App| s.notes.search_edit()));
    row()
        .key("note-search")
        .w_full()
        .h(36.0)
        .px(12.0)
        .gap(9.0)
        .items_center()
        .radius(9.0)
        .fill(ink.field)
        .border(1.0, Color::WHITE.alpha(0.10))
        .child(icon(&icons::SEARCH, 14.0).color(ink.muted))
        .child(input)
}

fn left(s: &App, ink: &Ink) -> El {
    let tabs = vec![
        tab("tab-notes", "Notes", s.notes.tab == Tab::Notes, Some(40.0), ink).on_press(|s: &mut App| s.notes.select_tab(Tab::Notes)),
        tab("tab-tasks", "Tasks", s.notes.tab == Tab::Tasks, Some(40.0), ink).on_press(|s: &mut App| s.notes.select_tab(Tab::Tasks)),
    ];
    let mut col_left = col()
        .key("notes-left")
        .w(LIST_W)
        .h_full()
        .shrink()
        .padding(14.0)
        .gap(12.0)
        .child(search_box(s, ink))
        .child(tab_strip("notes-tabs", tabs, ink).w_full());
    col_left = match s.notes.tab {
        Tab::Notes => col_left
            .child(
                row()
                    .key("new-note-row")
                    .w_full()
                    .gap(8.0)
                    .items_center()
                    .child(icon_label_button("new-note", &icons::PLUS, "New note", ink, Look::Primary).grow(1.0).h(36.0).on_press(|s: &mut App| s.notes.new_note(&Stamp::now())))
                    .child(row().key("ctrl-k").px(9.0).h(30.0).center().radius(7.0).border(1.0, ink.hair_strong).child(text("Ctrl K").font(t(11.0, 500.0).mono()).color(ink.muted))),
            )
            .child(note_list(s, ink)),
        Tab::Tasks => col_left.child(list_chips(s, ink)).child(add_task_row(s, ink)).child(task_list(s, ink)),
    };
    col_left
}

fn note_list(s: &App, ink: &Ink) -> El {
    let now = Stamp::now();
    let shown = s.notes.shown();
    if shown.is_empty() {
        let words = if s.notes.search.text.trim().is_empty() { "No notes yet." } else { "No notes match that." };
        return text(words).key("no-notes").font(t(12.5, 400.0)).color(ink.muted).px(4.0);
    }
    let mut list = col().key("note-list").w_full().gap(3.0);
    for n in shown {
        list = list.child(note_item(s, n, &now, ink));
    }
    col().key("note-scroll").w_full().grow(1.0).min_h(0.0).scroll(ScrollSpec { stick_bottom: false }).child(list)
}

fn note_item(s: &App, n: &Note, now: &Stamp, ink: &Ink) -> El {
    let on = s.notes.open.as_deref() == Some(n.id.as_str());
    let id = n.id.clone();
    let when = n.updated.or(n.created).map(|w| format!("{} \u{b7} {}", day_text(&w, now), clock_text(&w))).unwrap_or_default();
    let mut item = col()
        .key(format!("note-{}", n.id))
        .role("button")
        .label(n.heading().to_owned())
        .focusable(true)
        .w_full()
        .px(12.0)
        .py(10.0)
        .gap(3.0)
        .radius(9.0)
        .fill(if on { ink.accent.alpha(0.18) } else { Color::TRANSPARENT })
        .hover(Color::WHITE.alpha(if on { 0.02 } else { 0.05 }))
        .on_press(move |s: &mut App| s.notes.open_note(&id))
        .child(text(n.heading().to_owned()).font(t(13.0, 600.0)).color(ink.fg).truncate().a11y_hidden());
    let preview = n.preview();
    if !preview.is_empty() {
        item = item.child(text(preview).font(t(12.0, 400.0)).color(ink.muted).truncate().a11y_hidden());
    }
    item.child(text(when).font(t(11.0, 400.0)).color(ink.muted.alpha(0.75)).a11y_hidden())
}

// ---- the note being written -------------------------------------------------------------------------------------------------------

fn editor(s: &App, ink: &Ink) -> El {
    let Some(id) = s.notes.open.clone() else { return empty(ink) };
    let now = Stamp::now();
    let note = s.notes.data.notes.iter().find(|n| n.id == id);
    let when = note.and_then(|n| n.updated.or(n.created)).map(|w| format!("Edited {} \u{b7} {}", day_text(&w, &now), clock_text(&w))).unwrap_or_default();
    let saved = s.notes.title.text == note.map_or("", |n| n.title.as_str()) && s.notes.body.text == note.map_or("", |n| n.body.as_str());
    let controls = if s.notes.confirm_delete.as_deref() == Some(id.as_str()) {
        let yes = id.clone();
        row()
            .key("note-confirm")
            .gap(6.0)
            .items_center()
            .child(text("Delete this note?").font(t(12.0, 400.0)).color(ink.muted))
            .child(button("note-delete-yes", "Delete", ink, Look::Primary).on_press(move |s: &mut App| {
                s.notes.delete_note(&yes);
            }))
            .child(button("note-delete-no", "Keep", ink, Look::Plain).on_press(|s: &mut App| s.notes.confirm_delete = None))
    } else {
        let ask = id.clone();
        row().key("note-tools").items_center().child(icon_button("note-delete", &icons::TRASH, "Delete this note", ink.bad).on_press(move |s: &mut App| s.notes.ask_delete(&ask)))
    };
    let title = text_input(&s.notes.title, t(22.0, 650.0))
        .key("note-title")
        .single_line()
        .w_full()
        .color(ink.fg)
        .placeholder("Title", ink.muted)
        .on_edit(edit_access(|s: &mut App| s.notes.title_edit()));
    let body = auto_grow(
        text_input(&s.notes.body, t(14.0, 400.0))
            .key("note-body")
            .w_full()
            .color(ink.fg)
            .placeholder("Start writing\u{2026} or say \u{201c}note down \u{2026}\u{201d} and Orbit adds one for you.", ink.muted)
            .on_edit(edit_access(|s: &mut App| s.notes.body_edit())),
        14,
        400,
    );
    col()
        .key("note-editor")
        .grow(1.0)
        .min_w(0.0)
        .h_full()
        .px(30.0)
        .py(22.0)
        .gap(10.0)
        .child(row().key("note-head").w_full().gap(12.0).items_center().child(el().grow(1.0).min_w(0.0).child(title)).child(controls))
        .child(
            row()
                .key("note-meta")
                .gap(10.0)
                .items_center()
                .child(text(when).font(t(11.5, 400.0)).color(ink.muted))
                .child(text(if saved { "Saved" } else { "Saving\u{2026}" }).key("note-saved").font(t(11.5, 500.0)).color(if saved { ink.accent_text } else { ink.muted })),
        )
        .child(el().key("note-rule").w_full().h(1.0).fill(ink.hair))
        .child(col().key("note-body-scroll").w_full().grow(1.0).min_h(0.0).scroll(ScrollSpec { stick_bottom: false }).child(body))
        .child(notice(s.notes.notice.as_ref().map(|n| (n.text.as_str(), n.good)), ink))
}

fn empty(ink: &Ink) -> El {
    col()
        .key("nothing-open")
        .grow(1.0)
        .min_w(0.0)
        .h_full()
        .center()
        .gap(12.0)
        .child(text("Nothing open").font(t(15.0, 600.0)).color(ink.fg))
        .child(text("Create a note, or say \u{201c}note down \u{2026}\u{201d} and Orbit writes it for you: plans, checklists, ideas.").font(t(12.5, 400.0)).color(ink.muted).align(rune::Align::Center).wrap().max_w(330.0))
        .child(icon_label_button("empty-new-note", &icons::PLUS, "New note", ink, Look::Primary).on_press(|s: &mut App| s.notes.new_note(&Stamp::now())))
}

// ---- tasks ---------------------------------------------------------------------------------------------------------------------------

/// Which list to look at (and add to): All, the plain Tasks, and each list Orbit or you made ("add tomato to my shopping list"),
/// with a box to start a new one.
fn list_chips(s: &App, ink: &Ink) -> El {
    let mut chips = row().key("list-chips").w_full().gap(6.0).flex_wrap().gaps(6.0, 6.0);
    let choices = s.notes.list_choices();
    if choices.len() > 2 || s.notes.list.as_deref().is_some_and(|l| !l.is_empty()) {
        for (list, label, open) in choices {
            let key = match list.as_deref() {
                None => "list-all".to_owned(),
                Some("") => "list-plain".to_owned(),
                Some(name) => format!("list-{}", name.replace(' ', "-")),
            };
            let on = s.notes.list == list;
            let shown = if open > 0 && list.is_some() { format!("{label} \u{b7} {open}") } else { label };
            chips = chips.child(chip(key, &shown, on, ink).on_press(move |s: &mut App| s.notes.select_list(list.clone())));
        }
    }
    let input = text_input(&s.notes.new_list, t(12.5, 400.0))
        .key("new-list-input")
        .label("New list name")
        .single_line()
        .grow(1.0)
        .min_w(0.0)
        .color(ink.fg)
        .placeholder("New list, like shopping\u{2026}", ink.muted)
        .on_edit(edit_access(|s: &mut App| s.notes.list_edit()))
        .on_submit(|s: &mut App, _| {
            s.notes.start_list();
        });
    col().key("lists").w_full().gap(8.0).child(chips).child(
        row()
            .key("new-list-row")
            .w_full()
            .gap(8.0)
            .items_center()
            .child(row().key("new-list").grow(1.0).min_w(0.0).h(32.0).px(12.0).items_center().radius(9.0).fill(ink.field).border(1.0, Color::WHITE.alpha(0.10)).child(input))
            .child(icon_button("add-list", &icons::PLUS, "Start the list", ink.accent_text).on_press(|s: &mut App| {
                s.notes.start_list();
            })),
    )
}

fn add_task_row(s: &App, ink: &Ink) -> El {
    let placeholder = match s.notes.list.as_deref() {
        Some(name) if !name.is_empty() => format!("Add to {}\u{2026}", list_title(name).to_lowercase()),
        _ => "Add a task\u{2026}".to_owned(),
    };
    let input = text_input(&s.notes.new_task, t(13.0, 400.0))
        .key("new-task-input")
        .label("Add a task")
        .single_line()
        .grow(1.0)
        .min_w(0.0)
        .color(ink.fg)
        .placeholder(&placeholder, ink.muted)
        .on_edit(edit_access(|s: &mut App| s.notes.task_edit()))
        .on_submit(|s: &mut App, _| {
            s.notes.add_task(&Stamp::now());
        });
    row()
        .key("new-task-row")
        .w_full()
        .gap(8.0)
        .items_center()
        .child(row().key("new-task").grow(1.0).min_w(0.0).h(36.0).px(12.0).items_center().radius(9.0).fill(ink.field).border(1.0, Color::WHITE.alpha(0.10)).child(input))
        .child(icon_button("add-task", &icons::PLUS, "Add the task", ink.accent_text).on_press(|s: &mut App| {
            s.notes.add_task(&Stamp::now());
        }))
}

fn task_matches(task: &Task, words: &[String]) -> bool {
    let hay = task.text.to_lowercase();
    words.iter().all(|w| hay.contains(w.as_str()))
}

fn task_list(s: &App, ink: &Ink) -> El {
    let words: Vec<String> = s.notes.search.text.to_lowercase().split_whitespace().map(str::to_owned).collect();
    let tasks: Vec<&Task> = s.notes.data.tasks.iter().filter(|t| s.notes.in_view(t) && task_matches(t, &words)).collect();
    if tasks.is_empty() {
        let said = match (s.notes.tasks_in_view(), s.notes.list.as_deref()) {
            (0, Some(name)) if !name.is_empty() => format!("Nothing on the {} list yet.", name),
            (0, _) => "No tasks yet.".to_owned(),
            _ => "No tasks match that.".to_owned(),
        };
        return text(said).key("no-tasks").font(t(12.5, 400.0)).color(ink.muted).px(4.0);
    }
    let mut list = col().key("task-list").w_full().gap(2.0);
    for task in tasks {
        let (toggle, remove) = (task.id.clone(), task.id.clone());
        let done = task.done;
        list = list.child(
            row()
                .key(format!("task-{}", task.id))
                .w_full()
                .px(8.0)
                .py(6.0)
                .gap(10.0)
                .items_center()
                .radius(8.0)
                .hover(Color::WHITE.alpha(0.04))
                .child(
                    row()
                        .key(format!("task-{}-box", task.id))
                        .role("checkbox")
                        .label(task.text.clone())
                        .checked(done)
                        .focusable(true)
                        .size(20.0, 20.0)
                        .center()
                        .radius(10.0)
                        .fill(if done { ink.accent } else { Color::TRANSPARENT })
                        .border(1.5, if done { ink.accent } else { Color::WHITE.alpha(0.3) })
                        .on_press(move |s: &mut App| {
                            s.notes.toggle_task(&toggle);
                        })
                        .children(done.then(|| icon(&icons::CHECK, 12.0).color(Color::WHITE))),
                )
                .child(text(task.text.clone()).font(t(13.0, 400.0)).color(if done { ink.muted } else { ink.fg }).wrap().grow(1.0).min_w(0.0))
                .children((s.notes.list.is_none() && !task.list.is_empty()).then(|| badge(&list_title(&task.list), ink).key(format!("task-{}-list", task.id))))
                .child(icon_button(format!("task-{}-remove", task.id), &icons::CLOSE, "Remove the task", ink.muted).on_press(move |s: &mut App| {
                    s.notes.delete_task(&remove);
                })),
        );
    }
    col().key("task-scroll").w_full().grow(1.0).min_h(0.0).scroll(ScrollSpec { stick_bottom: false }).child(list)
}

fn tasks_summary(s: &App, ink: &Ink) -> El {
    let open = s.notes.open_tasks();
    let done = s.notes.tasks_in_view() - open;
    let which = match s.notes.list.as_deref() {
        None => "ALL LISTS".to_owned(),
        Some(name) => list_title(name).to_uppercase(),
    };
    let headline = match (open, done) {
        (0, 0) => "Nothing to do".to_owned(),
        (0, _) => "All done".to_owned(),
        (n, _) => format!("{n} to do"),
    };
    let mut panel = col()
        .key("tasks-summary")
        .grow(1.0)
        .min_w(0.0)
        .h_full()
        .center()
        .gap(12.0)
        .child(icon(&icons::TASKS, 28.0).color(ink.accent_text))
        .child(caps(&which, ink).key("tasks-which"))
        .child(text(headline).font(t(20.0, 650.0)).color(ink.fg))
        .child(text(if done > 0 { format!("{done} finished") } else { "Nothing finished yet".to_owned() }).font(t(12.5, 400.0)).color(ink.muted));
    if done > 0 {
        panel = panel.child(button("clear-done", "Clear finished", ink, Look::Plain).on_press(|s: &mut App| {
            s.notes.clear_done();
        }));
    }
    panel
        .child(
            text("Say \u{201c}add tomato and pasta to my shopping list\u{201d} and they appear here, on their own list. Say \u{201c}mark tomato as done\u{201d} to tick one off.")
                .font(t(12.0, 400.0))
                .color(ink.muted)
                .align(rune::Align::Center)
                .wrap()
                .max_w(330.0),
        )
        .child(notice(s.notes.notice.as_ref().map(|n| (n.text.as_str(), n.good)), ink))
}

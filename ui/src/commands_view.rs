//! The Commands page, drawn: your own commands as a table (say a phrase; Orbit runs the same steps, in the same order, every time), and
//! the form that makes or changes one. All the rules live in `editor`; this only shows its state and turns clicks into calls on it.

use rune::{Color, El, Icon, ScrollSpec, col, edit_access, el, icon, row, text};

use crate::app::App;
use crate::editor::{Draft, StepKind};
use crate::icons;
use crate::settings::{Command, Step};
use crate::view::Ink;
use crate::widgets::{Look, button, caps, divider, field, icon_button, icon_label_button, notice, page_header, switch, t};

pub(crate) fn page(s: &App, ink: &Ink) -> El {
    match &s.prefs.draft {
        Some(d) => editor_page(d, ink),
        None => list_page(s, ink),
    }
}

fn prefs_notice(s: &App, ink: &Ink) -> El {
    notice(s.prefs.notice.as_ref().map(|n| (n.text.as_str(), n.good)), ink)
}

fn step_glyph(step: &Step) -> &'static Icon {
    match step {
        Step::Open(_) => &icons::EXTERNAL,
        Step::Keys(_) => &icons::KEYBOARD,
        Step::Type(_) => &icons::TYPE,
        Step::Wait(_) => &icons::CLOCK,
    }
}

// ---- the list -------------------------------------------------------------------------------------------------------------------

fn list_page(s: &App, ink: &Ink) -> El {
    let prefs = &s.prefs;
    let new = icon_label_button("new-command", &icons::PLUS, "New command", ink, Look::Primary).on_press(|s: &mut App| s.prefs.new_command());
    let mut body = col().key("commands").w_full().gap(16.0);
    if !prefs.problems.is_empty() {
        body = body.child(
            col()
                .key("problems")
                .w_full()
                .padding(14.0)
                .gap(4.0)
                .radius(10.0)
                .border(1.0, ink.bad.alpha(0.5))
                .child(text("Orbit could not read part of the settings file:").font(t(12.0, 600.0)).color(ink.bad))
                .children(prefs.problems.iter().enumerate().map(|(i, p)| text(p.clone()).key(format!("problem-{i}")).font(t(12.0, 400.0)).color(ink.muted).wrap())),
        );
    }
    let commands = &prefs.settings.commands;
    if commands.is_empty() {
        body = body.child(
            col()
                .key("no-commands")
                .w_full()
                .padding(26.0)
                .gap(6.0)
                .radius(12.0)
                .border(1.0, ink.hair)
                .child(text("No commands yet").font(t(14.0, 600.0)).color(ink.fg))
                .child(text("For example: say \u{201c}work mode\u{201d} and Orbit opens Spotify and your project folder.").font(t(12.5, 400.0)).color(ink.muted).wrap()),
        );
    } else {
        let mut table = col().key("command-table").w_full().child(table_head(ink));
        for (i, c) in commands.iter().enumerate() {
            table = table.child(divider(ink)).child(command_row(s, i, c, ink));
        }
        body = body.child(col().key("command-card").w_full().radius(12.0).clip_rounded(12.0).border(1.0, ink.hair).child(table));
    }
    col().key("commands-page").w_full().h_full().child(page_header("Commands", None, "Say a phrase; Orbit runs the same steps, in the same order, every time.", Some(new), ink)).child(
        col()
            .key("commands-scroll")
            .grow(1.0)
            .min_h(0.0)
            .w_full()
            .scroll(ScrollSpec { stick_bottom: false })
            .child(col().key("commands-content").w_full().max_w(1100.0).px(28.0).py(22.0).gap(10.0).child(body).child(prefs_notice(s, ink))),
    )
}

fn table_head(ink: &Ink) -> El {
    row()
        .key("table-head")
        .w_full()
        .h(36.0)
        .px(24.0)
        .gap(12.0)
        .items_center()
        .child(el().key("th-name").grow(1.0).min_w(0.0).child(caps("COMMAND", ink)))
        .child(el().key("th-runs").w(190.0).child(caps("WHAT IT RUNS", ink)))
        .child(el().key("th-steps").w(52.0).child(caps("STEPS", ink)))
        .child(el().key("th-on").w(54.0).child(caps("ON", ink)))
        .child(el().key("th-actions").w(108.0))
}

fn command_row(s: &App, i: usize, c: &Command, ink: &Ink) -> El {
    let phrases = c.phrases.iter().map(|p| format!("\u{201c}{p}\u{201d}")).collect::<Vec<_>>().join("  \u{b7}  ");
    let tone = if c.enabled { ink.fg } else { ink.muted };

    let mut runs = row().key(format!("cmd-{i}-runs")).w(190.0).gap(6.0).items_center();
    for (n, step) in c.steps.iter().take(6).enumerate() {
        if n > 0 {
            runs = runs.child(el().w(6.0).h(1.0).fill(Color::WHITE.alpha(0.2)));
        }
        runs = runs.child(icon(step_glyph(step), 14.0).color(ink.muted));
    }
    if c.steps.len() > 6 {
        runs = runs.child(text(format!("+{}", c.steps.len() - 6)).font(t(11.0, 500.0)).color(ink.muted));
    }

    let actions = if s.prefs.confirm_delete == Some(i) {
        row()
            .key(format!("cmd-{i}-confirm"))
            .gap(6.0)
            .items_center()
            .child(button(&format!("cmd-{i}-delete-yes"), "Delete", ink, Look::Primary).on_press(move |s: &mut App| {
                s.prefs.delete(i);
            }))
            .child(button(&format!("cmd-{i}-delete-no"), "Keep", ink, Look::Plain).on_press(|s: &mut App| s.prefs.confirm_delete = None))
    } else {
        row()
            .key(format!("cmd-{i}-actions"))
            .w(108.0)
            .gap(2.0)
            .items_center()
            .justify_end()
            .child(icon_button(format!("cmd-{i}-edit"), &icons::PENCIL, "Edit", ink.muted).on_press(move |s: &mut App| s.prefs.edit_command(i)))
            .child(icon_button(format!("cmd-{i}-delete"), &icons::TRASH, "Delete", ink.bad).on_press(move |s: &mut App| s.prefs.ask_delete(i)))
    };

    row()
        .key(format!("cmd-{i}"))
        .w_full()
        .h(66.0)
        .px(24.0)
        .gap(12.0)
        .items_center()
        .child(el().key(format!("cmd-{i}-bar")).absolute(0.0, 0.0).w(2.0).h_full().fill(if c.enabled { ink.accent } else { Color::WHITE.alpha(0.12) }))
        .child(
            col()
                .key(format!("cmd-{i}-text"))
                .grow(1.0)
                .min_w(0.0)
                .gap(3.0)
                .child(text(c.name.clone()).font(t(14.0, 600.0)).color(tone).truncate())
                .child(text(phrases).font(t(12.0, 400.0)).color(ink.muted).truncate()),
        )
        .child(runs)
        .child(el().key(format!("cmd-{i}-count")).w(52.0).child(text(format!("{}", c.steps.len())).font(t(12.5, 500.0).mono()).color(ink.muted)))
        .child(el().key(format!("cmd-{i}-on-cell")).w(54.0).child(switch(format!("cmd-{i}-on"), c.enabled, &format!("{} is on", c.name), ink).on_press(move |s: &mut App| {
            s.prefs.toggle(i);
        })))
        .child(actions)
}

// ---- the form -------------------------------------------------------------------------------------------------------------------

fn label_text(label: &str, ink: &Ink) -> El {
    text(label.to_owned()).font(t(11.0, 500.0).tracking(1.1)).color(ink.muted)
}

fn editor_page(d: &Draft, ink: &Ink) -> El {
    let title = if d.index.is_some() { "Edit command" } else { "New command" };
    let mut body = col().key("editor").w_full().gap(20.0);

    // the name
    body = body.child(
        col()
            .key("name-block")
            .w_full()
            .gap(8.0)
            .child(label_text("NAME", ink))
            .child(field("name".to_owned(), &d.name, "Work mode", edit_access(|s: &mut App| s.prefs.name_edit()), ink)),
    );

    // what to say
    let mut phrases = col().key("phrases-block").w_full().gap(8.0).child(label_text("WHEN YOU SAY", ink));
    for i in 0..d.phrases.len() {
        let input = field(format!("phrase-{i}"), &d.phrases[i], "work mode", edit_access(move |s: &mut App| s.prefs.phrase_edit(i)), ink);
        let mut line = row().key(format!("phrase-line-{i}")).w_full().gap(6.0).items_center().child(input);
        if d.phrases.len() > 1 {
            line = line.child(icon_button(format!("phrase-{i}-remove"), &icons::CLOSE, "Remove this phrase", ink.muted).on_press(move |s: &mut App| s.prefs.remove_phrase(i)));
        }
        phrases = phrases.child(line);
    }
    phrases = phrases
        .child(icon_label_button("add-phrase", &icons::PLUS, "Add another phrase", ink, Look::Plain).on_press(|s: &mut App| s.prefs.add_phrase()).self_start())
        .child(text("Say it just like this. \u{201c}Run work mode\u{201d} and \u{201c}please work mode\u{201d} work too.").font(t(11.5, 400.0)).color(ink.muted));
    body = body.child(phrases);

    // what to do
    let mut steps = col().key("steps-block").w_full().gap(10.0).child(label_text("THEN ORBIT WILL", ink));
    for (i, step) in d.steps.iter().enumerate() {
        steps = steps.child(step_card(i, step.kind, &step.edit, d.steps.len(), ink));
    }
    let mut adds = row().key("add-steps").gap(8.0).flex_wrap().gaps(8.0, 8.0);
    for kind in StepKind::ALL {
        adds = adds.child(icon_label_button(&format!("add-{}", kind.label()), &icons::PLUS, kind.label(), ink, Look::Plain).on_press(move |s: &mut App| s.prefs.add_step(kind)));
    }
    steps = steps.child(adds);
    if d.sends_keys() {
        steps = steps.child(
            text("This presses keys or types, so Orbit asks you \u{201c}yes or no\u{201d} before it runs, listing exactly what it will do.")
                .font(t(11.5, 400.0))
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
                .border(1.0, ink.bad.alpha(0.5))
                .children(d.errors.iter().enumerate().map(|(i, e)| text(e.clone()).key(format!("error-{i}")).font(t(12.0, 400.0)).color(ink.bad).wrap())),
        );
    }
    body = body.child(
        row()
            .key("editor-actions")
            .gap(10.0)
            .child(button("save-command", "Save", ink, Look::Primary).on_press(|s: &mut App| {
                s.prefs.save_draft();
            }))
            .child(button("cancel-command", "Cancel", ink, Look::Plain).on_press(|s: &mut App| s.prefs.cancel_draft())),
    );
    col().key("editor-page").w_full().h_full().child(page_header(title, None, "Pick what to say, then what Orbit should do.", None, ink)).child(
        col()
            .key("editor-scroll")
            .grow(1.0)
            .min_h(0.0)
            .w_full()
            .scroll(ScrollSpec { stick_bottom: false })
            .child(col().key("editor-content").w_full().max_w(780.0).px(28.0).py(22.0).child(body)),
    )
}

fn step_card(i: usize, kind: StepKind, edit: &rune::TextEdit, count: usize, ink: &Ink) -> El {
    let mut kinds = row().key(format!("step-{i}-kinds")).gap(6.0).flex_wrap().gaps(6.0, 6.0);
    for k in StepKind::ALL {
        kinds = kinds.child(crate::widgets::chip(format!("step-{i}-kind-{}", k.label()), k.label(), k == kind, ink).on_press(move |s: &mut App| s.prefs.set_step_kind(i, k)));
    }
    let mut tools = row().key(format!("step-{i}-tools")).gap(2.0).items_center();
    if i > 0 {
        tools = tools.child(text_button(format!("step-{i}-up"), "\u{2191}", "Move this step up", ink).on_press(move |s: &mut App| s.prefs.move_step(i, -1)));
    }
    if i + 1 < count {
        tools = tools.child(text_button(format!("step-{i}-down"), "\u{2193}", "Move this step down", ink).on_press(move |s: &mut App| s.prefs.move_step(i, 1)));
    }
    if count > 1 {
        tools = tools.child(icon_button(format!("step-{i}-remove"), &icons::CLOSE, "Remove this step", ink.muted).on_press(move |s: &mut App| s.prefs.remove_step(i)));
    }
    col()
        .key(format!("step-{i}"))
        .w_full()
        .padding(14.0)
        .gap(10.0)
        .radius(10.0)
        .fill(ink.raised)
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
        .child(text(kind.hint()).font(t(11.5, 400.0)).color(ink.muted).wrap())
}

/// A small square button with a symbol typed as text (the arrows that move a step).
fn text_button(key: String, glyph: &str, label: &str, ink: &Ink) -> El {
    row()
        .key(key)
        .role("button")
        .label(label.to_owned())
        .focusable(true)
        .size(30.0, 30.0)
        .center()
        .radius(8.0)
        .hover(Color::WHITE.alpha(0.08))
        .child(text(glyph.to_owned()).font(t(14.0, 400.0)).color(ink.muted).a11y_hidden())
}

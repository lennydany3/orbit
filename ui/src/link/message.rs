//! What Orbit sends, as typed values, and the few things the window may send back. The messages are
//! documented in `src/orbit/ipc/protocol.py` in the Orbit project; this is their Rust reading.
//!
//! Parsing is forgiving about what it does not know (an unknown message or state is dropped, never an error), because
//! the two sides are updated separately, and strict about what it does use.

use serde_json::{Value, json};

use crate::orbit::OrbState;

#[derive(Debug, Clone, PartialEq)]
pub enum Incoming {
    State { state: OrbState, reason: Option<String>, seconds: Option<f64> },
    Level(f32),
    /// Sent once on connecting: where Orbit is right now.
    Hello { state: OrbState, level: f32 },
    Transcript { who: String, text: String },
    Reply { text: String, spoken: String, kind: String, emotion: String },
    Confirm { id: i64, question: String, action: String },
    ConfirmDone { id: i64, answer: bool, by: String },
    /// A reminder just came due (Orbit says it aloud itself; the window shows it and re-reads the list). `kind` is "reminder", "timer"
    /// or "alarm"; `ringing`: it keeps ringing until it is stopped (by voice, the talk key, or the window's Stop button).
    Reminder { id: String, title: String, text: String, kind: String, ringing: bool },
    /// The ringing stopped (so the Stop button goes away).
    AlarmStopped { id: String },
    Error(String),
}

fn text(v: &Value, key: &str) -> Option<String> {
    v.get(key)?.as_str().map(str::to_owned)
}

fn state(v: &Value) -> Option<OrbState> {
    OrbState::from_id(v.get("state")?.as_str()?)
}

fn level(v: &Value) -> Option<f32> {
    let x = v.get("level")?.as_f64()?;
    x.is_finite().then(|| x.clamp(0.0, 1.0) as f32)
}

/// Zero, one or two messages: a state and a level may share an object (the design page's own format).
pub fn parse(raw: &str) -> Vec<Incoming> {
    let Ok(v) = serde_json::from_str::<Value>(raw) else { return Vec::new() };
    if !v.is_object() {
        return Vec::new();
    }
    let kind = v.get("type").and_then(Value::as_str);
    let one = |m: Option<Incoming>| m.into_iter().collect::<Vec<_>>();
    match kind {
        None => {
            let mut out = Vec::new();
            if let Some(state) = state(&v) {
                out.push(Incoming::State { state, reason: text(&v, "reason"), seconds: v.get("seconds").and_then(Value::as_f64) });
            }
            if let Some(l) = level(&v) {
                out.push(Incoming::Level(l));
            }
            out
        }
        Some("hello") => one(state(&v).map(|state| Incoming::Hello { state, level: level(&v).unwrap_or(0.0) })),
        Some("transcript") => one((|| Some(Incoming::Transcript { who: text(&v, "who")?, text: text(&v, "text")? }))()),
        Some("reply") => one((|| {
            Some(Incoming::Reply { text: text(&v, "text")?, spoken: text(&v, "spoken").unwrap_or_default(), kind: text(&v, "kind")?, emotion: text(&v, "emotion").unwrap_or_default() })
        })()),
        Some("confirm") => one((|| Some(Incoming::Confirm { id: v.get("id")?.as_i64()?, question: text(&v, "question").unwrap_or_default(), action: text(&v, "action")? }))()),
        Some("confirm_done") => one((|| Some(Incoming::ConfirmDone { id: v.get("id")?.as_i64()?, answer: v.get("answer")?.as_bool()?, by: text(&v, "by").unwrap_or_default() }))()),
        Some("reminder") => one((|| {
            Some(Incoming::Reminder {
                id: text(&v, "id")?,
                title: text(&v, "title")?,
                text: text(&v, "text").unwrap_or_default(),
                kind: text(&v, "kind").filter(|k| matches!(k.as_str(), "timer" | "alarm")).unwrap_or_else(|| "reminder".to_owned()),
                ringing: v.get("ringing").and_then(Value::as_bool).unwrap_or(false),
            })
        })()),
        Some("alarm_stopped") => one(text(&v, "id").map(|id| Incoming::AlarmStopped { id })),
        Some("error") => one(text(&v, "message").map(Incoming::Error)),
        Some(_) => Vec::new(),
    }
}

/// The yes or no for an open question.
pub fn confirm_answer(id: i64, yes: bool) -> String {
    json!({"type": "confirm", "id": id, "yes": yes}).to_string()
}

/// The Play button on one of the saved playlists: one of the few things the window may send. It names the playlist by the id it has in
/// `music.json`, nothing else, so it can only ever start something the user saved.
pub fn play_request(playlist_id: &str) -> String {
    json!({"type": "play", "id": playlist_id}).to_string()
}

/// The Stop button on a reminder or timer that is ringing: it names the one that is ringing, so it can only ever silence that.
pub fn dismiss_request(reminder_id: &str) -> String {
    json!({"type": "dismiss", "id": reminder_id}).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_state_message_in_the_design_pages_format() {
        assert_eq!(parse(r#"{"state":"listening"}"#), [Incoming::State { state: OrbState::Listening, reason: None, seconds: None }]);
        assert_eq!(
            parse(r#"{"state":"follow_up","reason":"listening 8s","seconds":8.0}"#),
            [Incoming::State { state: OrbState::FollowUp, reason: Some("listening 8s".into()), seconds: Some(8.0) }]
        );
        assert_eq!(parse(r#"{"state":"follow-up"}"#).len(), 1, "the hyphenated spelling too");
    }

    #[test]
    fn a_level_alone_or_beside_a_state() {
        assert_eq!(parse(r#"{"level":0.42}"#), [Incoming::Level(0.42)]);
        assert_eq!(
            parse(r#"{"state":"speaking","level":0.7}"#),
            [Incoming::State { state: OrbState::Speaking, reason: None, seconds: None }, Incoming::Level(0.7)]
        );
        assert_eq!(parse(r#"{"level":7}"#), [Incoming::Level(1.0)], "clamped");
        assert_eq!(parse(r#"{"level":-1}"#), [Incoming::Level(0.0)]);
        assert!(parse(r#"{"level":"loud"}"#).is_empty());
    }

    #[test]
    fn hello_carries_the_current_state_and_level() {
        assert_eq!(parse(r#"{"type":"hello","version":1,"state":"thinking","level":0.5}"#), [Incoming::Hello { state: OrbState::Thinking, level: 0.5 }]);
        assert_eq!(parse(r#"{"type":"hello","version":1,"state":"idle"}"#), [Incoming::Hello { state: OrbState::Idle, level: 0.0 }]);
    }

    #[test]
    fn what_was_said_and_the_reply() {
        assert_eq!(parse(r#"{"type":"transcript","who":"you","text":"hi"}"#), [Incoming::Transcript { who: "you".into(), text: "hi".into() }]);
        assert_eq!(
            parse(r#"{"type":"reply","text":"Done.","spoken":"Done.","kind":"chat","emotion":"proud"}"#),
            [Incoming::Reply { text: "Done.".into(), spoken: "Done.".into(), kind: "chat".into(), emotion: "proud".into() }]
        );
    }

    #[test]
    fn a_question_and_its_settling() {
        assert_eq!(
            parse(r#"{"type":"confirm","id":3,"question":"q","action":"close window Notepad"}"#),
            [Incoming::Confirm { id: 3, question: "q".into(), action: "close window Notepad".into() }]
        );
        assert_eq!(
            parse(r#"{"type":"confirm_done","id":3,"answer":true,"by":"ui"}"#),
            [Incoming::ConfirmDone { id: 3, answer: true, by: "ui".into() }]
        );
        assert_eq!(parse(r#"{"type":"error","message":"no such confirmation is open"}"#), [Incoming::Error("no such confirmation is open".into())]);
    }

    #[test]
    fn what_is_not_understood_is_dropped_never_an_error() {
        for raw in [
            "", "x", "[]", "7", "null", r#"{}"#, r#"{"state":"dancing"}"#, r#"{"type":"from_the_future","state":"idle"}"#,
            r#"{"type":"transcript","who":"you"}"#, r#"{"type":"confirm","id":"3","action":"a"}"#, r#"{"type":"confirm","id":3}"#,
            r#"{"type":"confirm_done","id":3}"#, r#"{"type":"hello"}"#,
        ] {
            assert!(parse(raw).is_empty(), "{raw}");
        }
    }

    #[test]
    fn the_answer_is_exactly_what_orbit_expects() {
        let v: Value = serde_json::from_str(&confirm_answer(7, false)).unwrap();
        assert_eq!(v, json!({"type": "confirm", "id": 7, "yes": false}));
    }

    #[test]
    fn the_play_button_names_a_saved_playlist_and_nothing_else() {
        let v: Value = serde_json::from_str(&play_request("p-3f9a1c2b")).unwrap();
        assert_eq!(v, json!({"type": "play", "id": "p-3f9a1c2b"}));
    }

    #[test]
    fn a_reminder_that_came_due() {
        assert_eq!(
            parse(r#"{"type":"reminder","id":"r-1","title":"Call mom","text":"Reminder: Call mom."}"#),
            [Incoming::Reminder { id: "r-1".into(), title: "Call mom".into(), text: "Reminder: Call mom.".into(), kind: "reminder".into(), ringing: false }]
        );
        assert!(parse(r#"{"type":"reminder","title":"no id"}"#).is_empty() && parse(r#"{"type":"reminder","id":"r-1"}"#).is_empty());
    }

    #[test]
    fn a_reminder_that_is_ringing_and_what_kind_it_is() {
        assert_eq!(
            parse(r#"{"type":"reminder","id":"r-2","title":"Pasta","text":"Your timer is up.","kind":"timer","ringing":true}"#),
            [Incoming::Reminder { id: "r-2".into(), title: "Pasta".into(), text: "Your timer is up.".into(), kind: "timer".into(), ringing: true }]
        );
        let alarm = parse(r#"{"type":"reminder","id":"r-3","title":"Wake up","kind":"alarm"}"#);
        assert!(matches!(&alarm[..], [Incoming::Reminder { kind, ringing: false, .. }] if kind == "alarm"));
        let odd = parse(r#"{"type":"reminder","id":"r-4","title":"x","kind":"sing","ringing":"yes"}"#);
        assert!(matches!(&odd[..], [Incoming::Reminder { kind, ringing: false, .. }] if kind == "reminder"), "an unknown kind is a reminder, and only a real true rings");
    }

    #[test]
    fn the_ringing_stopping() {
        assert_eq!(parse(r#"{"type":"alarm_stopped","id":"r-2","by":"voice"}"#), [Incoming::AlarmStopped { id: "r-2".into() }]);
        assert!(parse(r#"{"type":"alarm_stopped"}"#).is_empty());
    }

    #[test]
    fn the_stop_button_names_the_one_that_is_ringing_and_nothing_else() {
        let v: Value = serde_json::from_str(&dismiss_request("r-3f9a1c2b")).unwrap();
        assert_eq!(v, json!({"type": "dismiss", "id": "r-3f9a1c2b"}));
    }
}

//! The window's end of the link against the real Orbit side: it starts Orbit's scripted demo exactly as `--spawn-demo`
//! does (own port, own password, the pipe that ends it), connects over a real socket and plays a whole turn, answers the
//! yes/no question, and checks that closing the window stops the child. Skipped when the project's Python is not there.

use std::sync::Arc;
use std::time::{Duration, Instant};

use orbit_ui::link::message::{self, Incoming};
use orbit_ui::link::{Event, Link, Status};
use orbit_ui::orbit::OrbState;
use orbit_ui::spawn::{self, Kind, Spawned};

fn python_available() -> bool {
    let present = spawn::python_for(&spawn::project_root()).exists();
    if !present {
        eprintln!("skipped: no Python at {}", spawn::python_for(&spawn::project_root()).display());
    }
    present
}

fn waker() -> rune::Waker {
    Arc::new(|| {})
}

/// Events until `done` says stop, or a deadline.
fn collect(link: &Link, seconds: u64, mut done: impl FnMut(&[Event], &Link) -> bool) -> Vec<Event> {
    let until = Instant::now() + Duration::from_secs(seconds);
    let mut all = Vec::new();
    while Instant::now() < until {
        all.extend(link.poll());
        if done(&all, link) {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    all
}

fn messages(events: &[Event]) -> Vec<Incoming> {
    events.iter().filter_map(|e| if let Event::Message(raw) = e { Some(message::parse(raw)) } else { None }).flatten().collect()
}

fn states(events: &[Event]) -> Vec<OrbState> {
    messages(events).into_iter().filter_map(|m| if let Incoming::State { state, .. } = m { Some(state) } else { None }).collect()
}

#[test]
fn a_whole_turn_with_a_question_answered_from_the_window() {
    if !python_available() {
        return;
    }
    let child = Spawned::start(Kind::Demo).expect("the demo starts");
    let link = Link::connect(&child.url, waker()).expect("the link is to this PC");
    let mut answered = false;
    let events = collect(&link, 40, |events, link| {
        if !answered
            && let Some(Incoming::Confirm { id, .. }) = messages(events).into_iter().find(|m| matches!(m, Incoming::Confirm { .. }))
        {
            assert!(link.send(message::confirm_answer(id, true)));
            answered = true;
        }
        states(events).contains(&OrbState::FollowUp)
    });

    let statuses: Vec<Status> = events.iter().filter_map(|e| if let Event::Status(s) = e { Some(*s) } else { None }).collect();
    assert!(statuses.contains(&Status::Open), "{statuses:?}");

    let all = messages(&events);
    assert!(matches!(all.first(), Some(Incoming::Hello { .. })), "the first thing a window is told is where Orbit is: {:?}", all.first());

    use OrbState::*;
    let seen = states(&events);
    let wanted = [Wake, Listening, Transcribing, Thinking, Confirming, Thinking, Success, Speaking, FollowUp];
    let mut at = 0;
    for s in &seen {
        if at < wanted.len() && *s == wanted[at] {
            at += 1;
        }
    }
    assert_eq!(at, wanted.len(), "a whole turn, in order: {seen:?}");

    assert!(all.iter().any(|m| matches!(m, Incoming::Transcript { text, .. } if text == "Close Notepad.")));
    assert!(all.iter().any(|m| matches!(m, Incoming::Level(l) if *l > 0.3)), "the voice levels arrive");
    assert!(
        all.iter().any(|m| matches!(m, Incoming::ConfirmDone { answer: true, by, .. } if by == "ui")),
        "the answer given here is the one Orbit acted on: {all:?}"
    );
    assert!(all.iter().any(|m| matches!(m, Incoming::Reply { text, .. } if text == "Okay, I closed Notepad.")), "so it went ahead");
    assert!(all.iter().any(|m| matches!(m, Incoming::State { state: FollowUp, seconds: Some(s), .. } if *s == 8.0)), "with the window it announced");
}

#[test]
fn closing_the_window_stops_orbit_by_itself_and_the_link_notices() {
    if !python_available() {
        return;
    }
    let mut child = Spawned::start(Kind::Demo).expect("the demo starts");
    let url = child.url.clone();
    let link = Link::connect(&url, waker()).unwrap();
    collect(&link, 20, |events, _| events.contains(&Event::Status(Status::Open)));

    let started = Instant::now();
    child.shutdown(Duration::from_secs(6));                  // what the window's exit does: end the pipe, wait
    assert!(started.elapsed() < Duration::from_secs(4), "it stopped on its own, it was not killed after the grace period: {:?}", started.elapsed());
    assert!(child.exited().is_some());

    let after = collect(&link, 10, |events, _| events.contains(&Event::Status(Status::Closed)));
    assert!(after.contains(&Event::Status(Status::Closed)), "the window sees Orbit go: {after:?}");
}

#[test]
fn a_wrong_password_never_connects_and_nothing_is_heard() {
    if !python_available() {
        return;
    }
    let child = Spawned::start(Kind::Demo).expect("the demo starts");
    let (base, _) = child.url.rsplit_once("token=").unwrap();
    let link = Link::connect(&format!("{base}token=not-the-token"), waker()).unwrap();
    let events = collect(&link, 3, |_, _| false);
    assert!(!events.iter().any(|e| matches!(e, Event::Status(Status::Open) | Event::Message(_))), "{events:?}");
    assert!(events.contains(&Event::Status(Status::Closed)), "it keeps trying: {events:?}");
}

#[test]
fn stopping_while_orbit_is_not_there_returns_at_once() {
    let port = spawn::free_port().unwrap();                  // nothing listens on it
    let link = Link::connect(&format!("ws://127.0.0.1:{port}/?token=x"), waker()).unwrap();
    std::thread::sleep(Duration::from_millis(200));
    let started = Instant::now();
    link.stop();
    assert!(started.elapsed() < Duration::from_millis(100), "stopping never waits on the network: {:?}", started.elapsed());
}

#[test]
fn a_window_started_before_orbit_connects_as_soon_as_orbit_is_up() {
    if !python_available() {
        return;
    }
    // Nothing is listening yet: the link retries. Then Orbit starts on that very port, and it connects without being asked.
    let root = spawn::project_root();
    let port = spawn::free_port().unwrap();
    let token = spawn::random_token();
    let plan = spawn::plan(Kind::Demo, &root, spawn::python_for(&root), port, token.clone());
    let link = Link::connect(&plan.url, waker()).unwrap();
    std::thread::sleep(Duration::from_millis(700));
    let mut child = std::process::Command::new(&plan.program)
        .args(&plan.args)
        .current_dir(&plan.cwd)
        .env("ORBIT_IPC_TOKEN", &token)
        .stdin(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let events = collect(&link, 25, |events, _| events.contains(&Event::Status(Status::Open)));
    drop(child.stdin.take());                                // end the pipe: the demo stops
    let _ = child.wait();
    assert!(events.contains(&Event::Status(Status::Closed)), "it had been retrying: {events:?}");
    assert!(events.contains(&Event::Status(Status::Open)), "{events:?}");
}

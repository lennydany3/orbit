//! Lets each one-shot flash finish before the next state shows.
//!
//! Orbit reports what happened: it says `wake` and, a tenth of a second later, `listening`; it says `success` and in the same
//! breath `speaking`. Shown as they arrive the flash would be cut off before it could be seen. The sphere owns the animation
//! lengths, so it paces them: a state waits for the flash before it to play for its minimum time, and states queue up in order.
//! Everything else shows the moment it arrives. The kill switch (`muted`) never waits.

use std::collections::VecDeque;

use crate::orbit::OrbState;

/// How long a state must be on screen before the next one may replace it.
pub fn minimum_time(state: OrbState) -> f64 {
    match state {
        OrbState::Wake => 0.4,
        OrbState::Success => 0.75,
        OrbState::Error => 0.7,
        _ => 0.0,
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Due {
    pub state: OrbState,
    /// The follow-up window Orbit announced with the state.
    pub seconds: Option<f64>,
    /// When it should show: the moment it arrived, or the moment the flash before it was done.
    pub at: f64,
}

#[derive(Debug, Clone, Copy)]
struct Waiting {
    state: OrbState,
    seconds: Option<f64>,
    arrived: f64,
}

#[derive(Debug, Default)]
pub struct Pacer {
    queue: VecDeque<Waiting>,
    /// The earliest the next state may show (the current flash's end).
    free_at: f64,
}

impl Pacer {
    pub fn new() -> Pacer {
        Pacer::default()
    }

    pub fn push(&mut self, state: OrbState, seconds: Option<f64>, now: f64) {
        if state == OrbState::Muted {
            self.queue.clear();
            self.free_at = 0.0;
        }
        self.queue.push_back(Waiting { state, seconds, arrived: now });
    }

    /// Forgets everything waiting (a new connection starts from what Orbit says it is now).
    pub fn clear(&mut self) {
        self.queue.clear();
        self.free_at = 0.0;
    }

    /// When the state at the front of the queue may show, so the app can ask to wake then.
    pub fn next_due(&self) -> Option<f64> {
        self.queue.front().map(|w| w.arrived.max(self.free_at))
    }

    pub fn is_empty(&self) -> bool {
        self.queue.is_empty()
    }

    /// The states to show by `now`, in order, each with the time it is due (a late frame still stamps it on time).
    pub fn drain(&mut self, now: f64) -> Vec<Due> {
        let mut out = Vec::new();
        while let Some(w) = self.queue.front().copied() {
            let at = w.arrived.max(self.free_at);
            if at > now {
                break;
            }
            self.queue.pop_front();
            self.free_at = at + minimum_time(w.state);
            out.push(Due { state: w.state, seconds: w.seconds, at });
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use OrbState::*;

    fn states(due: &[Due]) -> Vec<OrbState> {
        due.iter().map(|d| d.state).collect()
    }

    #[test]
    fn ordinary_states_show_the_moment_they_arrive() {
        let mut p = Pacer::new();
        p.push(Listening, None, 5.0);
        assert_eq!(p.drain(5.0), vec![Due { state: Listening, seconds: None, at: 5.0 }]);
        p.push(Thinking, None, 5.1);
        p.push(Speaking, None, 5.1);
        assert_eq!(states(&p.drain(5.1)), [Thinking, Speaking]);
        assert!(p.is_empty() && p.next_due().is_none());
    }

    #[test]
    fn wake_plays_for_four_tenths_before_listening_shows() {
        let mut p = Pacer::new();
        p.push(Wake, None, 10.0);
        p.push(Listening, None, 10.1);                   // Orbit follows the wake word almost at once
        assert_eq!(states(&p.drain(10.1)), [Wake]);
        assert_eq!(p.next_due(), Some(10.4));
        assert!(p.drain(10.39).is_empty());
        assert_eq!(p.drain(10.4), vec![Due { state: Listening, seconds: None, at: 10.4 }]);
    }

    #[test]
    fn success_holds_back_the_reply_for_three_quarters_of_a_second() {
        let mut p = Pacer::new();
        p.push(Success, None, 3.0);
        p.push(Speaking, None, 3.0);
        p.push(FollowUp, Some(8.0), 5.0);
        assert_eq!(states(&p.drain(3.0)), [Success]);
        assert_eq!(p.next_due(), Some(3.75));
        let later = p.drain(3.8);
        assert_eq!(later, vec![Due { state: Speaking, seconds: None, at: 3.75 }], "stamped on time though the frame was late");
        assert_eq!(p.drain(5.0), vec![Due { state: FollowUp, seconds: Some(8.0), at: 5.0 }], "the follow-up waited for nothing");
    }

    #[test]
    fn two_flashes_in_a_row_each_get_their_time() {
        let mut p = Pacer::new();
        p.push(Error, None, 1.0);
        p.push(Success, None, 1.0);
        p.push(Speaking, None, 1.0);
        let all = p.drain(10.0);
        assert_eq!(all.iter().map(|d| (d.state, (d.at * 100.0).round() / 100.0)).collect::<Vec<_>>(),
                   [(Error, 1.0), (Success, 1.7), (Speaking, 2.45)]);
    }

    #[test]
    fn a_flash_that_has_already_played_holds_nothing_back() {
        let mut p = Pacer::new();
        p.push(Success, None, 1.0);
        p.drain(1.0);
        p.push(Speaking, None, 4.0);
        assert_eq!(p.drain(4.0), vec![Due { state: Speaking, seconds: None, at: 4.0 }]);
    }

    #[test]
    fn the_kill_switch_never_waits_and_drops_what_was_queued() {
        let mut p = Pacer::new();
        p.push(Success, None, 1.0);
        p.push(Speaking, None, 1.0);
        p.drain(1.0);
        p.push(Muted, None, 1.1);
        assert_eq!(p.drain(1.1), vec![Due { state: Muted, seconds: None, at: 1.1 }]);
    }

    #[test]
    fn clearing_forgets_the_queue_and_any_hold() {
        let mut p = Pacer::new();
        p.push(Success, None, 1.0);
        p.drain(1.0);
        p.push(Speaking, None, 1.0);
        p.clear();
        assert!(p.is_empty());
        p.push(Listening, None, 1.2);
        assert_eq!(states(&p.drain(1.2)), [Listening]);
    }
}

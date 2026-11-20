//! The one controller every sphere on screen listens to: which state we are in, the voice level, the
//! timestamps the one-shot effects measure from, and the timers that return the sphere to idle.
//!
//! Time is the app clock in seconds (`Runtime::t`, or the virtual clock when headless), so a timed
//! hand-over is the same in a window and in a snapshot.

use super::state::OrbState;

/// When each one-shot effect last started. A large negative number means "never".
#[derive(Clone, Copy, Debug)]
pub struct Fx {
    pub wake: f64,
    pub success: f64,
    pub error: f64,
    pub follow: f64,
}

const NEVER: f64 = -99.0;

#[derive(Clone, Copy, Debug)]
struct Pending {
    due: f64,
    to: OrbState,
    /// The `entered_at` it was set for: a later state change cancels it.
    entered: f64,
}

#[derive(Clone, Debug)]
pub struct Orbit {
    pub state: OrbState,
    pub prev: OrbState,
    pub level: f32,
    pub entered_at: f64,
    pub fx: Fx,
    reduced: bool,
    /// Bumped on every state or reduced-motion change, so a sphere knows to start a new blend.
    pub epoch: u32,
    /// The clock time of the change that bumped `epoch`.
    pub changed_at: f64,
    pending: Option<Pending>,
}

impl Default for Orbit {
    fn default() -> Self {
        Orbit::new()
    }
}

impl Orbit {
    pub fn new() -> Orbit {
        Orbit {
            state: OrbState::Idle,
            prev: OrbState::Idle,
            level: 0.0,
            entered_at: 0.0,
            fx: Fx { wake: NEVER, success: NEVER, error: NEVER, follow: NEVER },
            reduced: false,
            epoch: 0,
            changed_at: 0.0,
            pending: None,
        }
    }

    pub fn reduced(&self) -> bool {
        self.reduced
    }

    pub fn set_reduced(&mut self, on: bool, t: f64) {
        if on != self.reduced {
            self.reduced = on;
            self.epoch += 1;
            self.changed_at = t;
        }
    }

    /// Enters `s` at time `t`. Setting the state you are already in restarts its one-shot.
    pub fn set_state(&mut self, s: OrbState, t: f64) {
        self.pending = None;
        self.prev = self.state;
        self.state = s;
        self.entered_at = t;
        self.epoch += 1;
        self.changed_at = t;
        match s {
            OrbState::Wake => {
                self.fx.wake = t;
                self.after(0.4, OrbState::Listening);
            }
            OrbState::Success => {
                self.fx.success = t;
                self.after(0.75, OrbState::Idle);
            }
            OrbState::Error => {
                self.fx.error = t;
                self.after(0.7, OrbState::Idle);
            }
            OrbState::FollowUp => {
                self.fx.follow = t;
                self.after(2.0, OrbState::Idle);
            }
            _ => {}
        }
    }

    fn after(&mut self, secs: f64, to: OrbState) {
        self.pending = Some(Pending { due: self.entered_at + secs, to, entered: self.entered_at });
    }

    /// When the next timed hand-over is due, so the app can ask to wake then.
    pub fn next_due(&self) -> Option<f64> {
        self.pending.map(|p| p.due)
    }

    /// Runs a hand-over that is due at `t`. True when the state changed. A late frame still
    /// stamps the change with the time it was due, so the animation is where it should be.
    pub fn tick(&mut self, t: f64) -> bool {
        let Some(p) = self.pending else { return false };
        if p.entered != self.entered_at {
            self.pending = None;
            return false;
        }
        if t >= p.due {
            self.set_state(p.to, p.due);
            return true;
        }
        false
    }

    pub fn set_level(&mut self, v: f32) {
        if v.is_finite() {
            self.level = v.clamp(0.0, 1.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wake_hands_over_to_listening_after_four_tenths_of_a_second() {
        let mut o = Orbit::new();
        o.set_state(OrbState::Wake, 10.0);
        assert_eq!(o.fx.wake, 10.0);
        assert!(!o.tick(10.39));
        assert_eq!(o.state, OrbState::Wake);
        assert!(o.tick(10.5));
        assert_eq!(o.state, OrbState::Listening);
        assert_eq!(o.entered_at, 10.4, "stamped with the due time, not the late frame");
    }

    #[test]
    fn one_shots_return_to_idle_on_their_own_clocks() {
        for (s, secs) in [(OrbState::Success, 0.75), (OrbState::Error, 0.7), (OrbState::FollowUp, 2.0)] {
            let mut o = Orbit::new();
            o.set_state(s, 1.0);
            assert_eq!(o.next_due(), Some(1.0 + secs));
            assert!(o.tick(1.0 + secs + 0.01));
            assert_eq!(o.state, OrbState::Idle);
            assert_eq!(o.next_due(), None);
        }
    }

    #[test]
    fn a_new_state_cancels_the_old_hand_over() {
        let mut o = Orbit::new();
        o.set_state(OrbState::Wake, 0.0);
        o.set_state(OrbState::Thinking, 0.1);
        assert_eq!(o.next_due(), None);
        assert!(!o.tick(5.0));
        assert_eq!(o.state, OrbState::Thinking);
    }

    #[test]
    fn every_change_bumps_the_epoch_and_stamps_its_time() {
        let mut o = Orbit::new();
        let e = o.epoch;
        o.set_state(OrbState::Listening, 3.0);
        o.set_reduced(true, 4.0);
        assert_eq!(o.epoch, e + 2);
        assert_eq!(o.changed_at, 4.0);
        o.set_reduced(true, 5.0);
        assert_eq!(o.epoch, e + 2, "no change, no bump");
    }

    #[test]
    fn level_is_clamped_and_ignores_garbage() {
        let mut o = Orbit::new();
        o.set_level(1.7);
        assert_eq!(o.level, 1.0);
        o.set_level(f32::NAN);
        assert_eq!(o.level, 1.0);
        o.set_level(-3.0);
        assert_eq!(o.level, 0.0);
    }
}

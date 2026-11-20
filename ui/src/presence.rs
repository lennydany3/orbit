//! When the floating orb is on screen.
//!
//! It appears the moment Orbit is doing anything (a wake word, a question, an answer to read) and goes away shortly after
//! everything is quiet again, so it never sits over your work. Pinned (the hotkey, or a click on the tray icon), it stays until
//! you pin it off. The lingering seconds keep it from flickering between the steps of one turn.

/// How long it stays after the last thing it was showing.
pub const LINGER_SECONDS: f64 = 1.6;

#[derive(Debug, Default)]
pub struct Presence {
    pinned: bool,
    /// The last time something was active.
    last_active: Option<f64>,
}

impl Presence {
    pub fn new() -> Presence {
        Presence::default()
    }

    pub fn pinned(&self) -> bool {
        self.pinned
    }

    pub fn toggle_pin(&mut self) {
        self.pinned = !self.pinned;
    }

    /// Whether the orb should be showing at `now`, given whether anything is active right now.
    pub fn visible(&mut self, active: bool, now: f64) -> bool {
        if active {
            self.last_active = Some(now);
        }
        self.pinned || active || self.last_active.is_some_and(|t| now < t + LINGER_SECONDS)
    }

    /// When the orb is due to go away, if only waiting is keeping it up (and that moment is still ahead).
    pub fn leaves_at(&self, now: f64) -> Option<f64> {
        if self.pinned {
            return None;
        }
        self.last_active.map(|t| t + LINGER_SECONDS).filter(|due| *due > now)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_is_hidden_until_something_happens() {
        let mut p = Presence::new();
        assert!(!p.visible(false, 0.0));
        assert!(!p.visible(false, 100.0));
        assert_eq!(p.leaves_at(0.0), None);
    }

    #[test]
    fn it_appears_at_once_and_lingers_after_things_go_quiet() {
        let mut p = Presence::new();
        assert!(p.visible(true, 10.0));
        assert!(p.visible(false, 10.5), "still there half a second later");
        assert_eq!(p.leaves_at(10.5), Some(10.0 + LINGER_SECONDS));
        assert!(!p.visible(false, 11.7), "gone after the linger");
        assert_eq!(p.leaves_at(11.7), None, "nothing left to wait for, so no more wake-ups");
    }

    #[test]
    fn activity_in_the_middle_of_the_linger_starts_it_again() {
        let mut p = Presence::new();
        p.visible(true, 5.0);
        assert!(p.visible(true, 6.0));
        assert!(p.visible(false, 7.0), "the linger counts from the last activity, not the first");
        assert!(!p.visible(false, 7.7));
    }

    #[test]
    fn a_step_gap_inside_one_turn_does_not_flicker() {
        let mut p = Presence::new();
        p.visible(true, 1.0);
        for gap in [0.2, 0.9, 1.4] {
            assert!(p.visible(false, 1.0 + gap), "{gap}");
        }
    }

    #[test]
    fn pinned_stays_until_unpinned() {
        let mut p = Presence::new();
        p.toggle_pin();
        assert!(p.pinned());
        assert!(p.visible(false, 1000.0));
        assert_eq!(p.leaves_at(1000.0), None, "nothing is waiting to take it away");
        p.toggle_pin();
        assert!(!p.visible(false, 1001.0), "unpinning with nothing going on hides it");
    }

    #[test]
    fn unpinning_while_active_keeps_it_for_what_is_happening() {
        let mut p = Presence::new();
        p.toggle_pin();
        p.toggle_pin();
        assert!(p.visible(true, 3.0));
    }
}

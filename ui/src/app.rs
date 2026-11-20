//! The app's state and the rules that move it: the shared orbit, the dev panel's controls, the
//! scripted demo turn and the simulated voice. No drawing and no windowing, so all of it is tested.

use crate::orbit::{HasOrbit, OrbState, Orbit};

pub struct DemoStep {
    pub state: OrbState,
    pub ms: f64,
    /// The mic or voice level is simulated during this step.
    pub voice: bool,
    pub who: &'static str,
    pub cap: &'static str,
    /// The chip lit in the panel while the step runs; empty for the unlabelled pause at the end.
    pub chip: &'static str,
}

const fn step(state: OrbState, ms: f64, voice: bool, who: &'static str, cap: &'static str, chip: &'static str) -> DemoStep {
    DemoStep { state, ms, voice, who, cap, chip }
}

/// One full turn, looping: wake word, speech, transcription, thinking, a click, the reply, follow-up.
pub const DEMO: [DemoStep; 9] = [
    step(OrbState::Idle, 1500.0, false, "", "", "Idle"),
    step(OrbState::Wake, 420.0, false, "You", "Hey Orbit", "Wake"),
    step(OrbState::Listening, 2600.0, true, "You", "click the first video", "Listening"),
    step(OrbState::Transcribing, 1000.0, false, "You", "Click the first video.", "Transcribing"),
    step(OrbState::Thinking, 1800.0, false, "Orbit", "Finding the first video on the page", "Thinking"),
    step(OrbState::Success, 800.0, false, "Orbit", "Clicked the first video", "Click"),
    step(OrbState::Speaking, 2300.0, true, "Orbit", "Playing the first video.", "Speaking"),
    step(OrbState::FollowUp, 2000.0, false, "Orbit", "Listening for a follow-up", "Follow-up"),
    step(OrbState::Idle, 2000.0, false, "", "", ""),
];

/// A speech-like level between 0 and 1: syllables, phrases, pauses and a little jitter.
pub fn voice_level(t: f64) -> f32 {
    let s = 0.5 + 0.5 * (t * 15.0 + (t * 2.7).sin() * 3.0).sin();
    let w = 0.5 + 0.5 * (t * 4.1 + (t * 1.3).sin() * 2.0).sin();
    let gate = if (t * 1.3 + (t * 0.7).sin()).sin() > -0.55 { 1.0 } else { 0.15 };
    // A cheap deterministic hash of the time stands in for random jitter.
    let jitter = ((t * 91.7).sin() * 43758.5453).fract().abs() - 0.5;
    ((0.1 + 0.85 * s.powf(1.6) * (0.35 + 0.65 * w)) * gate + jitter * 0.06).clamp(0.0, 1.0) as f32
}

#[derive(Clone, Copy, Debug)]
pub struct DemoRun {
    pub step: usize,
    due: f64,
}

pub struct App {
    pub orbit: Orbit,
    /// The slider's own value, used when nothing is simulating the level.
    pub manual: f32,
    pub voice: bool,
    pub demo: Option<DemoRun>,
    /// The level the panel shows (updated about 14 times a second, not every frame).
    pub shown_level: f32,
    last_push: f64,
}

impl Default for App {
    fn default() -> Self {
        App { orbit: Orbit::new(), manual: 0.35, voice: false, demo: None, shown_level: 0.35, last_push: -1.0 }
    }
}

impl HasOrbit for App {
    fn orbit(&self) -> &Orbit {
        &self.orbit
    }
}

impl App {
    /// Picking a state by hand stops the demo.
    pub fn pick(&mut self, s: OrbState, t: f64) {
        self.demo = None;
        self.orbit.set_state(s, t);
    }

    pub fn start_demo(&mut self, t: f64) {
        self.voice = false;
        self.run_step(0, t);
    }

    pub fn stop_demo(&mut self) {
        self.demo = None;
    }

    fn run_step(&mut self, i: usize, t: f64) {
        let i = i % DEMO.len();
        let st = &DEMO[i];
        if self.orbit.state != st.state {
            self.orbit.set_state(st.state, t);
        }
        self.demo = Some(DemoRun { step: i, due: t + st.ms / 1000.0 });
    }

    /// The demo step on screen, if the demo is running.
    pub fn demo_step(&self) -> Option<&'static DemoStep> {
        self.demo.map(|d| &DEMO[d.step])
    }

    pub fn voice_active(&self) -> bool {
        self.voice || self.demo_step().is_some_and(|d| d.voice)
    }

    /// The slider moved: that value is the level, and nothing else drives it.
    pub fn set_manual_level(&mut self, v: f32) {
        self.manual = v.clamp(0.0, 1.0);
        self.voice = false;
        self.demo = None;
        self.orbit.set_level(self.manual);
        self.shown_level = self.orbit.level;
    }

    pub fn set_reduced(&mut self, on: bool, t: f64) {
        self.orbit.set_reduced(on, t);
    }

    /// When something timed is next due (a hand-over or the next demo step).
    pub fn next_due(&self) -> Option<f64> {
        match (self.demo.map(|d| d.due), self.orbit.next_due()) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (a, b) => a.or(b),
        }
    }

    /// Runs everything due by `t`, in order, then sets the level for this frame. True when something
    /// the panel shows changed.
    pub fn advance(&mut self, t: f64) -> bool {
        let mut changed = false;
        for _ in 0..32 {
            let demo_due = self.demo.map(|d| d.due);
            let Some(due) = self.next_due().filter(|d| *d <= t) else { break };
            if demo_due == Some(due) {
                let next = self.demo.map_or(0, |d| d.step + 1);
                self.run_step(next, due);
            } else {
                self.orbit.tick(due);
            }
            changed = true;
        }

        let level = if self.voice_active() {
            voice_level(t)
        } else if self.demo.is_some() {
            0.0
        } else {
            self.manual
        };
        self.orbit.set_level(level);
        if t - self.last_push > 0.07 && (self.orbit.level - self.shown_level).abs() > 0.004 {
            self.shown_level = self.orbit.level;
            self.last_push = t;
            changed = true;
        }
        changed
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_voice_stays_in_range_and_actually_varies() {
        let (mut lo, mut hi) = (1.0f32, 0.0f32);
        for i in 0..4000 {
            let v = voice_level(i as f64 * 0.01);
            assert!((0.0..=1.0).contains(&v));
            lo = lo.min(v);
            hi = hi.max(v);
        }
        assert!(lo < 0.2 && hi > 0.6, "pauses and loud syllables: {lo}..{hi}");
    }

    #[test]
    fn the_demo_plays_one_full_turn_in_order_and_loops() {
        let mut app = App::default();
        app.start_demo(10.0);
        assert_eq!(app.demo_step().unwrap().chip, "Idle");
        let mut seen = vec![app.orbit.state];
        let mut t = 10.0;
        while t < 10.0 + 16.2 {
            t += 0.05;
            app.advance(t);
            if *seen.last().unwrap() != app.orbit.state {
                seen.push(app.orbit.state);
            }
        }
        use OrbState::*;
        assert_eq!(&seen[..9], &[Idle, Wake, Listening, Transcribing, Thinking, Success, Idle, Speaking, FollowUp]);
        assert_eq!(seen[9], Idle, "the follow-up hands over to idle");
        assert!(app.demo.is_some(), "still running");
    }

    #[test]
    fn the_whole_turn_lasts_fourteen_point_four_two_seconds() {
        let total: f64 = DEMO.iter().map(|s| s.ms).sum();
        assert_eq!(total, 14_420.0);
    }

    #[test]
    fn a_late_frame_catches_up_every_step_it_missed() {
        let mut app = App::default();
        app.start_demo(0.0);
        assert!(app.advance(4.0));
        assert_eq!(app.demo_step().unwrap().chip, "Listening", "1.5 + 0.42 + 2.6 > 4, so it is in the listening step");
        assert_eq!(app.orbit.state, OrbState::Listening);
    }

    #[test]
    fn picking_a_state_stops_the_demo_and_the_slider_takes_back_the_level() {
        let mut app = App::default();
        app.start_demo(0.0);
        app.pick(OrbState::Thinking, 1.0);
        assert!(app.demo.is_none());
        assert_eq!(app.orbit.state, OrbState::Thinking);
        app.voice = true;
        app.set_manual_level(0.8);
        assert!(!app.voice);
        app.advance(2.0);
        assert_eq!(app.orbit.level, 0.8);
        assert_eq!(app.shown_level, 0.8);
    }

    #[test]
    fn the_simulated_voice_drives_the_level_and_the_panel_reads_it_throttled() {
        let mut app = App { voice: true, ..App::default() };
        let mut pushes = 0;
        for i in 0..120 {
            if app.advance(i as f64 / 60.0) {
                pushes += 1;
            }
        }
        assert!(pushes > 3 && pushes <= 30, "about 14 a second over 2 s: {pushes}");
    }

    #[test]
    fn next_due_is_the_earliest_of_the_demo_and_the_hand_over() {
        let mut app = App::default();
        assert_eq!(app.next_due(), None);
        app.start_demo(5.0);
        assert_eq!(app.next_due(), Some(6.5), "the idle step lasts 1.5 s");
        app.orbit.set_state(OrbState::Wake, 5.2);
        let due = app.next_due().unwrap();
        assert!((due - 5.6).abs() < 1e-9, "the wake hand-over comes sooner than the next step: {due}");
    }
}

//! What each state does to the sphere, as a table of numbers that are blended when the state changes.
//! Every state is a partial override of the idle set; reduced motion freezes the movement and keeps
//! only the brightness, amber and arc differences.

use std::f32::consts::TAU;

use super::state::OrbState;

macro_rules! params {
    ($($f:ident),+ $(,)?) => {
        #[derive(Clone, Copy, Debug, PartialEq)]
        pub struct Params { $(pub $f: f32),+ }

        impl Params {
            pub fn lerp(&self, to: &Params, k: f32) -> Params {
                Params { $($f: self.$f + (to.$f - self.$f) * k),+ }
            }
        }
    };
}

params!(
    radius, noise, freq, rate, tw, rot, tilt, roll, bright, interior, rim, level_noise, level_radius, ripple, wave,
    scan, flow, swirl, loose, thick, amber, arc, flatten, breath,
);

/// The idle set: every state starts from this.
const D: Params = Params {
    radius: 1.0,
    noise: 0.075,
    freq: 1.7,
    rate: 1.0,
    tw: 1.0,
    rot: TAU / 30.0,
    tilt: 0.38,
    roll: 0.14,
    bright: 0.82,
    interior: 0.5,
    rim: 1.0,
    level_noise: 0.0,
    level_radius: 0.0,
    ripple: 0.0,
    wave: 0.0,
    scan: 0.0,
    flow: 0.0,
    swirl: 0.0,
    loose: 1.0,
    thick: 1.0,
    amber: 0.0,
    arc: 0.0,
    flatten: 0.0,
    breath: 1.0,
};

impl Params {
    #[cfg(test)]
    pub const IDLE: Params = D;

    /// The parameter set `state` settles to.
    pub fn for_state(state: OrbState, reduced: bool) -> Params {
        let t = match state {
            OrbState::Idle => D,
            OrbState::Wake => Params { radius: 1.05, bright: 1.0, noise: 0.07, rate: 1.4, rot: TAU / 20.0, ..D },
            OrbState::Listening => Params {
                radius: 1.07,
                bright: 1.0,
                noise: 0.045,
                level_noise: 0.15,
                ripple: 1.0,
                level_radius: 0.04,
                rate: 1.25,
                rot: TAU / 22.0,
                breath: 0.5,
                interior: 0.48,
                ..D
            },
            OrbState::Transcribing => Params {
                radius: 0.88,
                bright: 0.95,
                noise: 0.012,
                freq: 2.2,
                loose: 0.1,
                thick: 0.15,
                scan: 1.0,
                rate: 0.7,
                rot: TAU / 16.0,
                breath: 0.0,
                interior: 0.5,
                ..D
            },
            OrbState::Thinking => Params {
                radius: 0.97,
                bright: 0.92,
                noise: 0.045,
                rot: TAU / 7.0,
                tilt: 0.85,
                roll: 0.5,
                flow: 1.0,
                swirl: 1.0,
                rate: 1.5,
                breath: 0.3,
                ..D
            },
            OrbState::Confirming => Params {
                radius: 1.0,
                bright: 0.82,
                noise: 0.003,
                rate: 0.08,
                tw: 0.35,
                rot: TAU / 140.0,
                loose: 0.0,
                thick: 0.0,
                amber: 1.0,
                breath: 0.4,
                tilt: 0.3,
                ..D
            },
            OrbState::Speaking => Params {
                radius: 1.03,
                bright: 0.95,
                noise: 0.04,
                wave: 1.0,
                level_radius: 0.05,
                rate: 1.1,
                rot: TAU / 26.0,
                breath: 0.0,
                interior: 0.5,
                ..D
            },
            OrbState::FollowUp => Params { radius: 1.02, bright: 0.88, noise: 0.05, arc: 1.0, rot: TAU / 26.0, ..D },
            OrbState::Success => Params {
                radius: 1.02,
                bright: 1.0,
                noise: 0.0,
                loose: 0.0,
                thick: 0.0,
                rate: 0.4,
                rot: TAU / 24.0,
                breath: 0.0,
                ..D
            },
            OrbState::Error => Params { bright: 0.9, noise: 0.07, rate: 1.6, ..D },
            OrbState::Muted => Params {
                radius: 0.97,
                bright: 0.3,
                noise: 0.035,
                rate: 0.0,
                tw: 0.0,
                rot: 0.0,
                loose: 0.35,
                flatten: 1.0,
                breath: 0.0,
                rim: 0.7,
                ..D
            },
        };
        if !reduced {
            return t;
        }
        // Static sphere: movement parameters back to idle's, nothing animates, brightness stays.
        Params {
            radius: D.radius,
            noise: 0.05,
            freq: D.freq,
            rate: 0.0,
            tw: 0.0,
            rot: 0.0,
            tilt: D.tilt,
            roll: D.roll,
            level_noise: D.level_noise,
            level_radius: D.level_radius,
            ripple: D.ripple,
            wave: D.wave,
            scan: D.scan,
            flow: D.flow,
            swirl: D.swirl,
            loose: D.loose,
            thick: D.thick,
            flatten: D.flatten,
            breath: 0.0,
            ..t
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::orbit::state::ALL;

    #[test]
    fn idle_is_the_default_set() {
        assert_eq!(Params::for_state(OrbState::Idle, false), Params::IDLE);
    }

    #[test]
    fn muted_is_dim_and_still() {
        let m = Params::for_state(OrbState::Muted, false);
        assert_eq!((m.rate, m.rot, m.tw), (0.0, 0.0, 0.0));
        assert!(m.bright < 0.4);
    }

    #[test]
    fn reduced_motion_stops_all_movement_but_keeps_brightness() {
        for s in ALL {
            let r = Params::for_state(s, true);
            assert_eq!((r.rate, r.tw, r.rot, r.breath), (0.0, 0.0, 0.0, 0.0), "{s:?}");
            assert_eq!(r.noise, 0.05);
            assert_eq!(r.bright, Params::for_state(s, false).bright);
        }
        assert_eq!(Params::for_state(OrbState::Confirming, true).amber, 1.0);
        assert_eq!(Params::for_state(OrbState::FollowUp, true).arc, 1.0);
    }

    #[test]
    fn lerp_blends_every_field() {
        let a = Params::for_state(OrbState::Idle, false);
        let b = Params::for_state(OrbState::Thinking, false);
        assert_eq!(a.lerp(&b, 0.0), a);
        assert_eq!(a.lerp(&b, 1.0), b);
        let mid = a.lerp(&b, 0.5);
        assert!((mid.tilt - (a.tilt + b.tilt) / 2.0).abs() < 1e-6);
    }
}

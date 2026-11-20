//! One sphere's clock and blend. Each sphere on screen owns one of these: it follows the shared
//! [`Orbit`], blends its parameters when the state changes, keeps its own accumulated time and
//! rotation, and turns all of that into the numbers the shader reads for a frame ([`Frame`]).
//!
//! No GPU here, so every number can be checked in a test.

use std::f64::consts::{PI, TAU};

use super::controller::Orbit;
use super::ease::{MORPH, clamp01, smooth};
use super::params::Params;
use super::state::OrbState;

/// How a sphere is drawn, carried in its effect slot's data: point count, point size in points,
/// how much of the slot's shorter side the sphere spans, and how far the surface frays.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SphereConfig {
    pub count: usize,
    pub size: f32,
    pub scale: f32,
    pub fray: f32,
}

impl SphereConfig {
    pub fn to_data(self) -> [f32; 4] {
        [self.count as f32, self.size, self.scale, self.fray]
    }

    pub fn from_data(d: [f32; 4]) -> SphereConfig {
        SphereConfig {
            count: if d[0].is_finite() { d[0].round().clamp(200.0, 20_000.0) as usize } else { 5000 },
            size: if d[1].is_finite() { d[1].max(0.1) } else { 1.4 },
            scale: if d[2].is_finite() { d[2].max(0.05) } else { 0.62 },
            fray: if d[3].is_finite() { d[3].clamp(0.0, 3.0) } else { 1.0 },
        }
    }
}

/// Everything the shader needs for one frame of one sphere.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Frame {
    /// Rotation matrix columns.
    pub rot: [[f32; 3]; 3],
    /// Scale from the unit sphere to the slot's normalised coordinates.
    pub view: [f32; 2],
    pub radius: f32,
    /// Point size in device pixels.
    pub px: f32,
    pub t: f32,
    pub tw: f32,
    pub flow_t: f32,
    pub clock: f32,
    pub noise: f32,
    pub freq: f32,
    pub ripple: f32,
    pub wave: f32,
    pub scan: f32,
    pub scan_pos: f32,
    pub swirl: f32,
    pub flow: f32,
    pub loose: f32,
    pub thick: f32,
    pub jitter: f32,
    pub burst: f32,
    pub flatten: f32,
    pub sweep: f32,
    pub sweep_pos: f32,
    pub arc: f32,
    pub arc_prog: f32,
    pub amber: f32,
    pub pulse: f32,
    pub red: f32,
    pub bright: f32,
    pub rim: f32,
    pub interior: f32,
    /// True while this sphere still needs frames: it moves on its own, or a blend or flash is running.
    pub busy: bool,
}

pub struct Sphere {
    epoch: u32,
    p: Params,
    from: Params,
    to: Params,
    t0: f64,
    dur: f64,
    mt: f64,
    twt: f64,
    ft: f64,
    ang: f64,
    lv: f32,
    last: Option<f64>,
}

impl Sphere {
    /// A sphere that starts settled in the orbit's current state.
    pub fn new(o: &Orbit) -> Sphere {
        let p = Params::for_state(o.state, o.reduced());
        Sphere { epoch: o.epoch, p, from: p, to: p, t0: 0.0, dur: 0.0, mt: 37.0, twt: 37.0, ft: 0.0, ang: 1.2, lv: 0.0, last: None }
    }

    fn retarget(&mut self, o: &Orbit) {
        self.from = self.p;
        self.to = Params::for_state(o.state, o.reduced());
        self.t0 = o.changed_at;
        self.dur = if o.state == OrbState::Wake { 0.25 } else { 0.45 };
        self.epoch = o.epoch;
    }

    /// Advances to clock time `t` and returns the frame to draw. `slot` is the slot's size in
    /// device pixels, `dpr` the device pixels per point.
    pub fn step(&mut self, o: &Orbit, t: f64, slot: (f32, f32), dpr: f32, cfg: &SphereConfig) -> Frame {
        if o.epoch != self.epoch {
            self.retarget(o);
        }
        let dt = (t - self.last.unwrap_or(t)).clamp(0.0, 0.05);
        self.last = Some(t);

        let k = if self.dur > 0.0 { MORPH.at(((t - self.t0) / self.dur) as f32) } else { 1.0 };
        self.p = self.from.lerp(&self.to, k);
        let p = self.p;

        let r = o.reduced();
        let fray = cfg.fray.clamp(0.0, 3.0);
        let target_level = o.level;
        let rate = if target_level > self.lv { 22.0 } else { 8.0 };
        self.lv += (target_level - self.lv) * (1.0 - (-(dt as f32) * rate).exp());
        let lv = if r { 0.0 } else { self.lv };

        self.mt += dt * p.rate as f64;
        self.twt += dt * p.tw as f64;
        self.ft += dt * p.flow as f64 * 0.8;
        self.ang += dt * p.rot as f64;

        let (ew, es, ee, ef) = (t - o.fx.wake, t - o.fx.success, t - o.fx.error, t - o.fx.follow);
        let burst = (if (0.0..1.0).contains(&ew) {
            if ew < 0.1 { 1.0 - (1.0 - ew / 0.1).powi(3) } else { (-(ew - 0.1) * 11.0).exp() * ((ew - 0.1) * 13.0).cos() }
        } else {
            0.0
        }) as f32;
        let spike = if (0.0..2.0).contains(&ew) {
            let decay = ((ew - 0.06).max(0.0) * 5.0) as f32;
            smooth(0.0, 0.06, ew as f32) * (-decay).exp()
        } else {
            0.0
        };
        let jitter = if (0.0..0.7).contains(&ee) {
            if ee < 0.05 { (ee / 0.05) as f32 } else { 1.0 - smooth(0.25, 0.62, ee as f32) }
        } else {
            0.0
        };
        let red = if (0.0..1.0).contains(&ee) { smooth(0.0, 0.05, ee as f32) * (1.0 - smooth(0.42, 0.8, ee as f32)) } else { 0.0 };
        let sweep_env = if (0.0..0.75).contains(&es) { (PI * clamp01((es / 0.75) as f32) as f64).sin() as f32 } else { 0.0 };
        let sweep_pos = -1.9 + 3.8 * MORPH.at(clamp01((es / 0.7) as f32));
        let arc_prog = if r { 1.0 } else { clamp01((1.0 - ef / o.follow_window.max(0.1)) as f32) };

        let radius = p.radius * (1.0 + 0.02 * p.breath * (TAU * t / 4.0).sin() as f32) * (1.0 + p.level_radius * lv);

        let (w, h) = (slot.0.max(1.0), slot.1.max(1.0));
        let m = w.min(h);
        let (cy, sy) = (self.ang.cos() as f32, self.ang.sin() as f32);
        let (cx, sx) = (p.tilt.cos(), p.tilt.sin());
        let (cz, sz) = (p.roll.cos(), p.roll.sin());
        let rot_m = mul3(mul3([cz, -sz, 0.0, sz, cz, 0.0, 0.0, 0.0, 1.0], [1.0, 0.0, 0.0, 0.0, cx, -sx, 0.0, sx, cx]), [cy, 0.0, sy, 0.0, 1.0, 0.0, -sy, 0.0, cy]);

        let flash = (r && (es < 0.8 || ew < 2.0)) || k < 1.0;
        Frame {
            rot: [[rot_m[0], rot_m[3], rot_m[6]], [rot_m[1], rot_m[4], rot_m[7]], [rot_m[2], rot_m[5], rot_m[8]]],
            view: [m / w * cfg.scale, m / h * cfg.scale],
            radius,
            px: cfg.size * dpr,
            t: self.mt as f32,
            tw: self.twt as f32,
            flow_t: self.ft as f32,
            clock: (t % 100.0) as f32,
            noise: (p.noise + p.level_noise * lv) * fray,
            freq: p.freq,
            ripple: p.ripple * (0.12 + 0.88 * lv),
            wave: p.wave * (0.1 + 0.9 * lv),
            scan: p.scan,
            scan_pos: ((t * 2.3).sin() * 0.95) as f32,
            swirl: p.swirl,
            flow: p.flow,
            loose: p.loose * fray.min(1.2),
            thick: p.thick * fray,
            jitter: if r { 0.0 } else { jitter },
            burst: if r { 0.0 } else { burst },
            flatten: p.flatten,
            sweep: if r { 0.0 } else { sweep_env },
            sweep_pos,
            arc: p.arc,
            arc_prog,
            amber: p.amber,
            pulse: if r { 0.5 } else { 0.5 + 0.5 * (TAU * t / 1.6).sin() as f32 },
            red,
            bright: p.bright * (1.0 + 0.9 * spike) * if r { 1.0 + 0.3 * sweep_env } else { 1.0 },
            rim: p.rim,
            interior: p.interior,
            busy: !r || flash,
        }
    }
}

fn mul3(a: [f32; 9], b: [f32; 9]) -> [f32; 9] {
    let mut c = [0.0; 9];
    for r in 0..3 {
        for col in 0..3 {
            c[r * 3 + col] = a[r * 3] * b[col] + a[r * 3 + 1] * b[3 + col] + a[r * 3 + 2] * b[6 + col];
        }
    }
    c
}

#[cfg(test)]
mod tests {
    use super::*;

    const CFG: SphereConfig = SphereConfig { count: 5000, size: 1.4, scale: 0.6, fray: 1.0 };

    fn step(s: &mut Sphere, o: &Orbit, t: f64) -> Frame {
        s.step(o, t, (800.0, 600.0), 1.0, &CFG)
    }

    #[test]
    fn a_new_sphere_starts_settled_in_idle() {
        let o = Orbit::new();
        let mut s = Sphere::new(&o);
        let f = step(&mut s, &o, 0.0);
        assert_eq!(f.radius, 1.0);
        assert_eq!(f.bright, Params::IDLE.bright);
        assert!(f.busy);
        assert_eq!(f.view[0], 600.0 / 800.0 * 0.6);
        assert_eq!(f.view[1], 0.6);
    }

    #[test]
    fn a_state_change_blends_over_forty_five_hundredths_of_a_second() {
        let mut o = Orbit::new();
        let mut s = Sphere::new(&o);
        step(&mut s, &o, 0.0);
        o.set_state(OrbState::Thinking, 1.0);
        let mid = step(&mut s, &o, 1.225);
        let idle_tilt = Params::IDLE.tilt;
        assert!(mid.flow > 0.4 && mid.flow < 0.6, "halfway through the blend: {}", mid.flow);
        assert!(idle_tilt < 0.85);
        let done = step(&mut s, &o, 1.5);
        assert_eq!(done.flow, 1.0);
        assert_eq!(done.swirl, 1.0);
    }

    #[test]
    fn wake_blends_faster_and_bursts_out() {
        let mut o = Orbit::new();
        let mut s = Sphere::new(&o);
        step(&mut s, &o, 0.0);
        o.set_state(OrbState::Wake, 2.0);
        let f = step(&mut s, &o, 2.1);
        assert!((f.burst - 1.0).abs() < 1e-3, "full burst at 0.1 s: {}", f.burst);
        assert!(f.bright > Params::IDLE.bright * 1.5, "brightness spike: {}", f.bright);
        let later = step(&mut s, &o, 3.5);
        assert!(later.burst.abs() < 0.01 && later.jitter == 0.0);
    }

    #[test]
    fn error_flashes_red_then_reforms() {
        let mut o = Orbit::new();
        let mut s = Sphere::new(&o);
        step(&mut s, &o, 0.0);
        o.set_state(OrbState::Error, 1.0);
        let f = step(&mut s, &o, 1.1);
        assert!(f.jitter > 0.9 && f.red > 0.9, "{} {}", f.jitter, f.red);
        let g = step(&mut s, &o, 1.85);
        assert!(g.jitter < 0.01 && g.red < 0.01);
    }

    #[test]
    fn success_sweeps_a_band_across_the_sphere() {
        let mut o = Orbit::new();
        let mut s = Sphere::new(&o);
        step(&mut s, &o, 0.0);
        o.set_state(OrbState::Success, 1.0);
        let start = step(&mut s, &o, 1.0);
        let mid = step(&mut s, &o, 1.375);
        let end = step(&mut s, &o, 1.7);
        assert!(start.sweep_pos < -1.8 && end.sweep_pos > 1.8);
        assert!((mid.sweep - 1.0).abs() < 0.01, "peaks mid-sweep: {}", mid.sweep);
    }

    #[test]
    fn follow_up_drains_its_arc_over_two_seconds() {
        let mut o = Orbit::new();
        let mut s = Sphere::new(&o);
        step(&mut s, &o, 0.0);
        o.set_state(OrbState::FollowUp, 1.0);
        assert_eq!(step(&mut s, &o, 1.0).arc_prog, 1.0);
        assert!((step(&mut s, &o, 2.0).arc_prog - 0.5).abs() < 0.01);
        assert_eq!(step(&mut s, &o, 3.1).arc_prog, 0.0);
    }

    #[test]
    fn the_follow_up_arc_drains_over_the_window_orbit_sends() {
        let mut o = Orbit::new();
        o.follow_window = 8.0;
        let mut s = Sphere::new(&o);
        step(&mut s, &o, 0.0);
        o.set_state(OrbState::FollowUp, 1.0);
        assert!((step(&mut s, &o, 5.0).arc_prog - 0.5).abs() < 0.01, "half the 8 s window has passed");
        assert_eq!(step(&mut s, &o, 9.5).arc_prog, 0.0);
    }

    #[test]
    fn reduced_motion_holds_still_and_goes_idle_for_the_gpu() {
        let mut o = Orbit::new();
        o.set_reduced(true, 0.0);
        let mut s = Sphere::new(&o);
        let a = step(&mut s, &o, 1.0);
        let b = step(&mut s, &o, 1.04);
        assert_eq!(a.rot, b.rot, "no rotation");
        assert_eq!(a.t, b.t, "no surface drift");
        assert_eq!(a.radius, b.radius, "no breathing");
        assert!(!b.busy, "nothing left to animate");
        assert_eq!((b.jitter, b.burst, b.sweep), (0.0, 0.0, 0.0));
    }

    #[test]
    fn the_clock_cannot_jump_the_motion() {
        let o = Orbit::new();
        let mut s = Sphere::new(&o);
        let a = step(&mut s, &o, 0.0);
        let b = step(&mut s, &o, 600.0);
        assert!((b.t - a.t).abs() <= 0.05 * 1.01, "a long stall advances at most 50 ms: {}", b.t - a.t);
    }

    #[test]
    fn the_level_is_smoothed_and_only_matters_to_the_states_that_use_it() {
        let mut o = Orbit::new();
        o.set_state(OrbState::Listening, 0.0);
        let mut s = Sphere::new(&o);
        let quiet = step(&mut s, &o, 0.0);
        o.set_level(1.0);
        let one = step(&mut s, &o, 0.016);
        let many = (0..30).map(|i| step(&mut s, &o, 0.032 + i as f64 * 0.016)).last().unwrap();
        assert!(one.noise > quiet.noise && one.noise < many.noise, "rises over frames");
        assert!(many.ripple > 0.95);
        let mut idle = Orbit::new();
        idle.set_level(1.0);
        let mut si = Sphere::new(&idle);
        step(&mut si, &idle, 0.0);
        let f = (0..30).map(|i| step(&mut si, &idle, i as f64 * 0.016)).last().unwrap();
        assert!((f.noise - Params::IDLE.noise).abs() < 1e-6, "idle ignores the level");
    }

    #[test]
    fn the_rotation_stays_a_rotation() {
        let mut o = Orbit::new();
        o.set_state(OrbState::Thinking, 0.0);
        let mut s = Sphere::new(&o);
        let f = (0..60).map(|i| step(&mut s, &o, i as f64 * 0.016)).last().unwrap();
        let c = f.rot;
        let dot = |a: [f32; 3], b: [f32; 3]| a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
        for i in 0..3 {
            assert!((dot(c[i], c[i]) - 1.0).abs() < 1e-5);
            for j in (i + 1)..3 {
                assert!(dot(c[i], c[j]).abs() < 1e-5);
            }
        }
    }

    #[test]
    fn config_survives_the_trip_through_slot_data() {
        assert_eq!(SphereConfig::from_data(CFG.to_data()), CFG);
        let bad = SphereConfig::from_data([f32::NAN, -1.0, 0.0, 99.0]);
        assert_eq!(bad.count, 5000);
        assert!(bad.size > 0.0 && bad.scale > 0.0 && bad.fray == 3.0);
    }
}

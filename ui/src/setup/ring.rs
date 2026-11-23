//! Orbit's mark, the dashed ring with its satellite dot, drawn as vectors so it can move.
//!
//! The logo's numbers are the app tile's (`assets/logo/make_icons.py`, TILE): on a 155-unit square, a ring of radius 39 drawn 8
//! wide in 24 dashes with equal gaps, and a satellite dot of radius 9 at (33, -29) from the centre, up and to the right.
//! The installer's big ring (`Geometry::STAGE`) is the same mark drawn wider and lighter, so it sits around the particle sphere.
//!
//! The installer animates it: the satellite orbits while it waits, the dashes light up one by one as files are copied with the
//! satellite riding the lit edge, and when the install is done the satellite settles where the logo has it.

use std::f32::consts::{PI, TAU};

use rune::shape::Shape;
use rune::{Color, El, canvas};

/// The shapes' design square.
const BOX: f32 = 155.0;
pub const DASHES: usize = 24;
/// The logo's satellite, from the centre.
const DOT_AT: (f32, f32) = (33.0, -29.0);

/// The ring's measurements, in design units on the 155-unit square.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Geometry {
    pub radius: f32,
    pub stroke: f32,
    pub dot: f32,
}

impl Geometry {
    /// The logo exactly (the title bar's mark).
    pub const LOGO: Geometry = Geometry { radius: 39.0, stroke: 8.0, dot: 9.0 };
    /// The stage's ring: room for the sphere inside, lighter dashes.
    pub const STAGE: Geometry = Geometry { radius: 62.0, stroke: 4.2, dot: 5.8 };

    /// The satellite's distance from the centre: as far outside the ring as the logo's is (its centre a little past the rim).
    fn dot_distance(self) -> f32 {
        self.radius * DOT_AT.0.hypot(DOT_AT.1) / Geometry::LOGO.radius
    }
}

/// Where the logo has its satellite: radians clockwise from three o'clock (y is down).
pub fn logo_angle() -> f32 {
    DOT_AT.1.atan2(DOT_AT.0)
}

/// How the ring looks at one moment.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Look {
    /// 0 to 1: how many dashes are lit, clockwise from twelve o'clock. 1 = the plain logo.
    pub lit: f32,
    /// Where the satellite is (radians, as `logo_angle`).
    pub satellite: f32,
    /// Fading copies of the satellite behind it, while it moves (0 = none).
    pub trail: f32,
    pub on: Color,
    pub off: Color,
}

impl Look {
    /// The logo, still.
    pub fn logo(color: Color) -> Look {
        Look { lit: 1.0, satellite: logo_angle(), trail: 0.0, on: color, off: color }
    }
}

fn circle(cx: f32, cy: f32, r: f32) -> Vec<[f32; 2]> {
    (0..40).map(|i| {
        let a = i as f32 / 40.0 * TAU;
        [cx + r * a.cos(), cy + r * a.sin()]
    }).collect()
}

/// A dash: the band of the ring between two angles, as one filled outline (solid, no seams).
fn dash(c: f32, g: Geometry, start: f32, end: f32) -> Vec<[f32; 2]> {
    let (outer, inner) = (g.radius + g.stroke / 2.0, g.radius - g.stroke / 2.0);
    let at = |r: f32, a: f32| [c + r * a.cos(), c + r * a.sin()];
    let steps = 6;
    let mut points: Vec<[f32; 2]> = (0..=steps).map(|j| at(outer, start + (end - start) * j as f32 / steps as f32)).collect();
    points.extend((0..=steps).rev().map(|j| at(inner, start + (end - start) * j as f32 / steps as f32)));
    points
}

/// The mark as a shape (fills only, so it scales with the element).
pub fn shape(g: Geometry, look: Look) -> Shape {
    let c = BOX / 2.0;
    let step = TAU / DASHES as f32;
    let mut s = Shape::new().view_box(BOX, BOX);
    for i in 0..DASHES {
        // Dash i starts at twelve o'clock and runs clockwise; it takes half its step, the other half is the gap.
        let start = -PI / 2.0 + i as f32 * step + step * 0.25;
        let head = look.lit * DASHES as f32 - i as f32;          // >= 1 lit, <= 0 dark, between: the dash being lit now
        let color = if head >= 1.0 { look.on } else if head <= 0.0 { look.off } else { look.off.mix(look.on, head) };
        s = s.fill(dash(c, g, start, start + step * 0.5), color);
    }
    let d = g.dot_distance();
    if look.trail > 0.0 {
        for n in (1..=3).rev() {
            let a = look.satellite - n as f32 * 0.09 * look.trail;
            let fade = look.on.alpha(look.on.a * look.trail * 0.32 / n as f32);
            s = s.fill(circle(c + d * a.cos(), c + d * a.sin(), g.dot * (1.0 - 0.2 * n as f32)), fade);
        }
    }
    s.fill(circle(c + d * look.satellite.cos(), c + d * look.satellite.sin(), g.dot), look.on)
}

/// The mark as an element, `size` points square.
pub fn mark(size: f32, g: Geometry, look: Look) -> El {
    canvas(shape(g, look)).size(size, size)
}

/// The logo, still, `size` points square.
pub fn logo(size: f32, color: Color) -> El {
    mark(size, Geometry::LOGO, Look::logo(color))
}

/// The satellite's angle `t` seconds into its slow orbit (one turn in 9 s), starting at the logo's place.
pub fn orbiting(t: f64) -> f32 {
    logo_angle() + ((t / 9.0).fract() as f32) * TAU
}

/// The angle of the lit edge for `lit` (0 to 1), clockwise from twelve o'clock.
pub fn edge(lit: f32) -> f32 {
    -PI / 2.0 + lit.clamp(0.0, 1.0) * TAU
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_logo_has_its_satellite_up_and_to_the_right() {
        let a = logo_angle();
        assert!(a < 0.0 && a > -PI / 2.0, "{a}");                    // y is down: above and right of the centre
        assert!((Geometry::LOGO.dot_distance() - 43.9).abs() < 0.1);
    }

    #[test]
    fn the_stage_ring_fits_its_square_with_the_satellite_on_it() {
        let g = Geometry::STAGE;
        assert!(g.dot_distance() + g.dot < BOX / 2.0);
        assert!(g.dot_distance() - g.dot < g.radius + g.stroke / 2.0);           // the satellite still touches the ring
    }

    #[test]
    fn every_dash_and_the_satellite_are_drawn() {
        assert_eq!(shape(Geometry::LOGO, Look::logo(Color::WHITE)).ops.len(), DASHES + 1);
        let moving = Look { trail: 1.0, ..Look::logo(Color::WHITE) };
        assert_eq!(shape(Geometry::STAGE, moving).ops.len(), DASHES + 4);
    }

    #[test]
    fn the_edge_goes_once_round_from_twelve_o_clock() {
        assert!((edge(0.0) + PI / 2.0).abs() < 1e-6);
        assert!((edge(1.0) - edge(0.0) - TAU).abs() < 1e-5);
        assert!((orbiting(0.0) - logo_angle()).abs() < 1e-6);
    }
}

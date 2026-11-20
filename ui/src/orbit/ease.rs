//! CSS `cubic-bezier(x1, y1, x2, y2)`, solved by Newton's method as the web version does.

#[derive(Clone, Copy, Debug)]
pub struct CubicBezier {
    ax: f32,
    bx: f32,
    cx: f32,
    ay: f32,
    by: f32,
    cy: f32,
}

impl CubicBezier {
    pub const fn new(x1: f32, y1: f32, x2: f32, y2: f32) -> CubicBezier {
        let cx = 3.0 * x1;
        let bx = 3.0 * (x2 - x1) - cx;
        let cy = 3.0 * y1;
        let by = 3.0 * (y2 - y1) - cy;
        CubicBezier { ax: 1.0 - cx - bx, bx, cx, ay: 1.0 - cy - by, by, cy }
    }

    fn x(&self, t: f32) -> f32 {
        ((self.ax * t + self.bx) * t + self.cx) * t
    }
    fn y(&self, t: f32) -> f32 {
        ((self.ay * t + self.by) * t + self.cy) * t
    }
    fn dx(&self, t: f32) -> f32 {
        (3.0 * self.ax * t + 2.0 * self.bx) * t + self.cx
    }

    pub fn at(&self, x: f32) -> f32 {
        if x <= 0.0 {
            return 0.0;
        }
        if x >= 1.0 {
            return 1.0;
        }
        let mut t = x;
        for _ in 0..8 {
            let e = self.x(t) - x;
            let d = self.dx(t);
            if e.abs() < 1e-5 || d.abs() < 1e-6 {
                break;
            }
            t = (t - e / d).clamp(0.0, 1.0);
        }
        self.y(t)
    }
}

/// The morph curve every state change and the success sweep use.
pub const MORPH: CubicBezier = CubicBezier::new(0.65, 0.0, 0.35, 1.0);

pub fn clamp01(v: f32) -> f32 {
    v.clamp(0.0, 1.0)
}

pub fn smooth(a: f32, b: f32, x: f32) -> f32 {
    let t = clamp01((x - a) / (b - a));
    t * t * (3.0 - 2.0 * t)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_morph_curve_runs_from_zero_to_one_and_is_symmetric() {
        assert_eq!(MORPH.at(0.0), 0.0);
        assert_eq!(MORPH.at(1.0), 1.0);
        assert!((MORPH.at(0.5) - 0.5).abs() < 1e-3);
        let (a, b) = (MORPH.at(0.25), MORPH.at(0.75));
        assert!((a + b - 1.0).abs() < 1e-3, "{a} {b}");
        assert!(a < 0.25, "eases in");
    }

    #[test]
    fn smoothstep_clamps() {
        assert_eq!(smooth(0.0, 1.0, -1.0), 0.0);
        assert_eq!(smooth(0.0, 1.0, 2.0), 1.0);
        assert!((smooth(0.0, 1.0, 0.5) - 0.5).abs() < 1e-6);
    }
}

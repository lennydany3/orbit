//! The points of the sphere: a Fibonacci spiral of unit directions, each with four fixed random
//! numbers the shader uses to give every point its own size, flicker and drift. Seeded, so the
//! sphere is the same sphere on every run.

use std::f64::consts::PI;

/// mulberry32: the generator the web version seeds with 1337.
struct Rng(u32);

impl Rng {
    fn next(&mut self) -> f32 {
        self.0 = self.0.wrapping_add(0x6D2B_79F5);
        let s = self.0;
        let mut t = (s ^ (s >> 15)).wrapping_mul(1 | s);
        t = t.wrapping_add((t ^ (t >> 7)).wrapping_mul(61 | t)) ^ t;
        ((t ^ (t >> 14)) as f64 / 4_294_967_296.0) as f32
    }
}

/// One point per instance: `dir` is its unit direction, `rnd` four numbers in 0..1.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Point {
    pub dir: [f32; 3],
    pub rnd: [f32; 4],
}

pub fn sphere_points(count: usize) -> Vec<Point> {
    let n = count.clamp(200, 20_000);
    let mut rng = Rng(1337);
    let golden = PI * (3.0 - 5f64.sqrt());
    (0..n)
        .map(|i| {
            let y = 1.0 - (i as f64 + 0.5) / n as f64 * 2.0;
            let r = (1.0 - y * y).sqrt();
            let th = golden * i as f64;
            let dir = [(th.cos() * r) as f32, y as f32, (th.sin() * r) as f32];
            let rnd = [rng.next(), rng.next(), rng.next(), rng.next()];
            Point { dir, rnd }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn points_are_unit_directions_spread_over_the_whole_sphere() {
        let pts = sphere_points(2000);
        assert_eq!(pts.len(), 2000);
        let mut sum = [0.0f64; 3];
        for p in &pts {
            let len = p.dir.iter().map(|v| (*v as f64).powi(2)).sum::<f64>().sqrt();
            assert!((len - 1.0).abs() < 1e-5);
            for (s, d) in sum.iter_mut().zip(p.dir) {
                *s += d as f64;
            }
            assert!(p.rnd.iter().all(|r| (0.0..1.0).contains(r)));
        }
        // Evenly spread: the centroid of the points sits at the centre.
        assert!(sum.iter().all(|s| (s / 2000.0).abs() < 0.01), "{sum:?}");
    }

    #[test]
    fn the_same_count_gives_the_same_sphere_and_the_count_is_bounded() {
        assert_eq!(sphere_points(500), sphere_points(500));
        assert_eq!(sphere_points(1).len(), 200);
        assert_eq!(sphere_points(1_000_000).len(), 20_000);
    }

    #[test]
    fn the_generator_matches_the_web_versions_values() {
        // The first four outputs of the web version's generator (mulberry32, seed 1337), from running it.
        let mut r = Rng(1337);
        let got: Vec<f32> = (0..4).map(|_| r.next()).collect();
        let want = [0.184_412, 0.189_989, 0.810_472, 0.643_749];
        for (g, w) in got.iter().zip(want) {
            assert!((g - w).abs() < 1e-5, "{got:?}");
        }
        // And they are the first point's four numbers.
        assert_eq!(sphere_points(500)[0].rnd.to_vec(), got);
    }
}

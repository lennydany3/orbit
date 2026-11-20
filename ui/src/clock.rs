//! One clock for every window.
//!
//! Each Rune window keeps its own clock, starting when the window is made, so with a hidden hub, an overlay and a panel that
//! come and go, "the time" differs from window to window. Everything here (the sphere's one-shot flashes, the pacer, the
//! captions) is stamped with one real clock instead: seconds since the app started. A headless snapshot is the exception: it
//! uses Rune's virtual clock, so a picture at `--at 2` is the same picture every time.

use std::sync::OnceLock;
use std::time::Instant;

static START: OnceLock<Instant> = OnceLock::new();
static HEADLESS: OnceLock<bool> = OnceLock::new();

/// Starts the clock. Call first thing in `main`; the first call to [`now`] would do it too.
pub fn start() {
    START.get_or_init(Instant::now);
}

/// Seconds. `rune_t` is the window's own clock, used only when running headless.
pub fn now(rune_t: f64) -> f64 {
    if *HEADLESS.get_or_init(rune::is_headless) {
        rune_t
    } else {
        START.get_or_init(Instant::now).elapsed().as_secs_f64()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn live_time_is_one_real_clock_that_ignores_the_windows_own() {
        start();
        let (a, b) = (now(1000.0), now(0.0));
        assert!(a < 100.0 && b >= a, "seconds since start, whatever the window says: {a} {b}");
    }
}

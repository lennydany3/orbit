//! Notices when the other side changes a data file. Orbit (Python) adds reminders and notes when you ask by voice, names a playlist,
//! and moves a reminder along when it fires; a background thread looks at each file's size and time about once a second, sets a bit
//! for the ones that moved and wakes the window, which reads them again. Nothing is read on the UI thread unless something changed.

use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;

use rune::Waker;

use crate::userdata;

pub const REMINDERS: u32 = 1;
pub const NOTES: u32 = 2;
pub const MUSIC: u32 = 4;

/// How often the files are looked at.
pub const EVERY: Duration = Duration::from_millis(900);

#[derive(Clone, Default)]
pub struct Watcher {
    changed: Arc<AtomicU32>,
}

impl Watcher {
    /// Starts looking at `files` (a bit and a path each). The thread ends with the program.
    pub fn start(files: Vec<(u32, PathBuf)>, wake: Waker, every: Duration) -> Watcher {
        let watcher = Watcher::default();
        let flags = watcher.changed.clone();
        std::thread::Builder::new()
            .name("data-watcher".into())
            .spawn(move || {
                let mut last: Vec<_> = files.iter().map(|(_, path)| userdata::stamp(path)).collect();
                loop {
                    std::thread::sleep(every);
                    let mut moved = false;
                    for ((bit, path), before) in files.iter().zip(last.iter_mut()) {
                        let now = userdata::stamp(path);
                        if now != *before {
                            *before = now;
                            flags.fetch_or(*bit, Ordering::SeqCst);
                            moved = true;
                        }
                    }
                    if moved {
                        wake();
                    }
                }
            })
            .ok();
        watcher
    }

    /// Which files have changed since the last call (the bits), and forgets them.
    pub fn take(&self) -> u32 {
        self.changed.swap(0, Ordering::SeqCst)
    }

    /// Says a file changed (for tests).
    pub fn mark(&self, bits: u32) {
        self.changed.fetch_or(bits, Ordering::SeqCst);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::tests::tempdir;
    use std::sync::atomic::AtomicUsize;

    #[test]
    fn a_file_that_changes_is_reported_once_and_the_window_is_woken() {
        let dir = tempdir("watcher");
        let (a, b) = (dir.join("a.json"), dir.join("b.json"));
        std::fs::write(&a, "{}").unwrap();
        let woken = Arc::new(AtomicUsize::new(0));
        let counter = woken.clone();
        let watcher = Watcher::start(vec![(REMINDERS, a.clone()), (NOTES, b.clone())], Arc::new(move || { counter.fetch_add(1, Ordering::SeqCst); }), Duration::from_millis(20));
        std::thread::sleep(Duration::from_millis(120));
        assert_eq!((watcher.take(), woken.load(Ordering::SeqCst)), (0, 0), "nothing moved: nothing reported");
        std::fs::write(&b, "{\"new\": true}").unwrap();
        let mut got = 0;
        for _ in 0..100 {
            got |= watcher.take();
            if got != 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(got, NOTES, "only the file that moved");
        assert!(woken.load(Ordering::SeqCst) >= 1);
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(watcher.take(), 0, "and it is not reported again");
    }

    #[test]
    fn marking_sets_bits_that_take_clears() {
        let w = Watcher::default();
        w.mark(REMINDERS | MUSIC);
        assert_eq!((w.take(), w.take()), (REMINDERS | MUSIC, 0));
    }
}

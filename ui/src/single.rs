//! One Orbit at a time. Starting Orbit a second time (the Start menu, the desktop icon, or Windows starting it at sign-in while it
//! already runs) must not give two microphones, two trays and two engines; it should just bring the running one's window up.
//!
//! The first Orbit listens on a fixed port on this PC only. A second one connects, and if what answers says it is Orbit it asks
//! it to open its window and ends. If something else answers, or nothing does, the port is not Orbit's and this one carries on.
//! (A program run from the source folder uses another port, so working on Orbit never fights with the installed one.)

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::time::Duration;

const INSTALLED_PORT: u16 = 47_811;
const SOURCE_PORT: u16 = 47_812;
const GREETING: &[u8] = b"orbit-ui\n";

pub enum Claim {
    /// Nobody else is running: this one is the Orbit. Call `serve` to hear when another is started.
    First(Guard),
    /// Another Orbit is running and has been asked to show its window.
    Second,
    /// The port could not be used (so no guard): carry on without one.
    Unavailable,
}

pub struct Guard {
    listener: TcpListener,
}

impl Guard {
    /// Calls `on_open` each time another Orbit is started, from a thread of its own.
    pub fn serve(self, on_open: impl Fn() + Send + 'static) {
        let _ = std::thread::Builder::new().name("single-instance".into()).spawn(move || {
            for stream in self.listener.incoming().flatten() {
                let mut stream = stream;
                if stream.write_all(GREETING).is_ok() {
                    on_open();
                }
            }
        });
    }
}

pub fn port() -> u16 {
    if crate::spawn::is_installed() { INSTALLED_PORT } else { SOURCE_PORT }
}

pub fn claim() -> Claim {
    claim_on(port())
}

pub fn claim_on(port: u16) -> Claim {
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    match TcpListener::bind(addr) {
        Ok(listener) => Claim::First(Guard { listener }),
        Err(_) => match ask_running(addr) {
            true => Claim::Second,
            false => Claim::Unavailable,
        },
    }
}

/// Is an Orbit answering on `addr`? (It has just been told to open its window.)
fn ask_running(addr: SocketAddr) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(600)) else { return false };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(600)));
    let mut said = [0u8; 9];
    stream.read_exact(&mut said).is_ok() && said == *GREETING
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn the_second_start_finds_the_first_and_wakes_it() {
        let port = crate::spawn::free_port().unwrap();
        let Claim::First(guard) = claim_on(port) else { panic!("the first start should win") };
        let opened = Arc::new(AtomicUsize::new(0));
        let counter = opened.clone();
        guard.serve(move || {
            counter.fetch_add(1, Ordering::SeqCst);
        });
        assert!(matches!(claim_on(port), Claim::Second));
        for _ in 0..50 {
            if opened.load(Ordering::SeqCst) > 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(opened.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn something_that_is_not_orbit_on_the_port_does_not_stop_this_one() {
        let stranger = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = stranger.local_addr().unwrap().port();
        std::thread::spawn(move || {
            for stream in stranger.incoming().flatten() {
                let mut stream = stream;
                let _ = stream.write_all(b"HTTP/1.1 200 OK\r\n\r\n");
            }
        });
        assert!(matches!(claim_on(port), Claim::Unavailable));
    }

    #[test]
    fn a_port_nobody_answers_on_is_a_first_start() {
        let port = crate::spawn::free_port().unwrap();
        assert!(matches!(claim_on(port), Claim::First(_)));
    }
}

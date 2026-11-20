//! The window's end of the link: a background thread that connects to Orbit, reads what it sends and writes the one answer
//! the window may give. It reconnects by itself (so starting the window before Orbit, or restarting Orbit, just works),
//! and wakes the UI whenever there is something to read.
//!
//! Everything blocking happens here, never on the UI thread.

pub mod endpoint;
pub mod message;

use std::io;
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::time::{Duration, Instant};

use rune::Waker;
use tungstenite::{Message, WebSocket};

use endpoint::Endpoint;

/// The same five states the design's page shows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Off,
    Connecting,
    Open,
    /// Lost, or refused: trying again.
    Closed,
    /// The address itself is unusable (so there is nothing to retry).
    Error,
}

impl Status {
    pub fn label(self) -> &'static str {
        match self {
            Status::Off => "Not connected",
            Status::Connecting => "Connecting\u{2026}",
            Status::Open => "Connected",
            Status::Closed => "Retrying\u{2026}",
            Status::Error => "Could not connect",
        }
    }

    /// Is a link active, or about to be: connecting, open, or between retries?
    pub fn is_linked(self) -> bool {
        matches!(self, Status::Connecting | Status::Open | Status::Closed)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    Status(Status),
    /// One text frame, as Orbit sent it (see [`message::parse`]).
    Message(String),
}

enum Command {
    Send(String),
    Stop,
}

/// Orbit is on this PC, so a connection either succeeds at once or is refused. (Windows takes about two seconds to say
/// "refused" for a local port nothing listens on; waiting that long would only make everything else wait.)
const CONNECT_TIMEOUT: Duration = Duration::from_millis(400);

pub struct Link {
    events: Receiver<Event>,
    commands: Sender<Command>,
}

/// How long to wait before retry number `retry` (0, 1, 2 ...): 0.5 s, doubling, at most 8 s.
pub fn backoff(retry: u32) -> Duration {
    Duration::from_millis((500u64 << retry.min(10)).min(8000))
}

impl Link {
    /// Starts connecting to `url` in the background. Fails at once only if the address is not allowed.
    pub fn connect(url: &str, wake: Waker) -> Result<Link, String> {
        let endpoint = endpoint::parse(url)?;
        let (event_tx, events) = mpsc::channel();
        let (commands, command_rx) = mpsc::channel();
        let url = url.trim().to_owned();
        std::thread::Builder::new()
            .name("orbit-link".into())
            .spawn(move || run(&url, &endpoint, &event_tx, &command_rx, &wake))
            .map_err(|e| e.to_string())?;
        Ok(Link { events, commands })
    }

    /// Everything that arrived since the last call.
    pub fn poll(&self) -> Vec<Event> {
        self.events.try_iter().collect()
    }

    /// Queues a text frame. False if the link has stopped.
    pub fn send(&self, text: String) -> bool {
        self.commands.send(Command::Send(text)).is_ok()
    }

    /// Tells the thread to stop and returns at once; the thread notices within 50 ms (or the 0.4 s a connection attempt
    /// can take) and ends by itself. Dropping a `Link` does the same, so the UI thread never waits on the network.
    pub fn stop(self) {}
}

impl Drop for Link {
    fn drop(&mut self) {
        let _ = self.commands.send(Command::Stop);
    }
}

type Socket = WebSocket<TcpStream>;

fn open(url: &str, endpoint: &Endpoint) -> io::Result<Socket> {
    let addrs = (endpoint.host.as_str(), endpoint.port).to_socket_addrs()?;
    let mut last = io::Error::new(io::ErrorKind::NotFound, "no address");
    let mut stream = None;
    for addr in addrs {
        match TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) {
            Ok(s) => {
                stream = Some(s);
                break;
            }
            Err(e) => last = e,
        }
    }
    let stream = stream.ok_or(last)?;
    stream.set_nodelay(true).ok();
    stream.set_read_timeout(Some(Duration::from_secs(3)))?;     // the handshake may not wait forever
    stream.set_write_timeout(Some(Duration::from_secs(3)))?;
    let (socket, _) = tungstenite::client(url, stream).map_err(|e| io::Error::other(e.to_string()))?;
    socket.get_ref().set_read_timeout(Some(Duration::from_millis(50)))?;   // from now on, wake every 50 ms to check for commands
    Ok(socket)
}

enum Ended {
    Lost,
    Stopped,
}

fn session(socket: &mut Socket, commands: &Receiver<Command>, emit: &dyn Fn(Event) -> bool) -> Ended {
    loop {
        loop {
            match commands.try_recv() {
                Ok(Command::Send(text)) => {
                    if socket.send(Message::text(text)).is_err() {
                        return Ended::Lost;
                    }
                }
                Ok(Command::Stop) | Err(TryRecvError::Disconnected) => {
                    let _ = socket.close(None);
                    return Ended::Stopped;
                }
                Err(TryRecvError::Empty) => break,
            }
        }
        match socket.read() {
            Ok(Message::Text(text)) => {
                if !emit(Event::Message(text.as_str().to_owned())) {
                    return Ended::Stopped;                     // nobody is listening any more
                }
            }
            Ok(Message::Close(_)) => return Ended::Lost,
            Ok(_) => {}
            Err(tungstenite::Error::Io(e)) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => {}
            Err(_) => return Ended::Lost,
        }
        // A ping from Orbit is answered on the next flush; do it every turn so the pong is never late.
        match socket.flush() {
            Ok(()) => {}
            Err(tungstenite::Error::Io(e)) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => {}
            Err(_) => return Ended::Lost,
        }
    }
}

/// Waits `total`, in short slices, returning false if a stop arrived meanwhile.
fn pause(total: Duration, commands: &Receiver<Command>) -> bool {
    let until = Instant::now() + total;
    while Instant::now() < until {
        match commands.recv_timeout(Duration::from_millis(50)) {
            Ok(Command::Stop) | Err(mpsc::RecvTimeoutError::Disconnected) => return false,
            Ok(Command::Send(_)) | Err(mpsc::RecvTimeoutError::Timeout) => {}   // nothing is connected to send to
        }
    }
    true
}

fn run(url: &str, endpoint: &Endpoint, events: &Sender<Event>, commands: &Receiver<Command>, wake: &Waker) {
    // False once the window has dropped its end: nobody is listening, so the thread ends.
    let emit = |event: Event| {
        let sent = events.send(event).is_ok();
        if sent {
            wake();
        }
        sent
    };
    let mut retry = 0u32;
    loop {
        if !emit(Event::Status(Status::Connecting)) {
            return;
        }
        if let Ok(mut socket) = open(url, endpoint) {
            retry = 0;
            if !emit(Event::Status(Status::Open)) || matches!(session(&mut socket, commands, &emit), Ended::Stopped) {
                return;
            }
        }
        if !emit(Event::Status(Status::Closed)) || !pause(backoff(retry), commands) {
            return;
        }
        retry += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retries_start_at_half_a_second_double_and_stop_at_eight() {
        let waits: Vec<u64> = (0..7).map(|n| backoff(n).as_millis() as u64).collect();
        assert_eq!(waits, [500, 1000, 2000, 4000, 8000, 8000, 8000]);
        assert_eq!(backoff(u32::MAX), Duration::from_secs(8), "a long outage never overflows");
    }

    #[test]
    fn a_link_to_somewhere_else_is_refused_before_any_thread_starts() {
        let wake: Waker = std::sync::Arc::new(|| {});
        assert!(Link::connect("ws://example.com:8765/?token=x", wake).is_err());
    }

    #[test]
    fn only_connecting_open_and_retrying_count_as_linked() {
        assert!(Status::Connecting.is_linked() && Status::Open.is_linked() && Status::Closed.is_linked());
        assert!(!Status::Off.is_linked() && !Status::Error.is_linked());
    }
}

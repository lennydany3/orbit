//! Where the window may connect: Orbit on this PC, over a plain `ws://` link. The address carries the token, so a typo (or a
//! pasted link to somewhere else) must never send it off this machine.

use tungstenite::http::Uri;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Endpoint {
    pub host: String,
    pub port: u16,
}

pub fn parse(url: &str) -> Result<Endpoint, String> {
    let url = url.trim();
    let uri: Uri = url.parse().map_err(|_| "that is not a link".to_owned())?;
    if uri.scheme_str() != Some("ws") {
        return Err("the link must start with ws://".to_owned());
    }
    let host = uri.host().ok_or("the link has no address")?.trim_matches(|c| c == '[' || c == ']').to_owned();
    if !matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1") {
        return Err("only a link to this PC (127.0.0.1) is allowed: the link carries a password".to_owned());
    }
    Ok(Endpoint { host, port: uri.port_u16().unwrap_or(80) })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_link_orbit_prints_is_understood() {
        assert_eq!(parse("ws://127.0.0.1:8765/?token=abc"), Ok(Endpoint { host: "127.0.0.1".into(), port: 8765 }));
        assert_eq!(parse("  ws://localhost:9000/?token=abc \n"), Ok(Endpoint { host: "localhost".into(), port: 9000 }));
        assert_eq!(parse("ws://[::1]:8765/").unwrap().host, "::1");
    }

    #[test]
    fn nothing_but_this_pc_is_allowed() {
        for url in ["ws://192.168.1.20:8765/?token=abc", "ws://example.com/?token=abc", "ws://127.0.0.1.evil.example:8765/", "ws://localhost.evil.example/"] {
            assert!(parse(url).unwrap_err().contains("this PC"), "{url}");
        }
    }

    #[test]
    fn only_plain_ws_links_are_accepted() {
        assert!(parse("wss://127.0.0.1:8765/").unwrap_err().contains("ws://"));
        assert!(parse("http://127.0.0.1:8765/").unwrap_err().contains("ws://"));
        assert!(parse("").is_err());
        assert!(parse("not a link").is_err());
        assert!(parse("ws://").is_err());
    }
}

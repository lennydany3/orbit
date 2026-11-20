//! The eleven things the sphere can be doing, with the words the design shows for each.

#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash)]
pub enum OrbState {
    Idle,
    Wake,
    Listening,
    Transcribing,
    Thinking,
    Confirming,
    Speaking,
    FollowUp,
    Success,
    Error,
    Muted,
}

/// In the order the dev panel lists them (keys 1 to 0, then M).
pub const ALL: [OrbState; 11] = [
    OrbState::Idle,
    OrbState::Wake,
    OrbState::Listening,
    OrbState::Transcribing,
    OrbState::Thinking,
    OrbState::Confirming,
    OrbState::Speaking,
    OrbState::FollowUp,
    OrbState::Success,
    OrbState::Error,
    OrbState::Muted,
];

impl OrbState {
    /// The name the backend sends (`{"state":"follow_up"}`).
    pub fn id(self) -> &'static str {
        match self {
            OrbState::Idle => "idle",
            OrbState::Wake => "wake",
            OrbState::Listening => "listening",
            OrbState::Transcribing => "transcribing",
            OrbState::Thinking => "thinking",
            OrbState::Confirming => "confirming",
            OrbState::Speaking => "speaking",
            OrbState::FollowUp => "follow_up",
            OrbState::Success => "success",
            OrbState::Error => "error",
            OrbState::Muted => "muted",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            OrbState::Idle => "Idle",
            OrbState::Wake => "Wake",
            OrbState::Listening => "Listening",
            OrbState::Transcribing => "Transcribing",
            OrbState::Thinking => "Thinking",
            OrbState::Confirming => "Confirming",
            OrbState::Speaking => "Speaking",
            OrbState::FollowUp => "Follow-up",
            OrbState::Success => "Success",
            OrbState::Error => "Error",
            OrbState::Muted => "Muted",
        }
    }

    pub fn key(self) -> &'static str {
        match self {
            OrbState::Idle => "1",
            OrbState::Wake => "2",
            OrbState::Listening => "3",
            OrbState::Transcribing => "4",
            OrbState::Thinking => "5",
            OrbState::Confirming => "6",
            OrbState::Speaking => "7",
            OrbState::FollowUp => "8",
            OrbState::Success => "9",
            OrbState::Error => "0",
            OrbState::Muted => "M",
        }
    }

    pub fn description(self) -> &'static str {
        match self {
            OrbState::Idle => "Slow rotation, 30 s per turn. Breathing \u{b1}2% over 4 s.",
            OrbState::Wake => "Bursts out 15%, snaps back with a brightness spike, then listens.",
            OrbState::Listening => "Surface follows the mic level. Ripples travel across it.",
            OrbState::Transcribing => "Contracts and smooths. A scan band sweeps the surface.",
            OrbState::Thinking => "Tilted spin, latitude currents, travelling sparks, slow inner swirl.",
            OrbState::Confirming => "Motion settles. An amber pulse asks for a yes or no.",
            OrbState::Speaking => "Waves radiate from the centre with the voice level.",
            OrbState::FollowUp => "A bright rim arc drains over 2 s, then back to idle.",
            OrbState::Success => "Noise drops to zero. One bright band sweeps around.",
            OrbState::Error => "Brief glitch. A few points flash red, then reform.",
            OrbState::Muted => "Settled, dim, slightly flattened. No motion.",
        }
    }

    /// Accepts the backend's `follow_up` and the hyphenated `follow-up`. The backend link (next step) calls this.
    #[allow(dead_code)]
    pub fn from_id(id: &str) -> Option<OrbState> {
        let id = if id == "follow-up" { "follow_up" } else { id };
        ALL.into_iter().find(|s| s.id() == id)
    }

    /// The dev panel's shortcut keys: 1 to 9, 0, and M.
    pub fn from_key(key: &str) -> Option<OrbState> {
        let key = key.to_ascii_uppercase();
        ALL.into_iter().find(|s| s.key() == key)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_state_round_trips_through_its_id_and_key() {
        for s in ALL {
            assert_eq!(OrbState::from_id(s.id()), Some(s));
            assert_eq!(OrbState::from_key(s.key()), Some(s));
        }
        assert_eq!(OrbState::from_id("follow-up"), Some(OrbState::FollowUp));
        assert_eq!(OrbState::from_key("m"), Some(OrbState::Muted));
        assert_eq!(OrbState::from_id("nope"), None);
    }
}

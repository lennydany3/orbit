//! The orb's two sounds: a soft tone when it fades in and another when it fades out.
//!
//! Playing is built so it costs nothing while the orb is idle: the sounds are compiled into the program (nothing to find on
//! disk), nothing is started until the first sound is wanted, and then a single thread sleeps on a channel (no polling, no audio
//! stream held open). The thread decodes the file, scales it to the chosen volume and hands it to Windows (`PlaySound`, from
//! winmm.dll, which every Windows has), which plays it by itself and returns at once. So no sound ever makes the window wait.
//! This adds no crate. A new sound cuts the one still playing rather than mixing with it.
//!
//! What decides *when* to play is kept apart from the playing, so it can be tested without hearing anything.

use std::collections::VecDeque;
use std::sync::mpsc::{self, Receiver, Sender};
use std::time::{Duration, Instant};

use crate::settings::Sounds;

/// The sound for the orb appearing, and the one for it going away. WAV files in `assets/sounds`, in any common PCM or float
/// layout: they are read when first needed.
const UP: &[u8] = include_bytes!("../assets/sounds/pop-up.wav");
const DOWN: &[u8] = include_bytes!("../assets/sounds/pop-down.wav");

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cue {
    Up,
    Down,
}

impl Cue {
    fn wav(self) -> &'static [u8] {
        match self {
            Cue::Up => UP,
            Cue::Down => DOWN,
        }
    }
}

/// A sound that follows another this soon is left out: an orb shown and hidden again in a blink (the hotkey pressed twice) is
/// one blip, not two stumbling over each other.
pub const QUICK: Duration = Duration::from_millis(350);

/// Decides which changes of the orb get a sound.
#[derive(Debug, Default)]
pub struct Chime {
    last: Option<Instant>,
}

impl Chime {
    /// The orb has just been shown or hidden (`visible` is what it is now), at `now`. Says which sound to play, if any.
    /// Only a real show or hide gets here: a change of what the orb says is not one.
    pub fn on_change(&mut self, visible: bool, sounds: Sounds, now: Instant) -> Option<Cue> {
        if !sounds.audible() {
            return None;
        }
        if self.last.is_some_and(|t| now.saturating_duration_since(t) < QUICK) {
            return None;
        }
        self.last = Some(now);
        Some(if visible { Cue::Up } else { Cue::Down })
    }
}

// ---- the files ------------------------------------------------------------------------------------------------------------

/// A sound as numbers: samples from -1 to 1, the channels interleaved.
#[derive(Clone, Debug, PartialEq)]
pub struct Clip {
    pub rate: u32,
    pub channels: u16,
    pub samples: Vec<f32>,
}

fn u16_at(b: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_le_bytes(b.get(at..at + 2)?.try_into().ok()?))
}

fn u32_at(b: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(at..at + 4)?.try_into().ok()?))
}

impl Clip {
    /// Reads a WAV file: PCM of 8, 16, 24 or 32 bits, or 32 or 64 bit float, one or two channels.
    pub fn from_wav(bytes: &[u8]) -> Result<Clip, String> {
        if bytes.get(0..4) != Some(b"RIFF") || bytes.get(8..12) != Some(b"WAVE") {
            return Err("not a WAV file".into());
        }
        let (mut format, mut data) = (None, None);
        let mut at = 12;
        while let (Some(id), Some(size)) = (bytes.get(at..at + 4), u32_at(bytes, at + 4)) {
            let body = at + 8;
            let end = body.saturating_add(size as usize).min(bytes.len());
            match id {
                b"fmt " => format = Some(&bytes[body.min(end)..end]),
                b"data" => {
                    data = Some(&bytes[body.min(end)..end]);
                    break;
                }
                _ => {}
            }
            at = body.saturating_add(size as usize).saturating_add(size as usize & 1);
        }
        let (fmt, data) = (format.ok_or("no fmt chunk")?, data.ok_or("no data chunk")?);
        let (mut tag, channels, rate, bits) = (u16_at(fmt, 0).ok_or("short fmt chunk")?, u16_at(fmt, 2).ok_or("short fmt chunk")?, u32_at(fmt, 4).ok_or("short fmt chunk")?, u16_at(fmt, 14).ok_or("short fmt chunk")?);
        if tag == 0xFFFE {
            tag = u16_at(fmt, 24).ok_or("short extensible fmt chunk")?;     // the real format is the first two bytes of the sub-format
        }
        if !(1..=2).contains(&channels) || rate == 0 {
            return Err(format!("{channels} channels at {rate} Hz is not something Orbit plays"));
        }
        let width = (bits / 8) as usize;
        let frame = width * channels as usize;
        let usable = data.len() - data.len() % frame.max(1);
        let raw = &data[..usable];
        let samples: Vec<f32> = match (tag, bits) {
            (1, 8) => raw.iter().map(|b| (*b as f32 - 128.0) / 128.0).collect(),
            (1, 16) => raw.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0).collect(),
            (1, 24) => raw.chunks_exact(3).map(|b| (i32::from_le_bytes([0, b[0], b[1], b[2]]) >> 8) as f32 / 8_388_608.0).collect(),
            (1, 32) => raw.chunks_exact(4).map(|b| i32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f32 / 2_147_483_648.0).collect(),
            (3, 32) => raw.chunks_exact(4).map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]])).collect(),
            (3, 64) => raw.chunks_exact(8).map(|b| f64::from_le_bytes(b.try_into().unwrap_or([0; 8])) as f32).collect(),
            _ => return Err(format!("WAV format {tag} at {bits} bits is not something Orbit plays")),
        };
        if samples.is_empty() {
            return Err("the WAV has no sound in it".into());
        }
        Ok(Clip { rate, channels, samples })
    }

    pub fn seconds(&self) -> f32 {
        self.samples.len() as f32 / (self.rate as f32 * self.channels as f32)
    }

    /// The loudest point, from 0 to 1 (or more, if the file is over-full).
    pub fn peak(&self) -> f32 {
        self.samples.iter().fold(0.0, |m, s| m.max(s.abs()))
    }

    /// A WAV file of this sound at `volume` (0 to 100), as 16-bit PCM, ready for Windows to play from memory.
    pub fn render(&self, volume: u8) -> Vec<u8> {
        let gain = gain(volume);
        let data_len = self.samples.len() * 2;
        let mut wav = Vec::with_capacity(44 + data_len);
        let (channels, rate) = (self.channels, self.rate);
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&(36 + data_len as u32).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&channels.to_le_bytes());
        wav.extend_from_slice(&rate.to_le_bytes());
        wav.extend_from_slice(&(rate * channels as u32 * 2).to_le_bytes());
        wav.extend_from_slice(&(channels * 2).to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&(data_len as u32).to_le_bytes());
        for s in &self.samples {
            let level = ((s * gain).clamp(-1.0, 1.0) * 32767.0).round() as i16;
            wav.extend_from_slice(&level.to_le_bytes());
        }
        wav
    }
}

/// How loud 0 to 100 is: squared, because ears hear loudness on a curve, so the slider's lower half is not all "almost silent"
/// and the top is the file as it is (never louder than the file).
pub fn gain(volume: u8) -> f32 {
    let v = f32::from(volume.min(100)) / 100.0;
    v * v
}

// ---- playing ----------------------------------------------------------------------------------------------------------------

/// Plays the sounds. Nothing is started until the first one is asked for. (`Default` is the quiet one, so tests and headless
/// pictures never make a sound; the real window asks for `speaking`.)
pub struct Player {
    tx: Option<Sender<(Cue, u8)>>,
    /// A player that makes no sound and only writes down what it was asked to play (for tests and pictures).
    pub silent: bool,
    pub played: Vec<(Cue, u8)>,
}

impl Default for Player {
    fn default() -> Self {
        Player::silent()
    }
}

impl Player {
    /// The real one.
    pub fn speaking() -> Player {
        Player { tx: None, silent: false, played: Vec::new() }
    }

    /// One that stays quiet.
    pub fn silent() -> Player {
        Player { tx: None, silent: true, played: Vec::new() }
    }

    /// Asks for `cue` at `volume` and returns at once. A volume of 0 plays nothing.
    pub fn play(&mut self, cue: Cue, volume: u8) {
        if volume == 0 {
            return;
        }
        if self.silent {
            self.played.push((cue, volume));
            return;
        }
        if self.tx.is_none() {
            self.tx = start_worker();
        }
        if let Some(tx) = &self.tx {
            if tx.send((cue, volume)).is_err() {
                self.tx = None;                    // the thread is gone: the next sound starts a new one
            }
        }
    }
}

fn start_worker() -> Option<Sender<(Cue, u8)>> {
    let (tx, rx) = mpsc::channel();
    std::thread::Builder::new().name("orbit-sound".into()).spawn(move || worker(rx)).ok().map(|_| tx)
}

/// How many played sounds are kept in memory: Windows reads one from memory while it plays, and a sound is only ever cut by
/// the next, so a few is plenty.
const KEEP: usize = 3;

fn worker(rx: Receiver<(Cue, u8)>) {
    let mut clips: [Option<Clip>; 2] = [None, None];
    let mut playing: VecDeque<Vec<u8>> = VecDeque::new();
    while let Ok(mut job) = rx.recv() {
        while let Ok(newer) = rx.try_recv() {
            job = newer;                           // wanted twice before the first was played: only the latest counts
        }
        let (cue, volume) = job;
        let slot = &mut clips[cue as usize];
        if slot.is_none() {
            *slot = Clip::from_wav(cue.wav()).ok();
        }
        let Some(clip) = slot else { continue };
        playing.push_back(clip.render(volume));
        if let Some(wav) = playing.back() {
            let accepted = os::play(wav);
            if std::env::var_os("ORBIT_UI_DEBUG").is_some() {
                eprintln!("[sound] {cue:?} at volume {volume}: windows took it: {accepted}");
            }
        }
        while playing.len() > KEEP {
            playing.pop_front();
        }
    }
}

#[cfg(windows)]
mod os {
    use std::ffi::c_void;

    #[link(name = "winmm")]
    unsafe extern "system" {
        fn PlaySoundW(sound: *const u8, module: *mut c_void, flags: u32) -> i32;
    }

    const SND_ASYNC: u32 = 0x0001;
    const SND_NODEFAULT: u32 = 0x0002;
    const SND_MEMORY: u32 = 0x0004;

    /// Starts playing a WAV held in memory, and returns at once. The bytes must stay alive until the sound has ended or been
    /// replaced (the caller keeps the last few).
    pub fn play(wav: &[u8]) -> bool {
        // SAFETY: `wav` is a whole WAV file, which is what SND_MEMORY wants, and the caller keeps it in memory past the next
        // call to this (which is what ends or replaces it).
        unsafe { PlaySoundW(wav.as_ptr(), std::ptr::null_mut(), SND_MEMORY | SND_ASYNC | SND_NODEFAULT) != 0 }
    }
}

#[cfg(not(windows))]
mod os {
    pub fn play(_wav: &[u8]) -> bool {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(ms: u64) -> Instant {
        // One fixed starting point for a test: the gaps between sounds are all the test looks at.
        thread_local!(static T0: Instant = Instant::now());
        T0.with(|t| *t) + Duration::from_millis(ms)
    }

    fn on(volume: u8) -> Sounds {
        Sounds { enabled: true, volume }
    }

    fn wav(tag: u16, channels: u16, bits: u16, data: &[u8]) -> Vec<u8> {
        let mut w = b"RIFF".to_vec();
        w.extend_from_slice(&(36 + data.len() as u32).to_le_bytes());
        w.extend_from_slice(b"WAVEfmt ");
        w.extend_from_slice(&16u32.to_le_bytes());
        for v in [tag, channels] {
            w.extend_from_slice(&v.to_le_bytes());
        }
        w.extend_from_slice(&48_000u32.to_le_bytes());
        w.extend_from_slice(&(48_000 * u32::from(channels) * u32::from(bits / 8)).to_le_bytes());
        w.extend_from_slice(&(channels * bits / 8).to_le_bytes());
        w.extend_from_slice(&bits.to_le_bytes());
        w.extend_from_slice(b"data");
        w.extend_from_slice(&(data.len() as u32).to_le_bytes());
        w.extend_from_slice(data);
        w
    }

    // ---- when a sound plays ----

    #[test]
    fn showing_plays_the_pop_up_and_hiding_the_pop_down() {
        let mut chime = Chime::default();
        assert_eq!(chime.on_change(true, on(40), at(0)), Some(Cue::Up));
        assert_eq!(chime.on_change(false, on(40), at(5_000)), Some(Cue::Down));
    }

    #[test]
    fn sounds_that_are_off_or_at_zero_play_nothing() {
        let mut chime = Chime::default();
        assert_eq!(chime.on_change(true, Sounds { enabled: false, volume: 80 }, at(0)), None);
        assert_eq!(chime.on_change(true, on(0), at(10)), None);
        assert_eq!(chime.on_change(true, on(1), at(20)), Some(Cue::Up), "the quietest is still a sound, and the ones left out did not count as played");
    }

    #[test]
    fn a_blink_of_showing_and_hiding_is_one_sound_not_two() {
        let mut chime = Chime::default();
        assert_eq!(chime.on_change(true, on(40), at(1_000)), Some(Cue::Up));
        assert_eq!(chime.on_change(false, on(40), at(1_200)), None, "hidden again 200 ms later");
        assert_eq!(chime.on_change(true, on(40), at(1_300)), None, "and shown again");
        assert_eq!(chime.on_change(false, on(40), at(1_000 + 350)), Some(Cue::Down), "350 ms after the one that sounded is fine");
    }

    #[test]
    fn a_pause_after_a_sound_lets_the_next_one_through() {
        let mut chime = Chime::default();
        assert_eq!(chime.on_change(true, on(40), at(0)), Some(Cue::Up));
        assert_eq!(chime.on_change(false, on(40), at(2_000)), Some(Cue::Down));
        assert_eq!(chime.on_change(true, on(40), at(60_000)), Some(Cue::Up));
    }

    // ---- the files ----

    #[test]
    fn the_two_sounds_that_come_with_orbit_are_short_quiet_and_whole() {
        for (name, bytes) in [("pop-up", UP), ("pop-down", DOWN)] {
            let clip = Clip::from_wav(bytes).unwrap_or_else(|e| panic!("{name}: {e}"));
            assert!(clip.seconds() > 0.05 && clip.seconds() <= 0.5, "{name} is {} s", clip.seconds());
            assert!(clip.peak() > 0.1 && clip.peak() <= 1.0, "{name} peaks at {}", clip.peak());
            let tail = &clip.samples[clip.samples.len() - clip.samples.len() / 50..];
            assert!(tail.iter().all(|s| s.abs() < 0.02), "{name} ends in silence, so it does not click off");
        }
    }

    #[test]
    fn sixteen_bit_wav_files_are_read_exactly() {
        let data: Vec<u8> = [0i16, 16384, -16384, i16::MAX].iter().flat_map(|s| s.to_le_bytes()).collect();
        let clip = Clip::from_wav(&wav(1, 1, 16, &data)).unwrap();
        assert_eq!((clip.rate, clip.channels), (48_000, 1));
        assert_eq!(clip.samples, [0.0, 0.5, -0.5, 32767.0 / 32768.0]);
    }

    #[test]
    fn other_depths_and_floats_are_read_to_the_same_scale() {
        assert_eq!(Clip::from_wav(&wav(1, 1, 8, &[128, 255, 0])).unwrap().samples, [0.0, 127.0 / 128.0, -1.0]);
        let three: Vec<u8> = [0x00, 0x00, 0x40, 0x00, 0x00, 0xC0].to_vec();          // +0.5 and -0.5 in 24 bits
        assert_eq!(Clip::from_wav(&wav(1, 1, 24, &three)).unwrap().samples, [0.5, -0.5]);
        let big: Vec<u8> = (i32::MAX / 2).to_le_bytes().to_vec();
        assert!((Clip::from_wav(&wav(1, 1, 32, &big)).unwrap().samples[0] - 0.5).abs() < 1e-6);
        let floats: Vec<u8> = [0.25f32, -1.0].iter().flat_map(|s| s.to_le_bytes()).collect();
        assert_eq!(Clip::from_wav(&wav(3, 1, 32, &floats)).unwrap().samples, [0.25, -1.0]);
    }

    #[test]
    fn a_wav_with_extra_chunks_a_wrong_length_or_a_stray_byte_still_reads() {
        let mut w = wav(1, 2, 16, &[0, 0, 0, 64, 0, 0, 0, 192, 7]);                  // two frames and one stray byte
        w.splice(36..36, b"LIST\x03\x00\x00\x00abc\x00".iter().copied());             // an odd-sized chunk, padded, before the data
        let clip = Clip::from_wav(&w).unwrap();
        assert_eq!(clip.channels, 2);
        assert_eq!(clip.samples.len(), 4, "whole frames only");
        let mut lying = wav(1, 1, 16, &[0, 64, 0, 64]);
        lying[40..44].copy_from_slice(&u32::MAX.to_le_bytes());                       // a length longer than the file
        assert_eq!(Clip::from_wav(&lying).unwrap().samples.len(), 2);
    }

    #[test]
    fn things_that_are_not_playable_wav_say_so_and_never_panic() {
        assert!(Clip::from_wav(b"").is_err());
        assert!(Clip::from_wav(b"RIFF....WAVE").unwrap_err().contains("fmt"));
        assert!(Clip::from_wav(&wav(1, 1, 16, &[])).unwrap_err().contains("no sound"));
        assert!(Clip::from_wav(&wav(1, 6, 16, &[0, 0])).unwrap_err().contains("channels"));
        assert!(Clip::from_wav(&wav(85, 1, 16, &[0, 0])).unwrap_err().contains("format 85"), "an MP3 inside a WAV");
        let whole = wav(1, 1, 16, &[1, 2, 3, 4]);
        for cut in 0..whole.len() {
            let _ = Clip::from_wav(&whole[..cut]);
        }
    }

    // ---- the volume ----

    #[test]
    fn the_volume_curve_is_silent_at_zero_the_file_at_a_hundred_and_gentle_between() {
        assert_eq!((gain(0), gain(100)), (0.0, 1.0));
        assert_eq!(gain(200), 1.0, "never louder than the file");
        assert!((gain(50) - 0.25).abs() < 1e-6);
        assert!((0..100u8).all(|v| gain(v) < gain(v + 1)), "every step is a little louder");
    }

    #[test]
    fn rendering_scales_the_sound_and_writes_a_wav_windows_can_play() {
        let clip = Clip { rate: 44_100, channels: 1, samples: vec![0.0, 1.0, -1.0, 0.5] };
        let full = clip.render(100);
        assert_eq!((&full[0..4], &full[8..12], full.len()), (&b"RIFF"[..], &b"WAVE"[..], 44 + 8));
        let back = Clip::from_wav(&full).unwrap();
        assert_eq!((back.rate, back.channels), (44_100, 1));
        assert!((back.samples[1] - 1.0).abs() < 1e-3 && (back.samples[3] - 0.5).abs() < 1e-3);
        let half = Clip::from_wav(&clip.render(50)).unwrap();
        assert!((half.samples[1] - 0.25).abs() < 1e-3, "50 is a quarter of the amplitude: {}", half.samples[1]);
        assert!(Clip::from_wav(&clip.render(0)).unwrap().peak() == 0.0);
        let over = Clip { rate: 8000, channels: 1, samples: vec![3.0, -3.0] };
        assert!(Clip::from_wav(&over.render(100)).unwrap().peak() <= 1.0, "a file that was too hot is held at full scale");
    }

    // ---- the player ----

    #[test]
    fn a_silent_player_only_writes_down_what_it_was_asked_and_zero_is_not_asked() {
        let mut p = Player::silent();
        p.play(Cue::Up, 40);
        p.play(Cue::Down, 0);
        p.play(Cue::Down, 70);
        assert_eq!(p.played, [(Cue::Up, 40), (Cue::Down, 70)]);
    }

    #[test]
    fn nothing_is_started_until_a_sound_is_asked_for() {
        let p = Player::speaking();
        assert!(p.tx.is_none(), "no thread, no channel, no audio until the first sound");
    }

    #[test]
    fn asking_for_a_sound_returns_at_once_and_never_waits_for_it_to_play() {
        let mut p = Player::speaking();
        let began = Instant::now();
        for _ in 0..20 {
            p.play(Cue::Up, 1);
        }
        assert!(began.elapsed() < Duration::from_millis(250), "twenty requests took {:?}", began.elapsed());
    }
}

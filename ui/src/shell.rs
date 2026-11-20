//! The window's connection to the desktop: the floating orb, and the tray icon. Everything here talks to Rune's handles, which
//! work from any thread; the decisions (when the orb shows, what it says) are made elsewhere and only carried out here.

use std::time::Duration;

use rune::{AppHandle, OverlayHandle, Tray, TrayEvent, TrayIcon, TrayItem, TraySpec};

use crate::app::App;
use crate::face::{Face, FaceKey};
use crate::orbit::geometry::sphere_points;

/// How the orb leaves: a quick fade (Windows and macOS; elsewhere it just goes).
const FADE_OUT: Duration = Duration::from_millis(260);
const FADE_IN: Duration = Duration::from_millis(160);

#[derive(Default)]
pub struct Shell {
    orb: Option<OverlayHandle<Face>>,
    pub hub: Option<AppHandle<App>>,
    /// Kept alive: dropping it takes the icon out of the tray.
    tray: Option<Tray>,
    shown: bool,
    last_key: Option<FaceKey>,
    last_size: Option<(f32, f32)>,
}

impl Shell {
    pub fn new(orb: OverlayHandle<Face>, hub: AppHandle<App>, tray: Option<Tray>) -> Shell {
        Shell { orb: Some(orb), hub: Some(hub), tray, shown: false, last_key: None, last_size: None }
    }

    pub fn has_tray(&self) -> bool {
        self.tray.is_some()
    }

    /// Brings the orb up to date: new content if it changed, a new size if the question came or went, and a fade in or out.
    pub fn apply(&mut self, face: Face, visible: bool) {
        let Some(orb) = &self.orb else { return };
        let (key, size) = (face.key(), face.size());
        if self.last_size != Some(size) {
            orb.resize(size);
            self.last_size = Some(size);
        }
        if self.last_key.as_ref() != Some(&key) {
            self.last_key = Some(key);
            orb.update(move |state, _| *state = face);
        }
        if visible != self.shown {
            self.shown = visible;
            if visible {
                orb.fade_in(FADE_IN);
            } else {
                orb.fade_out(FADE_OUT);
            }
        }
    }
}

const ICON: usize = 32;

/// The tray icon: the sphere in miniature on a dark disc, so it shows on a light or a dark taskbar.
pub fn tray_icon() -> TrayIcon {
    TrayIcon::rgba(ICON as u32, ICON as u32, tray_pixels())
}

/// Straight (not premultiplied) RGBA rows.
fn tray_pixels() -> Vec<u8> {
    const N: usize = ICON;
    let mut px = vec![0u8; N * N * 4];
    let (c, r) = ((N as f32 - 1.0) / 2.0, N as f32 / 2.0 - 1.0);
    let mut put = |x: i32, y: i32, rgb: [f32; 3], a: f32| {
        if (0..N as i32).contains(&x) && (0..N as i32).contains(&y) {
            let i = (y as usize * N + x as usize) * 4;
            let under = px[i + 3] as f32 / 255.0;
            let out = a + under * (1.0 - a);
            for k in 0..3 {
                px[i + k] = ((rgb[k] * a + (px[i + k] as f32 / 255.0) * under * (1.0 - a)) / out.max(1e-4) * 255.0) as u8;
            }
            px[i + 3] = (out * 255.0) as u8;
        }
    };
    for y in 0..N as i32 {
        for x in 0..N as i32 {
            let d = ((x as f32 - c).powi(2) + (y as f32 - c).powi(2)).sqrt();
            let edge = (r + 0.5 - d).clamp(0.0, 1.0);
            if edge > 0.0 {
                put(x, y, [0.04, 0.04, 0.05], 0.94 * edge);
            }
        }
    }
    let (tilt_c, tilt_s) = (0.38f32.cos(), 0.38f32.sin());
    for p in sphere_points(420) {
        let [x, y, z] = p.dir;
        let (y2, z2) = (y * tilt_c - z * tilt_s, y * tilt_s + z * tilt_c);
        if z2 < -0.05 {
            continue;                                              // the far side
        }
        let a = 0.35 + 0.65 * z2.max(0.0);
        put((c + x * (r - 3.5)).round() as i32, (c - y2 * (r - 3.5)).round() as i32, [0.95, 0.955, 0.965], a);
    }
    px
}

/// The tray icon and its menu. Every action goes through the hub, on the app's own thread; the tray's thread only forwards.
pub fn make_tray(hub: AppHandle<App>) -> Option<Tray> {
    let spec = TraySpec::new("orbit.face", "Orbit", tray_icon()).tooltip("Orbit").menu(vec![
        TrayItem::action("orb", "Show or hide the orb"),
        TrayItem::action("window", "Open the Orbit window"),
        TrayItem::Separator,
        TrayItem::action("quit", "Quit Orbit"),
    ]);
    let tray = Tray::new(spec, move |event| match event {
        TrayEvent::Activate { .. } => hub.update(|app, tx| app.toggle_pin(crate::clock::now(tx.clock()))),
        TrayEvent::Menu(id) => match id.as_str() {
            "orb" => hub.update(|app, tx| app.toggle_pin(crate::clock::now(tx.clock()))),
            "window" => hub.open(),
            "quit" => hub.quit(),
            _ => {}
        },
        _ => {}
    });
    match tray {
        Ok(tray) => Some(tray),
        Err(why) => {
            eprintln!("orbit-ui: no tray icon ({why})");
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_tray_icon_is_a_dark_disc_with_light_dots_and_clear_corners() {
        let px = tray_pixels();
        assert_eq!(px.len(), ICON * ICON * 4);
        let at = |x: usize, y: usize| &px[(y * ICON + x) * 4..(y * ICON + x) * 4 + 4];
        assert_eq!(at(0, 0)[3], 0, "the corner is transparent");
        assert_eq!(at(ICON - 1, ICON - 1)[3], 0);
        assert!(at(ICON / 2, 2)[3] > 200, "the disc is solid near its top");
        let bright = px.chunks(4).filter(|p| p[3] > 100 && p[0] > 150).count();
        let dark = px.chunks(4).filter(|p| p[3] > 100 && p[0] < 60).count();
        assert!(bright > 40, "the sphere's dots show: {bright}");
        assert!(dark > bright, "mostly disc: {dark} vs {bright}");
    }
}

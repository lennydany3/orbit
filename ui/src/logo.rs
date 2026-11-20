//! Orbit's mark: the dot sphere (`assets/logo/orbit-dot-sphere.svg`). The window, taskbar and Alt+Tab icon, the tray icon, and the
//! small mark beside the title in the window. The icon files are drawn from the SVG's numbers by `assets/logo/make_icons.py`.

use rune::{Color, El, el};

/// The app tile (a white rounded square with the black sphere), 256 x 256 PNG: the window, taskbar and Alt+Tab icon.
const APP_ICON: &[u8] = include_bytes!("../assets/logo/orbit-icon-256.png");
/// The small mark (a ring and one dot on white), 32 x 32 straight RGBA: the tray icon, which Windows shows at 16 to 24 px.
const TRAY_RGBA: &[u8] = include_bytes!("../assets/logo/orbit-tray-32.rgba");
pub const TRAY_SIZE: u32 = 32;

/// Gives every window the program opens the Orbit icon (title bar, taskbar, Alt+Tab). Call before the windows are made.
pub fn install() {
    if let Err(why) = rune::set_app_icon(APP_ICON) {
        eprintln!("orbit-ui: no window icon ({why})");
    }
}

/// The tray icon's pixels.
pub fn tray_rgba() -> Vec<u8> {
    TRAY_RGBA.to_vec()
}

#[path = "logo_dots.rs"]
mod dots;
pub use dots::DOTS;

/// The primary mark is 444 design units across (its rim dots sit 214 from the centre; the satellite's wake reaches a little past the rim).
const SPAN: f32 = 444.0;

/// The primary mark `size` points across, in `color`: no outline, the dots make the sphere. Dots too small to draw are kept at
/// a point of width and keep their share of the opacity.
pub fn mark(size: f32, color: Color) -> El {
    let k = size / SPAN;
    let c = size / 2.0;
    let mut m = el().key("mark").size(size, size);
    for (i, (x, y, r, opacity)) in DOTS.into_iter().enumerate() {
        let wanted = r * k * 2.0;
        let d = wanted.max(1.0);
        let alpha = opacity * (wanted / d).min(1.0);
        m = m.child(el().key(format!("mark-dot-{i}")).absolute(c + x * k - d / 2.0, c + y * k - d / 2.0).size(d, d).radius(d / 2.0).fill(color.alpha(alpha)));
    }
    m
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_app_icon_is_a_256_pixel_png() {
        assert_eq!(&APP_ICON[..8], b"\x89PNG\r\n\x1a\n");
        assert_eq!(u32::from_be_bytes(APP_ICON[16..20].try_into().unwrap()), 256, "width");
        assert_eq!(u32::from_be_bytes(APP_ICON[20..24].try_into().unwrap()), 256, "height");
        assert_eq!(APP_ICON[25], 6, "RGBA, so the corners can be clear");
    }

    #[test]
    fn the_tray_icon_is_a_white_tile_with_a_black_ring_and_dot() {
        let px = tray_rgba();
        assert_eq!(px.len(), (TRAY_SIZE * TRAY_SIZE * 4) as usize);
        let at = |x: usize, y: usize| &px[(y * TRAY_SIZE as usize + x) * 4..][..4];
        assert_eq!(at(0, 0), [255, 255, 255, 255], "the tile's corner is solid white");
        assert_eq!(at(16, 16), [255, 255, 255, 255], "and so is the middle of the ring");
        let dark = px.chunks(4).filter(|p| p[3] > 200 && p[0] < 60).count();
        assert!((40..260).contains(&dark), "a thin ring and one dot: {dark} dark pixels");
    }

    #[test]
    fn the_mark_is_seventy_dots_with_a_separate_satellite() {
        let _ = mark(44.0, Color::WHITE);
        assert_eq!(DOTS.len(), 70);
        assert!(DOTS.iter().all(|(x, y, r, _)| x.abs() + r <= SPAN / 2.0 && y.abs() + r <= SPAN / 2.0), "everything is inside the mark's {SPAN} units");
        let rim = DOTS.iter().filter(|d| (d.0 * d.0 + d.1 * d.1).sqrt() > 205.0 && (5.0..=6.0).contains(&d.2)).count();
        assert_eq!(rim, 24, "the outer rim is 24 even dots");
        let satellite = DOTS.iter().find(|d| d.2 == 11.0).expect("the satellite");
        assert_eq!((satellite.0, satellite.1), (165.0, -145.0));
        assert!(DOTS.iter().all(|d| d.3 > 0.0 && d.3 <= 1.0));
    }
}

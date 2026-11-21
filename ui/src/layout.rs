//! Where the floating orb's window goes on the screen, and how the orb sits inside it. Pure arithmetic, so all of it is tested.
//!
//! The orb is placed by two fractions, `x` and `y`, from 0 (the left, the top) to 1 (the right, the bottom) of the part of the
//! screen apps can use (the desktop without the taskbar). Whatever the fractions and the size, the orb stays fully on screen,
//! a small gap in from the edge, so it can never be dragged out of reach. The window around it is wider than the orb so a
//! question's card fits; it is moved so the orb itself stays put when the card comes and goes: the card opens above the orb,
//! or below it when the orb is in the upper half of the screen.

use std::sync::OnceLock;

use crate::settings::OrbLook;

/// The orb window's width in points: wide enough for the question's card.
pub const WIDTH: f32 = 420.0;
/// The card's width.
pub const CARD_W: f32 = WIDTH - 40.0;
/// Room left around the orb in its window.
pub const SPARE: f32 = 28.0;
/// The extra height while a question is open.
pub const ASK_EXTRA: f32 = 164.0;
/// How far the orb keeps in from the edges of the usable screen.
pub const EDGE_X: f32 = 20.0;
pub const EDGE_Y: f32 = 14.0;

/// The part of the primary screen apps can use (without the taskbar), in points from the screen's top-left.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Screen {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
}

impl Default for Screen {
    /// A 1080p screen with a 40 point taskbar, until the real one is known.
    fn default() -> Screen {
        Screen { x: 0.0, y: 0.0, w: 1920.0, h: 1040.0 }
    }
}

impl Screen {
    /// The usable part of a display of `size` pixels at `scale`, given the work area in pixels (`[left, top, right, bottom]`) if
    /// Windows told us. A work area that does not fit inside the display is ignored.
    pub fn from_pixels(position: (i32, i32), size: (u32, u32), scale: f64, work: Option<[i32; 4]>) -> Screen {
        let scale = if scale.is_finite() && scale > 0.0 { scale as f32 } else { 1.0 };
        let (left, top, right, bottom) = (position.0, position.1, position.0 + size.0 as i32, position.1 + size.1 as i32);
        let [wl, wt, wr, wb] = work.filter(|w| w[0] >= left && w[1] >= top && w[2] <= right && w[3] <= bottom && w[2] > w[0] && w[3] > w[1]).unwrap_or([left, top, right, bottom]);
        Screen { x: (wl - left) as f32 / scale, y: (wt - top) as f32 / scale, w: (wr - wl) as f32 / scale, h: (wb - wt) as f32 / scale }
    }
}

static SCREEN: OnceLock<Screen> = OnceLock::new();

/// Remembers the screen the orb lives on (once, as the window starts).
pub fn set_screen(screen: Screen) {
    let _ = SCREEN.set(screen);
}

/// The screen the orb lives on (a 1080p one if it has not been told).
pub fn screen() -> Screen {
    SCREEN.get().copied().unwrap_or_default()
}

/// The work area of the primary display in pixels, `[left, top, right, bottom]`, as Windows reports it.
#[cfg(windows)]
pub fn work_area() -> Option<[i32; 4]> {
    #[repr(C)]
    struct Rect {
        left: i32,
        top: i32,
        right: i32,
        bottom: i32,
    }
    #[link(name = "user32")]
    unsafe extern "system" {
        fn SystemParametersInfoW(action: u32, param: u32, pv: *mut Rect, win_ini: u32) -> i32;
    }
    const SPI_GETWORKAREA: u32 = 0x0030;
    let mut rect = Rect { left: 0, top: 0, right: 0, bottom: 0 };
    // SAFETY: SPI_GETWORKAREA writes one RECT through the pointer, which points at a live one.
    let ok = unsafe { SystemParametersInfoW(SPI_GETWORKAREA, 0, &mut rect, 0) } != 0;
    ok.then_some([rect.left, rect.top, rect.right, rect.bottom])
}

#[cfg(not(windows))]
pub fn work_area() -> Option<[i32; 4]> {
    None
}

/// Where the orb's window is and where the orb is inside it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Place {
    /// The window's top-left on the screen, in points. `None` = not known (no screen to measure): the window sits at the bottom
    /// right, where it always did.
    pub at: Option<(f32, f32)>,
    /// The orb's diameter.
    pub diameter: f32,
    /// The orb's left edge inside the window.
    pub orb_x: f32,
    /// The question card's left edge inside the window.
    pub card_x: f32,
    /// The orb is in the upper half of the screen, so the card opens below it instead of above.
    pub flip: bool,
}

impl Default for Place {
    /// The bottom right, as before there was anything to set.
    fn default() -> Place {
        let diameter = crate::settings::OrbSize::Medium.diameter();
        Place { at: None, diameter, orb_x: WIDTH - diameter, card_x: WIDTH - CARD_W, flip: false }
    }
}

/// `v` limited to `lo..=hi`, and to `lo` when the range is empty (a screen too small for the orb).
fn between(v: f32, lo: f32, hi: f32) -> f32 {
    v.max(lo).min(hi.max(lo))
}

/// Where the orb's window goes for this look on this screen, with or without a question open.
pub fn place(look: OrbLook, screen: Screen, ask: bool) -> Place {
    let d = look.size.diameter();
    let height = d + SPARE + if ask { ASK_EXTRA } else { 0.0 };
    let (min_left, max_left) = (screen.x + EDGE_X, screen.x + screen.w - EDGE_X - d);
    let (min_top, max_top) = (screen.y + EDGE_Y, screen.y + screen.h - EDGE_Y - d);
    let left = min_left + (max_left.max(min_left) - min_left) * look.x.clamp(0.0, 1.0);
    let top = min_top + (max_top.max(min_top) - min_top) * look.y.clamp(0.0, 1.0);
    let centre = left + d / 2.0;
    let flip = top + d / 2.0 < screen.y + screen.h / 2.0;
    let win_x = between(centre - WIDTH / 2.0, min_left, screen.x + screen.w - EDGE_X - WIDTH);
    let win_y = if flip { top } else { top + d - height };
    Place { at: Some((win_x, win_y)), diameter: d, orb_x: left - win_x, card_x: between(centre - win_x - CARD_W / 2.0, 0.0, WIDTH - CARD_W), flip }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::OrbSize;

    fn look(x: f32, y: f32, size: OrbSize) -> OrbLook {
        OrbLook { x, y, size }
    }

    #[test]
    fn the_default_is_where_the_orb_always_was() {
        let screen = Screen::default();
        let p = place(OrbLook::default(), screen, false);
        let (x, y) = p.at.unwrap();
        assert_eq!((x + WIDTH, y + 112.0 + SPARE), (screen.w - EDGE_X, screen.h - EDGE_Y), "20 in from the right, 14 above the taskbar");
        assert_eq!((p.orb_x, p.card_x, p.flip), (WIDTH - 112.0, WIDTH - CARD_W, false), "right edge of its window, as the old layout had it");
        let (dx, dy) = (Place::default().orb_x, Place::default().card_x);
        assert_eq!((dx, dy), (p.orb_x, p.card_x), "the unplaced face is laid out the same way");
    }

    #[test]
    fn a_question_opens_above_without_moving_the_orb() {
        let screen = Screen::default();
        let calm = place(OrbLook::default(), screen, false);
        let asking = place(OrbLook::default(), screen, true);
        // the orb is at the bottom of its window, which grew upwards: the bottom edge is where it was
        assert_eq!(calm.at.unwrap().1 + calm.diameter + SPARE, asking.at.unwrap().1 + asking.diameter + SPARE + ASK_EXTRA);
        assert_eq!(calm.at.unwrap().0, asking.at.unwrap().0);
    }

    #[test]
    fn in_the_upper_half_the_card_opens_below() {
        let screen = Screen::default();
        let p = place(look(0.5, 0.0, OrbSize::Medium), screen, true);
        assert!(p.flip);
        assert_eq!(p.at.unwrap().1, EDGE_Y, "the window starts at the orb's top, so the orb has not moved");
        let low = place(look(0.5, 1.0, OrbSize::Medium), screen, true);
        assert!(!low.flip);
        let middle = place(look(0.5, 0.5, OrbSize::Medium), screen, false);
        assert!(!middle.flip, "exactly half way counts as the lower half");
    }

    #[test]
    fn the_orb_never_leaves_the_screen_whatever_the_fractions() {
        let screen = Screen { x: 0.0, y: 0.0, w: 1280.0, h: 720.0 };
        for size in OrbSize::ALL {
            let d = size.diameter();
            for (x, y) in [(-3.0, -3.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (9.0, 9.0), (0.0, 1.0), (1.0, 0.0)] {
                for ask in [false, true] {
                    let p = place(look(x, y, size), screen, ask);
                    let (wx, wy) = p.at.unwrap();
                    assert!(wx >= 0.0 && wx + WIDTH <= screen.w, "{x},{y}: window x {wx}");
                    let (ox, h) = (wx + p.orb_x, d + SPARE + if ask { ASK_EXTRA } else { 0.0 });
                    assert!(ox >= EDGE_X - 0.01 && ox + d <= screen.w - EDGE_X + 0.01, "{x},{y}: orb x {ox}");
                    let top = if p.flip { wy } else { wy + h - d };
                    assert!(top >= EDGE_Y - 0.01 && top + d <= screen.h - EDGE_Y + 0.01, "{x},{y}: orb top {top}");
                    assert!(wy >= -0.01 && wy + h <= screen.h + 0.01, "{x},{y}: window y {wy} + {h}");
                    assert!(p.orb_x >= 0.0 && p.orb_x + d <= WIDTH + 0.01 && p.card_x >= 0.0 && p.card_x + CARD_W <= WIDTH, "{x},{y}: inside its window");
                }
            }
        }
    }

    #[test]
    fn the_fractions_run_from_edge_to_edge_of_the_usable_screen() {
        let screen = Screen { x: 60.0, y: 0.0, w: 1860.0, h: 1080.0 };      // a taskbar down the left side
        let d = 112.0;
        let left = place(look(0.0, 0.5, OrbSize::Medium), screen, false);
        assert_eq!(left.at.unwrap().0 + left.orb_x, 60.0 + EDGE_X, "starts beside the taskbar, not under it");
        let right = place(look(1.0, 0.5, OrbSize::Medium), screen, false);
        assert_eq!(right.at.unwrap().0 + right.orb_x + d, 1920.0 - EDGE_X);
        let mid = place(look(0.5, 0.5, OrbSize::Medium), screen, false);
        let centre = mid.at.unwrap().0 + mid.orb_x + d / 2.0;
        assert!((centre - (60.0 + 1860.0 / 2.0)).abs() < 0.01, "{centre}");
    }

    #[test]
    fn the_card_sits_over_the_orb_as_far_as_the_window_allows() {
        let screen = Screen::default();
        let mid = place(look(0.5, 1.0, OrbSize::Medium), screen, true);
        let (orb_centre, card_centre) = (mid.orb_x + 56.0, mid.card_x + CARD_W / 2.0);
        assert!((orb_centre - card_centre).abs() < 0.01, "in the middle of the screen the card is right over it");
        let corner = place(look(1.0, 1.0, OrbSize::Medium), screen, true);
        assert_eq!(corner.card_x + CARD_W, WIDTH, "in a corner the card is as near as the window lets it be");
    }

    #[test]
    fn a_bigger_orb_is_bigger_and_still_in_place() {
        let screen = Screen::default();
        let (s, m, l) = (OrbSize::Small.diameter(), OrbSize::Medium.diameter(), OrbSize::Large.diameter());
        assert!(s < m && m < l);
        let p = place(look(1.0, 1.0, OrbSize::Large), screen, false);
        assert_eq!(p.diameter, l);
        assert_eq!(p.at.unwrap().0 + p.orb_x + l, screen.w - EDGE_X);
    }

    #[test]
    fn a_screen_too_small_for_the_orb_still_gives_a_place() {
        let tiny = Screen { x: 0.0, y: 0.0, w: 100.0, h: 100.0 };
        let p = place(look(1.0, 1.0, OrbSize::Large), tiny, true);
        assert!(p.at.is_some() && p.orb_x.is_finite() && p.card_x.is_finite());
    }

    #[test]
    fn the_work_area_trims_the_taskbar_and_scales_to_points() {
        let full = Screen::from_pixels((0, 0), (2560, 1440), 1.5, None);
        assert_eq!(full, Screen { x: 0.0, y: 0.0, w: 2560.0 / 1.5, h: 1440.0 / 1.5 });
        let trimmed = Screen::from_pixels((0, 0), (2560, 1440), 1.5, Some([0, 0, 2560, 1380]));
        assert_eq!((trimmed.w, trimmed.h), (2560.0 / 1.5, 1380.0 / 1.5), "60 pixels of taskbar are not usable");
        let nonsense = Screen::from_pixels((0, 0), (1920, 1080), 1.0, Some([0, 0, 5000, 5000]));
        assert_eq!(nonsense, Screen { x: 0.0, y: 0.0, w: 1920.0, h: 1080.0 }, "a work area outside the display is not believed");
        assert_eq!(Screen::from_pixels((0, 0), (1920, 1080), 0.0, None).w, 1920.0, "a nonsense scale is one");
    }
}

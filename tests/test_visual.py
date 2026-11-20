"""The on-screen animations: the maths, the drawing, and the safe-to-call API. Nothing here opens a window: the real stage is
checked by hand (python -m orbit.visual.demo) and the tests use a fake one."""
import numpy as np
import pytest

from orbit import config, visual
from orbit.visual import motion, sprites, textmask


# ---------- motion ----------
def test_easing_is_pinned_at_both_ends_and_never_goes_backwards():
    for ease in (motion.ease_in_out, motion.ease_out):
        assert ease(0) == 0 and ease(1) == 1 and ease(-3) == 0 and ease(7) == 1
        values = [ease(i / 50) for i in range(51)]
        assert values == sorted(values)


def test_a_cursor_path_starts_and_ends_exactly_on_its_points():
    a, b = (100.0, 700.0), (900.0, 300.0)
    assert motion.arc_point(a, b, 0) == pytest.approx(a) and motion.arc_point(a, b, 1) == pytest.approx(b)


def test_a_cursor_path_bows_upward_not_downward():
    a, b = (100.0, 500.0), (900.0, 500.0)                       # a straight run to the right
    assert motion.arc_point(a, b, 0.5)[1] < 500                 # the middle of the path is above the straight line (smaller y)
    a, b = (900.0, 500.0), (100.0, 500.0)                       # and the same to the left
    assert motion.arc_point(a, b, 0.5)[1] < 500


def test_a_zero_length_path_stays_put():
    assert motion.arc_point((5.0, 5.0), (5.0, 5.0), 0.5) == (5.0, 5.0)


def test_the_bow_is_capped_so_a_long_move_is_not_a_huge_loop():
    middle = motion.arc_point((0.0, 1000.0), (6000.0, 1000.0), 0.5)
    assert 1000 - middle[1] <= 90 + 1


def test_travel_time_is_quick_for_short_hops_and_capped_for_long_ones():
    assert motion.travel_seconds(10) == pytest.approx(0.32, abs=0.02)
    assert motion.travel_seconds(100000) == 1.0
    assert motion.travel_seconds(800, speed=2.0) == pytest.approx(motion.travel_seconds(800) / 2)


def test_a_press_shrinks_the_pointer_on_impact_and_springs_back():
    assert motion.press_scale(0) == 1.0
    assert motion.press_scale(motion.IMPACT_AT) == pytest.approx(0.78, abs=0.01)
    assert motion.press_scale(motion.PRESS_SECONDS) == 1.0
    assert motion.IMPACT_AT < motion.PRESS_SECONDS


def test_a_ripple_has_two_rings_that_grow_and_fade_and_then_it_is_over():
    early, late = motion.ripple_rings(0.2), motion.ripple_rings(0.5)
    assert len(early) == 2
    assert early[0][0] < late[0][0] and early[0][1] > late[0][1]          # bigger and fainter later
    assert motion.ripple_rings(motion.RIPPLE_SECONDS + 0.01) == []
    assert motion.ripple_rings(0.0)[0][1] == pytest.approx(1.0)


def test_the_spinner_has_one_bright_leader_that_moves_round():
    first, later = motion.spinner_dots(0.0), motion.spinner_dots(0.25)
    brightest = lambda dots: max(range(len(dots)), key=lambda i: dots[i][2])  # noqa: E731
    assert brightest(first) != brightest(later) and len(first) == 8
    assert all(0 < b <= 1 for _, _, b in first)


def test_lerp_rect_goes_from_one_rectangle_to_the_other():
    a, b = (0, 0, 100, 100), (1000, 200, 1100, 300)
    assert motion.lerp_rect(a, b, 0) == a and motion.lerp_rect(a, b, 1) == b and motion.lerp_rect(a, b, 0.5) == (500, 100, 600, 200)


def test_a_window_slides_slower_than_a_bare_pointer_but_never_longer_than_about_a_second():
    assert motion.window_glide_seconds(0) >= 0.45 and motion.window_glide_seconds(10**6) <= 1.1


# ---------- drawing ----------
def test_the_pointer_is_a_valid_premultiplied_picture_with_a_solid_arrow_at_its_tip():
    sprite, pad = sprites.cursor_sprite(1.0)
    assert sprite.dtype == np.uint8 and sprite.shape[2] == 4
    assert (sprite[..., :3] <= sprite[..., 3:]).all()                      # premultiplied: no colour channel above alpha
    assert sprite[pad + 8, pad + 2, 3] == 255                              # inside the arrow's body
    assert sprite[0, sprite.shape[1] - 1, 3] < 8                           # the far corner is clear (only a faint shadow)


def test_the_pointer_is_drawn_bigger_on_a_bigger_screen_and_fades_with_opacity():
    small, big = sprites.cursor_sprite(1.0)[0], sprites.cursor_sprite(1.5)[0]
    assert big.shape[0] > small.shape[0] and big.shape[1] > small.shape[1]
    half = sprites.cursor_sprite(1.0, 0.5)[0]
    assert half[..., 3].max() <= 128 and sprites.cursor_sprite(1.0, 1.0)[0][..., 3].max() == 255


def test_pointer_sizes_are_reported_consistently():
    w, h, pad = sprites.cursor_size(1.25)
    sprite, pad2 = sprites.cursor_sprite(1.25)
    assert (sprite.shape[1], sprite.shape[0], pad2) == (w, h, pad)
    assert sprites.quantize_scale(1.2549) in (1.24, 1.26)


def test_a_ripple_is_visible_midway_and_gone_at_the_end():
    frame = sprites.ripple_frame(0.2)
    assert frame is not None and frame.shape[:2] == (sprites.ripple_size(1.0),) * 2 and frame[..., 3].max() > 100
    assert (frame[..., :3] <= frame[..., 3:]).all()
    assert sprites.ripple_frame(motion.RIPPLE_SECONDS + 0.1) is None


def test_the_text_mask_draws_real_letters():
    mask = textmask.render_text("Clicking…", 14)
    assert mask.ndim == 2 and mask.shape[1] > 30 and mask.max() > 0.8 and 0 < (mask > 0.5).mean() < 0.5


def test_the_working_pill_has_a_body_and_its_spinner_turns():
    pill = sprites.Pill(textmask.render_text("Typing…", 14), 1.0)
    a, b = pill.frame(0.0), pill.frame(0.3)
    assert a.shape == (pill.height, pill.width, 4) and not np.array_equal(a, b)
    assert a[pill.height // 2, pill.pad + 90, 3] > 200                      # the dark body is solid
    assert a[0, 0, 3] < 8                                                   # the corner is clear
    assert pill.frame(0.0, opacity=0.5)[..., 3].max() <= 128


# ---------- the safe-to-call API ----------
class FakeStage:
    def __init__(self, explode=False):
        self.calls, self.explode = [], explode

    def __getattr__(self, name):
        def call(*args):
            if self.explode:
                raise RuntimeError("the stage broke")
            self.calls.append((name, *args))
        return call


@pytest.fixture
def fake_stage(monkeypatch):
    stage = FakeStage()
    monkeypatch.setattr(config, "VISUALS", True)
    visual.install(stage)
    yield stage
    visual.install(None)


def test_with_animations_off_nothing_is_started_or_called(monkeypatch):
    visual.install(None)
    monkeypatch.setattr(config, "VISUALS", False)
    assert not visual.enabled()
    visual.glide_to(100, 100), visual.click_at(100, 100), visual.grab(1, 1), visual.follow(2, 2), visual.release()
    with visual.working("Typing…", None):
        pass
    assert visual._stage is None


def test_calls_reach_the_stage_when_animations_are_on(fake_stage):
    visual.click_at(300, 400)
    visual.grab(10, 20)
    visual.follow(30, 40)
    visual.release(False)
    assert [c[0] for c in fake_stage.calls] == ["click_at", "grab", "follow", "release"]
    assert fake_stage.calls[0][1:] == (300, 400)


def test_a_point_that_cannot_be_on_screen_is_ignored(fake_stage):
    visual.click_at(0, 0)                        # a control scrolled away reports an empty rectangle at 0,0
    visual.click_at(10**7, 5)
    assert fake_stage.calls == []
    assert visual.sane(-1500, 300) and not visual.sane(0, 0)          # (negative = a monitor to the left: fine)


def test_the_working_pill_shows_for_the_block_and_always_hides_after(fake_stage):
    with pytest.raises(ValueError):
        with visual.working("Searching Google…", lambda: (0, 0, 800, 600)):
            assert fake_stage.calls == [("busy_on", "Searching Google…", (0, 0, 800, 600))]
            raise ValueError("the job failed")
    assert fake_stage.calls[-1] == ("busy_off",)


def test_an_anchor_that_fails_still_lets_the_job_run(fake_stage):
    def broken():
        raise OSError("no window")
    with visual.working("Clicking…", broken):
        pass
    assert fake_stage.calls[0] == ("busy_on", "Clicking…", None)


def test_a_stage_that_breaks_never_breaks_the_caller(monkeypatch):
    monkeypatch.setattr(config, "VISUALS", True)
    visual.install(FakeStage(explode=True))
    try:
        visual.click_at(10, 10)
        visual.release()
        with visual.working("x", None):
            pass
    finally:
        visual.install(None)

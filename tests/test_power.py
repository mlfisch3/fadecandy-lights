"""Power governor tests.

This is the safety-critical component: if it fails open, a 512-pixel run pulls
about 30 A through a supply rated for a fraction of that.  So these tests care
less about the arithmetic being pretty than about the clamp being unconditional.
"""

from __future__ import annotations

import numpy as np
import pytest

from fclights.power import (
    DEFAULT_IDLE_MA_PER_PIXEL,
    DEFAULT_MA_PER_CHANNEL,
    PowerConfigError,
    PowerGovernor,
)


def full_white(n: int) -> np.ndarray:
    return np.ones((n, 3), dtype=np.float32)


class TestPrediction:
    def test_full_white_matches_the_datasheet_arithmetic(self):
        # 512 px * 3 channels * 20 mA = 30.72 A, plus 512 * 1 mA idle.
        governor = PowerGovernor(limit_amps=40.0, pixel_count=512)
        assert governor.full_white_amps == pytest.approx(30.72 + 0.512)

    def test_predicts_full_white_frame(self):
        governor = PowerGovernor(limit_amps=40.0, pixel_count=512)
        assert governor.predict_amps(full_white(512)) == pytest.approx(
            governor.full_white_amps
        )

    def test_black_frame_still_draws_the_quiescent_current(self):
        governor = PowerGovernor(limit_amps=40.0, pixel_count=512)
        black = np.zeros((512, 3), dtype=np.float32)
        assert governor.predict_amps(black) == pytest.approx(0.512)

    def test_prediction_is_linear_in_duty(self):
        governor = PowerGovernor(limit_amps=100.0, pixel_count=100, idle_ma_per_pixel=0.0)
        half = np.full((100, 3), 0.5, dtype=np.float32)
        assert governor.predict_amps(half) == pytest.approx(
            governor.predict_amps(full_white(100)) / 2
        )

    def test_single_channel_is_a_third_of_white(self):
        governor = PowerGovernor(limit_amps=100.0, pixel_count=100, idle_ma_per_pixel=0.0)
        red = np.zeros((100, 3), dtype=np.float32)
        red[:, 0] = 1.0
        assert governor.predict_amps(red) == pytest.approx(100 * DEFAULT_MA_PER_CHANNEL / 1000)

    def test_values_above_one_do_not_inflate_the_estimate(self):
        # The engine clips before we see the frame, but an effect that overshoots
        # must not be able to make the prediction meaningless.
        governor = PowerGovernor(limit_amps=100.0, pixel_count=10, idle_ma_per_pixel=0.0)
        hot = np.full((10, 3), 5.0, dtype=np.float32)
        assert governor.predict_amps(hot) == pytest.approx(
            governor.predict_amps(full_white(10))
        )

    def test_empty_layout_predicts_nothing(self):
        governor = PowerGovernor(limit_amps=1.0, pixel_count=0)
        assert governor.predict_amps(np.zeros((0, 3), dtype=np.float32)) == 0.0


class TestClamping:
    def test_it_actually_clamps(self):
        governor = PowerGovernor(limit_amps=10.0, pixel_count=512)
        frame = full_white(512)
        report = governor.apply(frame)

        assert report.clamped is True
        assert report.requested_amps > 10.0
        assert report.delivered_amps == pytest.approx(10.0, abs=1e-3)
        # And the buffer itself was actually modified, not merely reported on.
        assert frame.max() < 1.0

    def test_the_clamped_frame_really_fits_the_ceiling(self):
        # Re-predicting from the mutated buffer is the check that matters: it is
        # what the strip will actually be asked to draw.
        for limit in (1.0, 3.5, 7.2, 15.0, 30.0):
            governor = PowerGovernor(limit_amps=limit, pixel_count=512)
            frame = full_white(512)
            governor.apply(frame)
            assert governor.predict_amps(frame) <= limit + 1e-6

    def test_arbitrary_frames_are_never_left_over_the_ceiling(self):
        rng = np.random.default_rng(1234)
        governor = PowerGovernor(limit_amps=4.0, pixel_count=512)
        for _ in range(50):
            frame = rng.random((512, 3)).astype(np.float32)
            governor.apply(frame)
            assert governor.predict_amps(frame) <= 4.0 + 1e-6

    def test_frames_under_the_ceiling_pass_through_untouched(self):
        governor = PowerGovernor(limit_amps=30.0, pixel_count=100)
        frame = np.full((100, 3), 0.5, dtype=np.float32)
        before = frame.copy()
        report = governor.apply(frame)

        assert report.clamped is False
        assert report.scale == 1.0
        np.testing.assert_array_equal(frame, before)

    def test_clamping_preserves_hue(self):
        # Scaling the whole frame means the scene dims rather than shifting
        # colour, which is the difference between "a bit dark" and "wrong".
        governor = PowerGovernor(limit_amps=2.0, pixel_count=64)
        frame = np.zeros((64, 3), dtype=np.float32)
        frame[:] = (1.0, 0.5, 0.25)
        governor.apply(frame)

        ratios = frame[0] / frame[0][0]
        np.testing.assert_allclose(ratios, [1.0, 0.5, 0.25], rtol=1e-5)

    def test_clamping_preserves_relative_brightness_between_pixels(self):
        governor = PowerGovernor(limit_amps=1.0, pixel_count=4)
        frame = np.zeros((4, 3), dtype=np.float32)
        frame[0] = 1.0
        frame[1] = 0.5
        frame[2] = 0.25
        governor.apply(frame)

        assert frame[1].mean() == pytest.approx(frame[0].mean() / 2, rel=1e-5)
        assert frame[2].mean() == pytest.approx(frame[0].mean() / 4, rel=1e-5)

    def test_report_headroom_is_never_negative(self):
        governor = PowerGovernor(limit_amps=2.0, pixel_count=512)
        frame = full_white(512)
        report = governor.apply(frame)
        assert report.headroom_amps >= 0.0

    def test_idle_draw_comes_off_the_top_of_the_budget(self):
        # With a ceiling barely above the quiescent draw, almost nothing is left
        # for the LEDs themselves and the frame should be crushed accordingly.
        governor = PowerGovernor(limit_amps=0.6, pixel_count=512)
        frame = full_white(512)
        governor.apply(frame)
        assert governor.predict_amps(frame) <= 0.6 + 1e-6
        assert frame.max() < 0.01


class TestGamma:
    def test_default_gamma_over_predicts_which_is_the_safe_direction(self):
        conservative = PowerGovernor(limit_amps=40.0, pixel_count=64, gamma=1.0)
        accurate = PowerGovernor(limit_amps=40.0, pixel_count=64, gamma=2.2)
        frame = np.full((64, 3), 0.5, dtype=np.float32)
        assert conservative.predict_amps(frame) > accurate.predict_amps(frame)

    def test_clamp_holds_with_a_gamma_model(self):
        governor = PowerGovernor(limit_amps=3.0, pixel_count=512, gamma=2.2)
        frame = full_white(512)
        governor.apply(frame)
        assert governor.predict_amps(frame) <= 3.0 + 1e-6


class TestConfiguration:
    @pytest.mark.parametrize("limit", [0.0, -1.0])
    def test_a_non_positive_ceiling_is_rejected(self, limit):
        with pytest.raises(PowerConfigError):
            PowerGovernor(limit_amps=limit, pixel_count=10)

    def test_a_ceiling_below_the_quiescent_draw_is_rejected_loudly(self):
        # 512 pixels idle at 0.512 A; a 0.4 A supply cannot run this rig at all,
        # and silently outputting black would hide a wiring mistake.
        with pytest.raises(PowerConfigError, match="quiescent"):
            PowerGovernor(limit_amps=0.4, pixel_count=512)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_ceiling_is_rejected(self, value):
        # A NaN ceiling compares False against every frame, so the clamp would
        # fire on every frame and scale by NaN - worse than having no governor.
        with pytest.raises(PowerConfigError, match="finite"):
            PowerGovernor(limit_amps=value, pixel_count=512)

    @pytest.mark.parametrize("field", ["ma_per_channel", "idle_ma_per_pixel", "gamma"])
    def test_a_non_finite_current_model_is_rejected(self, field):
        with pytest.raises(PowerConfigError, match="finite"):
            PowerGovernor(limit_amps=10.0, pixel_count=10, **{field: float("nan")})

    def test_zero_gamma_is_rejected(self):
        with pytest.raises(PowerConfigError):
            PowerGovernor(limit_amps=10.0, pixel_count=10, gamma=0.0)

    def test_current_model_is_configurable(self):
        # Some WS2812B clones draw less; the model must not be hardcoded.
        governor = PowerGovernor(
            limit_amps=100.0, pixel_count=100, ma_per_channel=12.0, idle_ma_per_pixel=0.6
        )
        assert governor.full_white_amps == pytest.approx(100 * 3 * 0.012 + 100 * 0.0006)

    def test_defaults_match_the_documented_datasheet_figures(self):
        assert DEFAULT_MA_PER_CHANNEL == 20.0
        assert DEFAULT_IDLE_MA_PER_PIXEL == 1.0

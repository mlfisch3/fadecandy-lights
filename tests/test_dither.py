"""Temporal dithering of the 8-bit OPC output.

fcserver's OPC input is 8-bit only - its command enum has `SetPixelColors` and
`SystemExclusive` and nothing else - so this is the last place float precision
exists. The slow near-identical white fades this installation is for are exactly
where losing it shows, so these tests are about the precision actually surviving
rather than about the arithmetic being tidy.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from fclights.color import kelvin_to_rgb
from fclights.opc import TemporalDither, quantize_plain


def stream(dither: TemporalDither, value: float, frames: int) -> list[int]:
    buffer = np.full((1, 3), value, dtype=np.float32)
    return [int(dither.quantize(buffer)[0][0]) for _ in range(frames)]


class TestConvergence:
    @pytest.mark.parametrize("code", [0.1, 12.5, 128.3, 128.9, 200.0, 254.5])
    def test_the_average_output_equals_the_exact_value(self, code):
        dither = TemporalDither((1, 3))
        emitted = stream(dither, code / 255.0, 1000)
        assert np.mean(emitted) == pytest.approx(code, abs=0.02)

    def test_it_only_ever_uses_two_adjacent_codes(self):
        # Anything wider would be visible noise rather than dither.
        emitted = set(stream(TemporalDither((1, 3)), 128.3 / 255.0, 500))
        assert emitted == {128, 129}

    def test_an_exact_code_emits_no_dither_at_all(self):
        # Every solid colour a user picks lands exactly on a code, and should
        # not be given noise it does not need.
        assert set(stream(TemporalDither((1, 3)), 200.0 / 255.0, 200)) == {200}

    def test_black_and_full_are_left_alone(self):
        assert set(stream(TemporalDither((1, 3)), 0.0, 100)) == {0}
        assert set(stream(TemporalDither((1, 3)), 1.0, 100)) == {255}

    def test_plain_rounding_cannot_do_this(self):
        # The comparison that justifies the whole mechanism.
        buffer = np.full((1, 3), 128.3 / 255.0, dtype=np.float32)
        assert {int(v) for v in quantize_plain(buffer)[0]} == {128}


class TestSlowFadePrecision:
    def test_a_slow_ramp_moves_every_frame_rather_than_stepping(self):
        # Ramp within a single code over 200 frames: 128.1 to 128.4, which
        # plain rounding flattens to a constant 128. Dithered it has to remain
        # a changing mixture the whole way.
        def value_at(i: int) -> float:
            return (128.1 + 0.3 * i / 199.0) / 255.0

        dither = TemporalDither((1, 3))
        emitted = [
            int(dither.quantize(np.full((1, 3), value_at(i), np.float32))[0][0])
            for i in range(200)
        ]

        first_half = np.mean(emitted[:100])
        second_half = np.mean(emitted[100:])
        assert second_half > first_half + 0.1, "the ramp did not survive quantisation"

        undithered = {
            int(quantize_plain(np.full((1, 3), value_at(i), np.float32))[0][0])
            for i in range(200)
        }
        assert undithered == {128}, "the ramp really is sub-code, so this is a fair comparison"

    def test_a_fifteen_minute_white_fade_never_holds_a_value_for_long(self):
        # The real use case: 2700 K to 3400 K over 900 s at 60 fps. Undithered,
        # the blue channel holds each code for about ten seconds and walks up a
        # visible staircase. Dithered, no run of identical frames is long.
        a = kelvin_to_rgb(2700)
        b = kelvin_to_rgb(3400)
        dither = TemporalDither((1, 3))

        fps, seconds = 60, 60  # one minute of the fade is enough to show it
        emitted = []
        for frame in range(fps * seconds):
            mix = frame / (fps * 900.0 / 2)
            colour = (a + (b - a) * mix).astype(np.float32)
            emitted.append(int(dither.quantize(colour.reshape(1, 3))[0][2]))

        longest_run = max_run(emitted)
        assert longest_run < fps * 2, (
            f"blue held the same code for {longest_run / fps:.1f} s, which would read as a step"
        )

    def test_the_undithered_version_of_that_fade_does_step(self):
        # Establishes that the previous test is testing something real.
        a = kelvin_to_rgb(2700)
        b = kelvin_to_rgb(3400)
        fps, seconds = 60, 60
        emitted = []
        for frame in range(fps * seconds):
            mix = frame / (fps * 900.0 / 2)
            colour = (a + (b - a) * mix).astype(np.float32)
            emitted.append(int(quantize_plain(colour.reshape(1, 3))[0][2]))
        assert max_run(emitted) > fps * 4, "expected a visible staircase without dithering"


class TestRobustness:
    def test_the_error_does_not_run_away_at_a_clamped_extreme(self):
        # An overbright frame must not bank a huge residual and then dump it as
        # wrong values once the scene comes back into range.
        dither = TemporalDither((1, 3))
        stream(dither, 3.0, 200)
        recovered = stream(dither, 0.5, 20)
        assert set(recovered) <= {127, 128}

    def test_output_is_always_a_valid_byte(self):
        rng = np.random.default_rng(7)
        dither = TemporalDither((64, 3))
        for _ in range(200):
            frame = (rng.random((64, 3)) * 1.4 - 0.2).astype(np.float32)
            out = dither.quantize(frame)
            assert out.dtype == np.uint8
            assert out.min() >= 0 and out.max() <= 255

    def test_reset_clears_the_carried_residual(self):
        dither = TemporalDither((1, 3))
        stream(dither, 128.5 / 255.0, 3)
        dither.reset()
        assert stream(dither, 200.0 / 255.0, 5) == [200] * 5

    def test_each_pixel_carries_its_own_residual(self):
        dither = TemporalDither((2, 3))
        frame = np.array([[128.3 / 255] * 3, [200.0 / 255] * 3], dtype=np.float32)
        for _ in range(50):
            out = dither.quantize(frame)
            assert out[1][0] == 200, "an exact pixel picked up its neighbour's noise"

    def test_it_does_not_modify_the_frame_it_is_given(self):
        # The engine reads the frame back for its power telemetry.
        frame = np.full((4, 3), 0.503, dtype=np.float32)
        before = frame.copy()
        TemporalDither((4, 3)).quantize(frame)
        np.testing.assert_array_equal(frame, before)


def max_run(values: list[int]) -> int:
    longest = run = 1
    for previous, current in pairwise(values):
        run = run + 1 if current == previous else 1
        longest = max(longest, run)
    return longest


class TestNonFiniteFramesCannotPoisonTheAccumulator:
    """The residual is carried between frames, so one bad frame is forever.

    Nothing upstream should produce a NaN any more, but the dither is the last
    gate before eight bits and is load-bearing for this installation's main use
    case; it has to survive one anyway rather than go black until a restart.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(float("nan"), 0), (float("inf"), 255), (float("-inf"), 0)],
    )
    def test_a_non_finite_frame_is_blacked_out_or_clamped_not_propagated(self, value, expected):
        dither = TemporalDither((1, 3))
        assert stream(dither, value, 1)[0] == expected

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_later_frames_still_carry_their_full_precision(self, value):
        dither = TemporalDither((1, 3))
        dither.quantize(np.full((1, 3), value, dtype=np.float32))

        # 127.5 can only be delivered by alternating two codes, which is exactly
        # what a poisoned residual can no longer do.
        emitted = stream(dither, 127.5 / 255.0, 1000)
        assert np.mean(emitted) == pytest.approx(127.5, abs=0.02)
        assert set(emitted) == {127, 128}

    def test_plain_quantisation_is_defended_too(self):
        frame = np.array([[float("nan"), float("inf"), float("-inf")]], dtype=np.float32)
        np.testing.assert_array_equal(quantize_plain(frame), [[0, 255, 0]])

"""Colour temperature and the colour value model.

This installation is apartment lighting meant to approximate natural light, so
a kelvin value is a first-class way to name a colour and has to behave like one.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from fclights.color import (
    DEFAULT_KELVIN,
    MAX_KELVIN,
    MIN_KELVIN,
    ColorError,
    color_to_array,
    kelvin_to_rgb,
    kelvin_to_rgb255,
    parse_color,
)


class TestBlackbodyCurve:
    def test_the_offered_range_covers_candle_to_daylight(self):
        assert MIN_KELVIN == 1800.0
        assert MAX_KELVIN == 6500.0
        assert MIN_KELVIN < DEFAULT_KELVIN < MAX_KELVIN

    @pytest.mark.parametrize("kelvin", [1800, 2200, 2700, 3000, 4000, 5000, 6500])
    def test_output_is_a_normalised_in_range_triple(self, kelvin):
        rgb = kelvin_to_rgb(kelvin)
        assert rgb.shape == (3,)
        assert rgb.dtype == np.float32
        assert rgb.min() >= 0.0
        assert rgb.max() == pytest.approx(1.0), "the brightest channel should be full"

    def test_warm_temperatures_are_red_dominant(self):
        r, g, b = kelvin_to_rgb(2000)
        assert r > g > b, "candlelight should be red-heavy"

    def test_blue_rises_monotonically_with_temperature(self):
        # The defining property of the locus: cooler light has more blue in it.
        blues = [float(kelvin_to_rgb(k)[2]) for k in range(1800, 6600, 100)]
        assert all(later >= earlier - 1e-6 for earlier, later in pairwise(blues))
        assert blues[-1] > blues[0] + 0.5

    def test_red_never_drops_out_over_the_range(self):
        # Every temperature in range is a warm-ish white, not a saturated colour.
        for kelvin in range(1800, 6600, 100):
            assert kelvin_to_rgb(kelvin)[0] > 0.5

    def test_it_is_continuous_across_the_piecewise_boundaries(self):
        # The locus fit changes formula at 2222 K and 4000 K; a discontinuity
        # there would show as a visible jump partway along the slider.
        for boundary in (2222.0, 4000.0):
            below = kelvin_to_rgb(boundary - 0.5)
            above = kelvin_to_rgb(boundary + 0.5)
            np.testing.assert_allclose(below, above, atol=2e-3)

    def test_daylight_is_close_to_neutral(self):
        r, g, b = kelvin_to_rgb255(6500)
        assert max(r, g, b) - min(r, g, b) < 20, "6500 K should read as near-white"

    def test_the_255_form_rounds_the_float_form(self):
        np.testing.assert_allclose(
            np.array(kelvin_to_rgb255(3000)) / 255.0, kelvin_to_rgb(3000), atol=1 / 255
        )

    def test_nearby_temperatures_differ_by_only_a_few_codes(self):
        # This is the case the whole dithering story exists for: 2700 K and
        # 2900 K are only a handful of 8-bit codes apart.
        a = np.array(kelvin_to_rgb255(2700))
        b = np.array(kelvin_to_rgb255(2900))
        assert 0 < int(np.abs(a - b).max()) < 40


class TestColorParsing:
    def test_rgb_list(self):
        assert parse_color([255, 170, 80]) == {"mode": "rgb", "rgb": [255, 170, 80]}

    def test_rgb_components_are_clamped_and_rounded(self):
        assert parse_color([300, -5, 12.6])["rgb"] == [255, 0, 13]

    def test_hex_long_and_short(self):
        assert parse_color("#ff8000")["rgb"] == [255, 128, 0]
        assert parse_color("#f80")["rgb"] == [255, 136, 0]
        assert parse_color("FF8000")["rgb"] == [255, 128, 0]

    def test_kelvin_object(self):
        parsed = parse_color({"kelvin": 2700})
        assert parsed["mode"] == "kelvin"
        assert parsed["kelvin"] == 2700.0
        assert parsed["rgb"] == kelvin_to_rgb255(2700)

    def test_a_kelvin_colour_carries_its_rgb_for_a_swatch(self):
        # So a client can draw the colour without reimplementing the blackbody
        # maths, while still knowing where to put the warm-to-cool slider.
        parsed = parse_color({"kelvin": 3000})
        assert set(parsed) == {"mode", "kelvin", "rgb"}

    def test_canonical_objects_round_trip(self):
        for value in ([255, 170, 80], "#ff8000", {"kelvin": 2700}):
            once = parse_color(value)
            assert parse_color(once) == once

    def test_kelvin_outside_the_range_is_refused(self):
        for kelvin in (1000, 1799, 6501, 20000):
            with pytest.raises(ColorError, match="range"):
                parse_color({"kelvin": kelvin})

    def test_kelvin_must_be_a_number(self):
        with pytest.raises(ColorError, match="must be a number"):
            parse_color({"kelvin": "warm"})
        with pytest.raises(ColorError, match="must be a number"):
            parse_color({"kelvin": True})

    def test_malformed_values_are_refused(self):
        for bad in ("#12345", "orange", [1, 2], [1, 2, 3, 4], 42, None, {}, {"nope": 1}):
            with pytest.raises(ColorError):
                parse_color(bad)

    def test_booleans_are_not_colour_components(self):
        with pytest.raises(ColorError, match=r"numbers 0\.\.255"):
            parse_color([True, 0, 0])

    def test_the_error_names_the_field(self):
        with pytest.raises(ColorError, match="background"):
            parse_color("nope", field="background")


class TestColorToArray:
    def test_rgb_scales_to_unit_range(self):
        np.testing.assert_allclose(
            color_to_array({"mode": "rgb", "rgb": [255, 128, 0]}),
            [1.0, 128 / 255, 0.0],
            atol=1e-6,
        )

    def test_kelvin_is_recomputed_at_full_float_precision(self):
        # Not read back from the stored 8-bit rgb: a slow fade between two
        # nearby whites needs more resolution than 8 bits to interpolate over.
        parsed = parse_color({"kelvin": 2750})
        np.testing.assert_allclose(color_to_array(parsed), kelvin_to_rgb(2750))

    def test_two_nearby_temperatures_are_distinguishable_in_float(self):
        a = color_to_array(parse_color({"kelvin": 2700}))
        b = color_to_array(parse_color({"kelvin": 2705}))
        assert not np.array_equal(a, b), "5 K apart should not collapse to one value"

    def test_output_is_float32_in_range(self):
        for value in ([255, 170, 80], {"kelvin": 4000}):
            array = color_to_array(parse_color(value))
            assert array.dtype == np.float32
            assert array.min() >= 0.0 and array.max() <= 1.0

"""Effect tests.

Two things matter here.  First, that every registered effect behaves - fills the
whole buffer, stays in range, produces finite numbers - because a NaN reaching
the encoder becomes a random 8-bit value on the strip.  Second, that the
parameter schemas are honest, since the Android app builds its controls from
them and cannot check them itself.
"""

from __future__ import annotations

import numpy as np
import pytest

from fclights import effects
from fclights.effects.base import ParamError, hsv_to_rgb_array
from fclights.layout import simple_layout

ALL_EFFECTS = effects.all_effects()
EFFECT_IDS = [e.name for e in ALL_EFFECTS]


@pytest.fixture(params=ALL_EFFECTS, ids=EFFECT_IDS)
def effect_cls(request):
    return request.param


def render_sequence(effect, pixel_count: int, frames: int = 30, fps: float = 60.0):
    """Render a run of frames and return them stacked."""
    buffer = np.zeros((pixel_count, 3), dtype=np.float32)
    dt = 1.0 / fps
    out = []
    for i in range(frames):
        effect.render(buffer, i * dt, dt)
        out.append(buffer.copy())
    return np.stack(out)


class TestEveryEffect:
    def test_renders_finite_values_in_range(self, effect_cls, layout):
        effect = effect_cls(layout, effect_cls.defaults())
        rendered = render_sequence(effect, layout.pixel_count, frames=120)

        assert np.isfinite(rendered).all(), f"{effect_cls.name} produced NaN or inf"
        assert rendered.min() >= -1e-6, f"{effect_cls.name} produced negative values"
        assert rendered.max() <= 1.0 + 1e-6, f"{effect_cls.name} produced values above 1"

    def test_fills_the_whole_buffer(self, effect_cls, layout):
        # The engine reuses the frame buffer between frames and does not clear
        # it, so an effect that only writes some pixels would leave the rest
        # showing the previous effect.
        buffer = np.full((layout.pixel_count, 3), -7.0, dtype=np.float32)
        effect = effect_cls(layout, effect_cls.defaults())
        effect.render(buffer, 0.0, 1 / 60)
        assert not (buffer == -7.0).any(), f"{effect_cls.name} left pixels untouched"

    def test_output_shape_and_dtype_survive_rendering(self, effect_cls, layout):
        buffer = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        effect = effect_cls(layout, effect_cls.defaults())
        effect.render(buffer, 1.0, 1 / 60)
        assert buffer.shape == (layout.pixel_count, 3)
        assert buffer.dtype == np.float32

    def test_works_on_a_single_pixel_layout(self, effect_cls):
        # Degenerate layouts are where divide-by-span bugs surface.
        tiny = simple_layout(1)
        effect = effect_cls(tiny, effect_cls.defaults())
        rendered = render_sequence(effect, 1, frames=10)
        assert np.isfinite(rendered).all()

    def test_zero_dt_does_not_break_anything(self, effect_cls, layout):
        # Two renders can land in the same instant on a coarse clock.
        effect = effect_cls(layout, effect_cls.defaults())
        buffer = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        effect.render(buffer, 0.0, 0.0)
        effect.render(buffer, 0.0, 0.0)
        assert np.isfinite(buffer).all()

    def test_extreme_parameter_values_stay_in_range(self, effect_cls, layout):
        # Drive every numeric knob to both of its declared limits.
        for pick in (lambda s: s.minimum, lambda s: s.maximum):
            params = effect_cls.defaults()
            for spec in effect_cls.params:
                limit = pick(spec)
                if spec.type in {"float", "int"} and limit is not None:
                    params[spec.name] = int(limit) if spec.type == "int" else limit
            effect = effect_cls(layout, effect_cls.coerce_params(params))
            rendered = render_sequence(effect, layout.pixel_count, frames=20)
            assert np.isfinite(rendered).all(), f"{effect_cls.name} at limits produced NaN"
            assert rendered.min() >= -1e-6 and rendered.max() <= 1.0 + 1e-6


class TestSchemas:
    def test_every_effect_is_discoverable(self):
        names = effects.names()
        assert {
            "solid",
            "slowfade",
            "gradient",
            "breathe",
            "wipe",
            "rainbow",
            "twinkle",
            "fire",
        } <= set(names)

    def test_colour_controls_advertise_a_kelvin_slider(self, effect_cls):
        # The Android app builds a warm-to-cool slider from this, which for
        # apartment lighting is the control that gets used.
        for param in effect_cls.schema()["params"]:
            if param["type"] == "color":
                assert param["supports_kelvin"] is True
                low, high = param["kelvin_range"]
                assert low < param["kelvin_default"] < high

    def test_colour_defaults_are_canonical_objects(self, effect_cls):
        # So the schema a client reads and the state it reads back agree.
        for param in effect_cls.schema()["params"]:
            if param["type"] == "color":
                assert set(param["default"]) >= {"mode", "rgb"}

    def test_schema_is_json_serialisable(self, effect_cls):
        import json

        json.dumps(effect_cls.schema())

    def test_defaults_satisfy_their_own_declared_ranges(self, effect_cls):
        # The Android app trusts these; a default outside its own range would
        # render a slider already out of bounds.
        for spec in effect_cls.params:
            assert spec.coerce(spec.default) is not None or spec.default is not None

    def test_defaults_round_trip_through_coercion(self, effect_cls):
        assert effect_cls.coerce_params({}) == effect_cls.defaults()

    def test_schema_declares_a_type_the_client_can_render(self, effect_cls):
        for spec in effect_cls.params:
            assert spec.type in {"float", "int", "bool", "color", "enum"}
            if spec.type == "enum":
                assert spec.choices, f"{effect_cls.name}.{spec.name} is an enum with no choices"
                assert spec.default in spec.choices
            if spec.type in {"float", "int"}:
                assert spec.minimum is not None and spec.maximum is not None, (
                    f"{effect_cls.name}.{spec.name} is numeric but declares no range, "
                    "so a client cannot build a slider for it"
                )
                assert spec.minimum <= spec.default <= spec.maximum


class TestParameterCoercion:
    def test_unknown_parameters_are_rejected(self):
        with pytest.raises(ParamError, match="no parameter"):
            effects.get("solid").coerce_params({"colour": [1, 2, 3]})

    def test_out_of_range_values_are_rejected(self):
        with pytest.raises(ParamError, match="above maximum"):
            effects.get("rainbow").coerce_params({"saturation": 4.0})
        with pytest.raises(ParamError, match="below minimum"):
            effects.get("rainbow").coerce_params({"saturation": -1.0})

    def test_bad_enum_choice_is_rejected(self):
        with pytest.raises(ParamError, match="not one of"):
            effects.get("rainbow").coerce_params({"axis": "diagonal"})

    def test_booleans_are_not_accepted_as_numbers(self):
        # bool subclasses int in Python; accepting True as 1 would hide a
        # client bug rather than reporting it.
        with pytest.raises(ParamError, match="expected a number"):
            effects.get("rainbow").coerce_params({"speed": True})

    def test_non_numeric_values_are_rejected(self):
        with pytest.raises(ParamError, match="expected a number"):
            effects.get("rainbow").coerce_params({"speed": "fast"})

    def test_hex_colours_are_accepted(self):
        solid = effects.get("solid")
        assert solid.coerce_params({"color": "#ff8000"})["color"] == {
            "mode": "rgb",
            "rgb": [255, 128, 0],
        }
        assert solid.coerce_params({"color": "#f80"})["color"]["rgb"] == [255, 136, 0]

    def test_rgb_lists_are_accepted_and_clamped(self):
        coerced = effects.get("solid").coerce_params({"color": [300, -5, 12.6]})
        assert coerced["color"] == {"mode": "rgb", "rgb": [255, 0, 13]}

    def test_malformed_colours_are_rejected(self):
        for bad in ("#12345", "orange", [1, 2], [1, 2, 3, 4], 42):
            with pytest.raises(ParamError):
                effects.get("solid").coerce_params({"color": bad})

    def test_ints_are_rounded_not_truncated(self):
        assert effects.get("twinkle").coerce_params({"seed": 7.6})["seed"] == 8

    def test_partial_params_fall_back_to_defaults(self):
        coerced = effects.get("gradient").coerce_params({"speed": 0.9})
        assert coerced["speed"] == 0.9
        assert coerced["color_a"] == effects.get("gradient").defaults()["color_a"]


class TestSpecificBehaviour:
    def test_solid_defaults_to_a_warm_white(self, small_layout):
        # This is apartment lighting, not a display piece; out of the box it
        # should look like a lamp, not a saturated colour.
        default = effects.get("solid").defaults()["color"]
        assert default["mode"] == "kelvin"
        assert 2000 <= default["kelvin"] <= 3500

    def test_colours_can_be_named_as_a_temperature(self, small_layout):
        from fclights.color import kelvin_to_rgb

        cls = effects.get("solid")
        effect = cls(small_layout, cls.coerce_params({"color": {"kelvin": 2700}}))
        buffer = np.zeros((small_layout.pixel_count, 3), dtype=np.float32)
        effect.render(buffer, 0.0, 1 / 60)
        np.testing.assert_allclose(buffer[0], kelvin_to_rgb(2700), atol=1e-6)

    def test_a_warmer_setting_really_is_warmer(self, small_layout):
        cls = effects.get("solid")
        buffer = np.zeros((small_layout.pixel_count, 3), dtype=np.float32)

        cls(small_layout, cls.coerce_params({"color": {"kelvin": 2000}})).render(
            buffer, 0.0, 1 / 60
        )
        warm_blue = float(buffer[0][2])
        cls(small_layout, cls.coerce_params({"color": {"kelvin": 6000}})).render(
            buffer, 0.0, 1 / 60
        )
        assert float(buffer[0][2]) > warm_blue

    def test_slowfade_crosses_between_its_two_colours(self, small_layout):
        from fclights.color import kelvin_to_rgb

        cls = effects.get("slowfade")
        effect = cls(
            small_layout,
            cls.coerce_params(
                {
                    "color_a": {"kelvin": 2700},
                    "color_b": {"kelvin": 5000},
                    "period": 100.0,
                    "hold": 0.0,
                }
            ),
        )
        buffer = np.zeros((small_layout.pixel_count, 3), dtype=np.float32)

        effect.render(buffer, 0.0, 1 / 60)
        np.testing.assert_allclose(buffer[0], kelvin_to_rgb(2700), atol=2e-3)
        effect.render(buffer, 50.0, 1 / 60)
        np.testing.assert_allclose(buffer[0], kelvin_to_rgb(5000), atol=2e-3)
        effect.render(buffer, 100.0, 1 / 60)
        np.testing.assert_allclose(buffer[0], kelvin_to_rgb(2700), atol=2e-3)

    def test_slowfade_moves_in_float_between_every_frame(self, small_layout):
        # The primary use case: a fifteen minute fade between near-identical
        # whites. Consecutive frames must differ, or the effect has quantised
        # before the dither at the encoder ever gets a chance.
        cls = effects.get("slowfade")
        effect = cls(
            small_layout,
            cls.coerce_params(
                {"color_a": {"kelvin": 2700}, "color_b": {"kelvin": 2900},
                 "period": 900.0, "hold": 0.0}
            ),
        )
        a = np.zeros((small_layout.pixel_count, 3), dtype=np.float32)
        b = np.zeros((small_layout.pixel_count, 3), dtype=np.float32)
        effect.render(a, 200.0, 1 / 60)
        effect.render(b, 200.0 + 1 / 60, 1 / 60)
        assert not np.array_equal(a, b), "the fade stalled between consecutive frames"

    def test_slowfade_dwell_parks_at_each_end(self, small_layout):
        cls = effects.get("slowfade")
        effect = cls(small_layout, cls.coerce_params({"period": 100.0, "hold": 0.4}))
        assert effect._mix_at(0.0) == 0.0
        assert effect._mix_at(4.0) == 0.0, "should still be parked at the near end"
        assert effect._mix_at(50.0) == pytest.approx(1.0)
        assert effect._mix_at(54.0) == pytest.approx(1.0), "should be parked at the far end"

    def test_slowfade_with_no_dwell_leaves_each_end_immediately(self, small_layout):
        cls = effects.get("slowfade")
        effect = cls(small_layout, cls.coerce_params({"period": 100.0, "hold": 0.0}))
        assert effect._mix_at(0.0) == 0.0
        assert effect._mix_at(5.0) > 0.0

    def test_slowfade_is_smooth_at_the_turnaround(self, small_layout):
        # A kink at the top of the cycle is exactly what a slow fade must not do.
        cls = effects.get("slowfade")
        effect = cls(
            small_layout, cls.coerce_params({"period": 100.0, "hold": 0.0, "easing": "smooth"})
        )
        around = [effect._mix_at(t) for t in np.linspace(49.0, 51.0, 41)]
        steps = np.abs(np.diff(around))
        assert steps.max() < 0.01, "the crossfade changes direction abruptly"

    def test_slowfade_supports_a_multi_hour_cycle(self, small_layout):
        cls = effects.get("slowfade")
        spec = next(p for p in cls.params if p.name == "period")
        assert spec.maximum >= 3600.0, "a fade should be able to run for hours"

    def test_solid_is_uniform_and_matches_the_requested_colour(self, small_layout):
        cls = effects.get("solid")
        effect = cls(small_layout, cls.coerce_params({"color": [255, 128, 0]}))
        buffer = np.zeros((small_layout.pixel_count, 3), dtype=np.float32)
        effect.render(buffer, 0.0, 1 / 60)

        assert np.allclose(buffer, buffer[0])
        np.testing.assert_allclose(buffer[0], [1.0, 128 / 255, 0.0], atol=1e-6)

    def test_solid_does_not_animate(self, small_layout):
        cls = effects.get("solid")
        effect = cls(small_layout, cls.coerce_params({}))
        rendered = render_sequence(effect, small_layout.pixel_count, frames=10)
        assert np.allclose(rendered[0], rendered[-1])

    def test_gradient_reaches_both_colours_across_one_cycle(self, layout):
        # One cycle is colour A to colour B and back, so A sits at both ends and
        # B in the middle. That symmetry is what keeps the seam invisible when
        # the gradient slides.
        cls = effects.get("gradient")
        effect = cls(
            layout,
            cls.coerce_params(
                {"color_a": [255, 0, 0], "color_b": [0, 0, 255], "speed": 0.0, "cycles": 1.0}
            ),
        )
        buffer = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        effect.render(buffer, 0.0, 1 / 60)

        np.testing.assert_allclose(buffer[0], [1.0, 0.0, 0.0], atol=1e-3)
        np.testing.assert_allclose(buffer[-1], [1.0, 0.0, 0.0], atol=1e-2)
        np.testing.assert_allclose(buffer[layout.pixel_count // 2], [0.0, 0.0, 1.0], atol=1e-2)

    def test_gradient_slides_without_a_seam(self, layout):
        # Sliding by exactly one cycle must land back on the same picture.
        cls = effects.get("gradient")
        effect = cls(layout, cls.coerce_params({"speed": 1.0, "cycles": 1.0}))
        a = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        b = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        effect.render(a, 0.0, 1 / 60)
        effect.render(b, 1.0, 1 / 60)
        np.testing.assert_allclose(a, b, atol=1e-5)

    def test_gradient_is_position_aware_not_index_aware(self, layout):
        # Rendering along the x axis of a straight strip must agree with
        # rendering along the run, or "spatial" means nothing.
        cls = effects.get("gradient")
        by_run = cls(layout, cls.coerce_params({"speed": 0.0, "axis": "run"}))
        by_x = cls(layout, cls.coerce_params({"speed": 0.0, "axis": "x"}))

        a = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        b = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        by_run.render(a, 0.0, 1 / 60)
        by_x.render(b, 0.0, 1 / 60)
        np.testing.assert_allclose(a, b, atol=2e-3)

    def test_breathe_actually_varies_over_time(self, small_layout):
        cls = effects.get("breathe")
        effect = cls(small_layout, cls.coerce_params({"speed": 1.0, "minimum": 0.0}))
        rendered = render_sequence(effect, small_layout.pixel_count, frames=60)
        per_frame = rendered.mean(axis=(1, 2))
        assert per_frame.max() - per_frame.min() > 0.2

    def test_breathe_respects_its_brightness_bounds(self, small_layout):
        cls = effects.get("breathe")
        effect = cls(
            small_layout,
            cls.coerce_params(
                {"color": [255, 255, 255], "speed": 2.0, "minimum": 0.25, "maximum": 0.75}
            ),
        )
        rendered = render_sequence(effect, small_layout.pixel_count, frames=90)
        assert rendered.max() <= 0.75 + 1e-5
        assert rendered.min() >= 0.25 - 1e-5

    def test_breathe_tolerates_inverted_bounds(self, small_layout):
        cls = effects.get("breathe")
        effect = cls(small_layout, cls.coerce_params({"minimum": 0.9, "maximum": 0.1}))
        rendered = render_sequence(effect, small_layout.pixel_count, frames=30)
        assert np.isfinite(rendered).all()

    def test_wipe_front_advances_along_the_run(self, layout):
        cls = effects.get("wipe")
        effect = cls(
            layout,
            cls.coerce_params(
                {"color": [255, 255, 255], "background": [0, 0, 0], "speed": 1.0}
            ),
        )
        buffer = np.zeros((layout.pixel_count, 3), dtype=np.float32)

        effect.render(buffer, 0.25, 1 / 60)
        lit_early = int((buffer.mean(axis=1) > 0.5).sum())
        effect.render(buffer, 0.75, 1 / 60)
        lit_late = int((buffer.mean(axis=1) > 0.5).sum())

        assert 0 < lit_early < lit_late <= layout.pixel_count

    def test_wipe_bounce_reverses(self, layout):
        cls = effects.get("wipe")
        effect = cls(layout, cls.coerce_params({"speed": 1.0, "bounce": True}))
        buffer = np.zeros((layout.pixel_count, 3), dtype=np.float32)

        # The front reaches the far end at t=1.0 and retreats after it, so the
        # lit fraction must fall away on the return leg.
        effect.render(buffer, 1.0, 1 / 60)
        at_peak = buffer.mean()
        effect.render(buffer, 1.4, 1 / 60)
        retreating = buffer.mean()
        effect.render(buffer, 1.9, 1 / 60)
        nearly_home = buffer.mean()
        assert nearly_home < retreating < at_peak

    def test_rainbow_covers_the_hue_circle(self, layout):
        cls = effects.get("rainbow")
        effect = cls(layout, cls.coerce_params({"speed": 0.0, "cycles": 1.0}))
        buffer = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        effect.render(buffer, 0.0, 1 / 60)

        # Somewhere along the run each channel should peak and each should bottom.
        assert buffer.max(axis=0).min() > 0.95
        assert buffer.min(axis=0).max() < 0.05

    def test_rainbow_at_zero_saturation_is_white(self, small_layout):
        cls = effects.get("rainbow")
        effect = cls(small_layout, cls.coerce_params({"saturation": 0.0}))
        buffer = np.zeros((small_layout.pixel_count, 3), dtype=np.float32)
        effect.render(buffer, 0.3, 1 / 60)
        np.testing.assert_allclose(buffer, 1.0, atol=1e-5)

    def test_twinkle_is_reproducible_for_a_given_seed(self, layout):
        cls = effects.get("twinkle")
        params = cls.coerce_params({"seed": 99, "density": 40.0})
        a = render_sequence(cls(layout, params), layout.pixel_count, frames=40)
        b = render_sequence(cls(layout, params), layout.pixel_count, frames=40)
        np.testing.assert_allclose(a, b)

    def test_twinkle_lights_pixels_and_lets_them_fade(self, layout):
        cls = effects.get("twinkle")
        effect = cls(
            layout,
            cls.coerce_params(
                {"seed": 7, "density": 200.0, "decay": 0.2, "background": [0, 0, 0]}
            ),
        )
        rendered = render_sequence(effect, layout.pixel_count, frames=60)
        assert rendered.max() > 0.5, "no spark ever lit"

        # Stop sparking and confirm the strip actually decays away.
        quiet = cls(layout, cls.coerce_params({"seed": 7, "density": 0.0, "decay": 0.2,
                                               "background": [0, 0, 0]}))
        faded = render_sequence(quiet, layout.pixel_count, frames=120)
        assert faded[-1].max() < 1e-3

    def test_twinkle_jitter_moves_the_hue_and_nothing_else(self, layout):
        # color_jitter is documented as a hue spread, so a jittered spark must
        # keep the chosen colour's brightness rather than igniting at full duty.
        cls = effects.get("twinkle")
        half_red = {"color": [128, 0, 0], "background": [0, 0, 0], "seed": 5,
                    "density": 200.0, "decay": 0.2}
        peak = 128 / 255

        jittered = render_sequence(
            cls(layout, cls.coerce_params({**half_red, "color_jitter": 0.2})),
            layout.pixel_count,
            frames=60,
        )
        assert jittered.max() > 0.4, "no spark ever lit"
        assert jittered.max() <= peak + 1e-5

        plain = render_sequence(
            cls(layout, cls.coerce_params({**half_red, "color_jitter": 0.0})),
            layout.pixel_count,
            frames=60,
        )
        assert jittered.max() == pytest.approx(plain.max(), abs=1e-5)

    def test_twinkle_with_zero_density_shows_only_the_background(self, small_layout):
        cls = effects.get("twinkle")
        effect = cls(
            small_layout,
            cls.coerce_params({"density": 0.0, "background": [10, 20, 30], "seed": 1}),
        )
        buffer = np.zeros((small_layout.pixel_count, 3), dtype=np.float32)
        effect.render(buffer, 0.0, 1 / 60)
        np.testing.assert_allclose(buffer[0], [10 / 255, 20 / 255, 30 / 255], atol=1e-6)

    def test_fire_warms_up_and_stays_bounded(self, layout):
        cls = effects.get("fire")
        effect = cls(layout, cls.coerce_params({"seed": 3}))
        rendered = render_sequence(effect, layout.pixel_count, frames=180)
        assert rendered[-30:].max() > 0.2, "fire never caught"
        assert rendered.max() <= 1.0 + 1e-6

    def test_fire_burns_each_output_separately_when_asked(self, layout):
        # Per-segment fire should be hot near the base of every output, not just
        # at the very start of the whole run.
        cls = effects.get("fire")
        effect = cls(
            layout, cls.coerce_params({"seed": 5, "per_segment": True, "sparking": 1.0})
        )
        rendered = render_sequence(effect, layout.pixel_count, frames=200)
        brightness = rendered[-60:].mean(axis=(0, 2))

        for segment in range(layout.segment_count):
            in_segment = layout.segment == segment
            assert brightness[in_segment].max() > 0.05, f"output {segment} never lit"

    def test_fire_with_no_sparking_burns_out(self, small_layout):
        cls = effects.get("fire")
        effect = cls(small_layout, cls.coerce_params({"sparking": 0.0, "cooling": 1.0}))
        rendered = render_sequence(effect, small_layout.pixel_count, frames=200)
        assert rendered[-1].max() < 1e-3


class TestHSVHelper:
    def test_matches_colorsys_for_scalar_hues(self):
        import colorsys

        hues = np.linspace(0, 1, 25, endpoint=False)
        got = hsv_to_rgb_array(hues, 1.0, 1.0)
        want = np.array([colorsys.hsv_to_rgb(float(h), 1.0, 1.0) for h in hues])
        np.testing.assert_allclose(got, want, atol=1e-6)

    def test_hue_wraps(self):
        np.testing.assert_allclose(
            hsv_to_rgb_array(np.array([0.25]), 1.0, 1.0),
            hsv_to_rgb_array(np.array([3.25]), 1.0, 1.0),
            atol=1e-6,
        )
        np.testing.assert_allclose(
            hsv_to_rgb_array(np.array([0.25]), 1.0, 1.0),
            hsv_to_rgb_array(np.array([-0.75]), 1.0, 1.0),
            atol=1e-6,
        )

    def test_zero_value_is_black(self):
        np.testing.assert_allclose(hsv_to_rgb_array(np.linspace(0, 1, 7), 1.0, 0.0), 0.0)


class TestRegistry:
    def test_unknown_effect_names_report_what_is_available(self):
        with pytest.raises(effects.UnknownEffectError) as excinfo:
            effects.get("disco")
        assert "rainbow" in str(excinfo.value)

    def test_registering_a_duplicate_name_is_rejected(self):
        class Clash(effects.Effect):
            name = "solid"

            def render(self, frame, t, dt):
                frame[:] = 0

        with pytest.raises(ValueError, match="already registered"):
            effects.register(Clash)

    def test_registering_the_same_class_twice_is_harmless(self):
        effects.register(effects.get("solid"))

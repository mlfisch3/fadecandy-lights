"""Engine tests, including the end-to-end path through a real OPC socket.

The end-to-end tests are the closest thing we have to hardware verification:
the engine renders, the governor clamps, the frame is encoded, and it crosses a
real TCP socket into a receiver that validates the protocol.  Everything up to
the USB link is covered; nothing beyond it is, and nothing here should be read
as evidence that a strip lights up.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from itertools import pairwise

import numpy as np
import pytest

from fclights import effects
from fclights.config import PowerConfig
from fclights.engine import Engine
from fclights.opc import OPC_HEADER_BYTES, NullSink, OPCClient
from fclights.power import PowerGovernor
from fclights.state import default_state
from opc_sink import RecordingOPCServer


def engine_with(layout, config, sink=None, **state_kwargs):
    engine = Engine(layout, sink or NullSink(), config)
    engine.apply_state(replace(default_state(), revision=1, **state_kwargs))
    return engine


class TestFramePipeline:
    def test_brightness_scales_the_frame(self, layout, config):
        engine = engine_with(
            layout, config, effect="solid", params={"color": [255, 255, 255]}, brightness=0.25
        )
        engine.render_frame(1 / 60)
        np.testing.assert_allclose(engine.frame, 0.25, atol=1e-6)

    def test_master_off_blacks_the_frame(self, layout, config):
        engine = engine_with(
            layout,
            config,
            power=False,
            effect="solid",
            params={"color": [255, 255, 255]},
            brightness=1.0,
        )
        engine.render_frame(1 / 60)
        assert engine.frame.max() == 0.0

    def test_the_governor_clamps_inside_the_pipeline(self, layout, config):
        # This is the assertion the whole safety story rests on: not that the
        # governor can clamp, but that the engine actually runs it.
        config = replace(config, power=PowerConfig(limit_amps=2.0))
        engine = engine_with(
            layout, config, effect="solid", params={"color": [255, 255, 255]}, brightness=1.0
        )
        report = engine.render_frame(1 / 60)

        assert report.clamped is True
        assert engine.governor.predict_amps(engine.frame) <= 2.0 + 1e-6
        assert engine.frame.max() < 1.0

    def test_the_governor_runs_even_when_the_effect_misbehaves(self, layout, config):
        # An effect writing out-of-range values must not be able to get past
        # the clamp; the engine clips before the governor sees the frame.
        class Overbright(effects.Effect):
            name = "test_overbright"

            def render(self, frame, t, dt):
                frame[:] = 12.0

        effects.register(Overbright)
        try:
            config = replace(config, power=PowerConfig(limit_amps=3.0))
            engine = engine_with(
                layout, config, effect="test_overbright", params={}, brightness=1.0
            )
            engine.render_frame(1 / 60)
            assert engine.governor.predict_amps(engine.frame) <= 3.0 + 1e-6
        finally:
            effects._REGISTRY.pop("test_overbright", None)

    def test_a_frame_that_fits_is_not_touched(self, layout, config):
        config = replace(config, power=PowerConfig(limit_amps=40.0))
        engine = engine_with(
            layout, config, effect="solid", params={"color": [255, 255, 255]}, brightness=1.0
        )
        report = engine.render_frame(1 / 60)
        assert report.clamped is False
        np.testing.assert_allclose(engine.frame, 1.0)

    def test_gamma_is_not_applied_by_the_engine(self, layout, config):
        # It belongs to fcserver. Applying it here as well would double-correct
        # and crush exactly the low end the Fadecandy's dithering exists to save.
        config = replace(config, power=PowerConfig(limit_amps=40.0))
        engine = engine_with(
            layout, config, effect="solid", params={"color": [128, 128, 128]}, brightness=1.0
        )
        engine.render_frame(1 / 60)
        encoded = engine.encode()[0]
        assert encoded[OPC_HEADER_BYTES] == 128

    def test_effect_changes_are_picked_up(self, layout, config):
        engine = engine_with(layout, config, effect="solid", params={"color": [255, 0, 0]})
        engine.render_frame(1 / 60)
        assert engine.frame[0][0] > engine.frame[0][2]

        engine.apply_state(
            replace(engine._state, effect="solid", params={"color": [0, 0, 255]}, revision=2)
        )
        engine.render_frame(1 / 60)
        assert engine.frame[0][2] > engine.frame[0][0]

    def test_a_broken_effect_does_not_take_the_lights_down(self, layout, config):
        engine = engine_with(layout, config, effect="solid", params={"color": [255, 0, 0]})
        engine.render_frame(1 / 60)
        good = engine.frame.copy()

        # An impossible state, as if a newer client sent something we cannot build.
        engine.apply_state(replace(engine._state, effect="nonexistent", revision=99))
        engine.render_frame(1 / 60)
        np.testing.assert_allclose(engine.frame, good, atol=1e-6)

    def test_animation_time_advances_with_dt(self, layout, config):
        engine = engine_with(layout, config, effect="rainbow", params={"speed": 1.0})
        engine.render_frame(0.5)
        first = engine.frame.copy()
        engine.render_frame(0.5)
        assert not np.allclose(first, engine.frame)

    def test_animation_time_does_not_advance_while_off(self, layout, config):
        engine = engine_with(layout, config, power=False, effect="rainbow")
        engine.render_frame(10.0)
        assert engine._animation_time == 0.0


class TestEncoding:
    def test_one_message_per_device_channel(self, layout, config):
        engine = engine_with(layout, config)
        engine.render_frame(1 / 60)
        messages = engine.encode()
        assert len(messages) == 1
        assert messages[0][0] == 0
        assert len(messages[0]) == OPC_HEADER_BYTES + 512 * 3

    def test_multi_device_layouts_produce_one_message_each(self, config):
        from fclights.layout import build_layout

        layout = build_layout(
            {
                "devices": [
                    {"id": "a", "opc_channel": 1, "outputs": [{"index": 0, "count": 64}]},
                    {"id": "b", "opc_channel": 2, "outputs": [{"index": 0, "count": 64}]},
                ]
            }
        )
        engine = engine_with(layout, config)
        engine.render_frame(1 / 60)
        messages = engine.encode()
        assert [m[0] for m in messages] == [1, 2]


class TestEndToEndOverASocket:
    async def test_frames_reach_a_real_opc_receiver(self, layout, config):
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, replace(config, simulate=False))
            engine.apply_state(
                replace(
                    default_state(),
                    revision=1,
                    effect="solid",
                    params={"color": [255, 128, 0]},
                    brightness=1.0,
                )
            )
            for _ in range(3):
                await engine.render_once(1 / 60)
            received = await sink.wait_for_frames(3)
            await engine.stop()

        sink.assert_clean()
        assert len(received) >= 3
        for frame in received:
            assert frame.pixel_count == 512
            assert frame.channel == 0

        # The colour that arrived is the colour we asked for, clamped exactly as
        # an independent governor over the same buffer would clamp it.
        governor = PowerGovernor(limit_amps=config.power.limit_amps, pixel_count=512)
        expected = np.tile([1.0, 128 / 255, 0.0], (512, 1)).astype(np.float32)
        governor.apply(expected)

        arrived = received[-1].pixels.astype(float) / 255.0
        np.testing.assert_allclose(arrived, expected, atol=1 / 255)
        assert arrived[0][1] < arrived[0][0]
        assert arrived[0][2] == 0

    async def test_the_running_loop_delivers_frames_at_the_configured_rate(self, layout, config):
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, replace(config, fps=30.0, simulate=False))
            engine.apply_state(replace(default_state(), revision=1))
            engine.start()
            try:
                await sink.wait_for_frames(10, timeout=5.0)
            finally:
                await engine.stop()

        sink.assert_clean()
        assert engine.stats.frames_rendered >= 10
        # Generous bounds: CI timing is noisy, and the point is that it runs at
        # roughly the rate asked for rather than free-running or stalling.
        assert 15 <= engine.stats.measured_fps <= 45 or engine.stats.measured_fps == 0

    async def test_the_loop_survives_the_sink_going_away(self, layout, config):
        # fcserver restarting must not stop the render loop; the rig has to
        # reconnect on its own.
        sink = await RecordingOPCServer().start()
        client = OPCClient("127.0.0.1", sink.port, min_retry=0.01, max_retry=0.05)
        engine = Engine(layout, client, replace(config, fps=60.0, simulate=False))
        engine.apply_state(replace(default_state(), revision=1))
        engine.start()
        try:
            await sink.wait_for_frames(3, timeout=5.0)
            await sink.stop()
            await asyncio.sleep(0.3)
            rendered_while_down = engine.stats.frames_rendered
            assert rendered_while_down > 0

            revived = RecordingOPCServer(port=sink.port)
            await revived.start()
            try:
                await revived.wait_for_frames(3, timeout=5.0)
            finally:
                await revived.stop()
        finally:
            await engine.stop()

        assert engine.stats.frames_rendered > rendered_while_down

    async def test_all_seven_effects_survive_the_full_pipeline(self, layout, config):
        # The integration counterpart to the per-effect unit tests: every effect
        # rendered, clamped, encoded and pushed across a socket into a validator.
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, replace(config, simulate=False))
            for index, effect_cls in enumerate(effects.all_effects()):
                engine.apply_state(
                    replace(
                        default_state(),
                        revision=index + 1,
                        effect=effect_cls.name,
                        params=effect_cls.defaults(),
                        brightness=1.0,
                    )
                )
                for _ in range(5):
                    await engine.render_once(1 / 60)
            expected = 5 * len(effects.all_effects())
            received = await sink.wait_for_frames(expected)
            await engine.stop()

        sink.assert_clean()
        assert len(received) >= expected
        for frame in received:
            assert frame.pixel_count == 512

    async def test_no_frame_ever_exceeds_the_power_ceiling_over_a_long_run(self, layout, config):
        # The safety property stated over the wire rather than in the engine:
        # decode what actually arrived and check the current it implies.
        config = replace(config, power=PowerConfig(limit_amps=3.0))
        governor = PowerGovernor(limit_amps=3.0, pixel_count=512)

        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, replace(config, simulate=False))
            for index, effect_cls in enumerate(effects.all_effects()):
                engine.apply_state(
                    replace(
                        default_state(),
                        revision=index + 1,
                        effect=effect_cls.name,
                        params=effect_cls.defaults(),
                        brightness=1.0,
                    )
                )
                for _ in range(20):
                    await engine.render_once(1 / 60)
            received = await sink.wait_for_frames(20 * len(effects.all_effects()))
            await engine.stop()

        sink.assert_clean()
        for frame in received:
            amps = governor.predict_amps(frame.pixels.astype(np.float32) / 255.0)
            # One 8-bit quantisation step across 512 pixels is ~0.06 A, so allow
            # a step of slack: the clamp is exact in float, the wire is not.
            assert amps <= 3.0 + 0.07, f"a frame on the wire would draw {amps:.3f} A"


class TestSlowFadePrecisionEndToEnd:
    """The natural-light case, measured on what actually reaches the wire."""

    async def test_a_slow_white_fade_keeps_moving_on_the_wire(self, layout, config):
        # 2700 K to 3400 K over fifteen minutes. Undithered, the blue channel
        # would hold each 8-bit code for about ten seconds and walk up a visible
        # staircase. This asserts on the decoded bytes the receiver got, not on
        # anything internal to the engine.
        config = replace(config, fps=60.0, simulate=False, power=PowerConfig(limit_amps=40.0))
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, config)
            engine.apply_state(
                replace(
                    default_state(),
                    revision=1,
                    effect="slowfade",
                    params=effects.get("slowfade").coerce_params(
                        {
                            "color_a": {"kelvin": 2700},
                            "color_b": {"kelvin": 3400},
                            "period": 900.0,
                            "hold": 0.0,
                        }
                    ),
                    brightness=1.0,
                )
            )
            frames = 60 * 30  # thirty seconds of the fade
            for _ in range(frames):
                await engine.render_once(1 / 60)
            received = await sink.wait_for_frames(frames)
            await engine.stop()

        sink.assert_clean()
        blue = [int(f.pixels[0][2]) for f in received[:frames]]
        assert longest_run(blue) < 60 * 3, (
            f"blue held one code for {longest_run(blue) / 60:.1f} s, which reads as a step"
        )
        assert len(set(blue)) >= 2, "the fade did not move at all"

    async def test_dithering_can_be_switched_off(self, layout, config):
        config = replace(config, dither=False, simulate=False, power=PowerConfig(limit_amps=40.0))
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, config)
            engine.apply_state(
                replace(
                    default_state(),
                    revision=1,
                    effect="solid",
                    params=effects.get("solid").coerce_params({"color": [128, 128, 128]}),
                    brightness=0.5019607843137255,
                )
            )
            for _ in range(20):
                await engine.render_once(1 / 60)
            received = await sink.wait_for_frames(20)
            await engine.stop()

        assert engine.status()["dither"] is False
        # Without dithering every frame quantises identically.
        assert len({int(f.pixels[0][0]) for f in received}) == 1

    async def test_a_solid_colour_is_not_given_dither_noise(self, layout, config):
        # Every colour a user picks from a wheel lands exactly on a code, and
        # must come out the other side as that exact code, every frame.
        config = replace(config, simulate=False, power=PowerConfig(limit_amps=40.0))
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, config)
            engine.apply_state(
                replace(
                    default_state(),
                    revision=1,
                    effect="solid",
                    params=effects.get("solid").coerce_params({"color": [200, 100, 50]}),
                    brightness=1.0,
                )
            )
            for _ in range(30):
                await engine.render_once(1 / 60)
            received = await sink.wait_for_frames(30)
            await engine.stop()

        for frame in received:
            np.testing.assert_array_equal(frame.pixels[0], [200, 100, 50])


class TestMultiBoardPipeline:
    """One board today, but nothing may assume it."""

    async def test_a_three_board_layout_renders_and_ships(self, config):
        from fclights.layout import simple_layout

        layout = simple_layout(1152)
        config = replace(config, simulate=False, power=PowerConfig(limit_amps=80.0))
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, config)
            engine.apply_state(replace(default_state(), revision=1, brightness=1.0))
            await engine.render_once(1 / 60)
            received = await sink.wait_for_frames(3)
            await engine.stop()

        sink.assert_clean()
        assert [(f.channel, f.pixel_count) for f in received[:3]] == [
            (1, 512),
            (2, 512),
            (3, 128),
        ]

    async def test_the_governor_covers_every_board(self, config):
        from fclights.layout import simple_layout
        from fclights.power import PowerGovernor

        layout = simple_layout(1152)
        config = replace(config, simulate=False, power=PowerConfig(limit_amps=10.0))
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            engine = Engine(layout, client, config)
            engine.apply_state(
                replace(
                    default_state(),
                    revision=1,
                    effect="solid",
                    params=effects.get("solid").coerce_params({"color": [255, 255, 255]}),
                    brightness=1.0,
                )
            )
            await engine.render_once(1 / 60)
            received = await sink.wait_for_frames(3)
            await engine.stop()

        governor = PowerGovernor(limit_amps=10.0, pixel_count=1152)
        on_the_wire = np.concatenate([f.pixels for f in received[:3]]).astype(np.float32) / 255.0
        assert governor.predict_amps(on_the_wire) <= 10.0 + 0.2


def longest_run(values: list[int]) -> int:
    longest = run = 1
    for previous, current in pairwise(values):
        run = run + 1 if current == previous else 1
        longest = max(longest, run)
    return longest


class TestStatus:
    def test_status_is_json_clean_and_reports_the_power_model(self, layout, config):
        import json

        engine = engine_with(layout, config)
        engine.render_frame(1 / 60)
        status = engine.status()
        json.dumps(status)

        assert status["pixel_count"] == 512
        assert status["power"]["limit_amps"] == config.power.limit_amps
        assert status["power_model"]["full_white_amps"] == pytest.approx(31.232)

    async def test_stop_is_idempotent(self, layout, config):
        engine = engine_with(layout, config)
        await engine.stop()
        await engine.stop()

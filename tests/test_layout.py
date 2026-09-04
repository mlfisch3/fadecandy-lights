"""Layout parsing and coordinate mapping."""

from __future__ import annotations

import json

import numpy as np
import pytest

from fclights.layout import (
    MAX_PIXELS_PER_OUTPUT,
    OUTPUTS_PER_DEVICE,
    PIXELS_PER_DEVICE,
    LayoutError,
    build_layout,
    load_layout,
    simple_layout,
)


def one_output(count: int = 8, **extra) -> dict:
    return {"devices": [{"id": "fc0", "outputs": [{"index": 0, "count": count, **extra}]}]}


class TestShipping512:
    def test_a_board_is_hard_capped_at_eight_outputs_of_sixty_four(self):
        assert PIXELS_PER_DEVICE == OUTPUTS_PER_DEVICE * MAX_PIXELS_PER_OUTPUT == 512

    def test_one_fadecandy_fills_eight_outputs_of_sixty_four(self, layout):
        assert layout.pixel_count == 512
        assert len(layout.devices) == 1
        assert layout.segment_count == OUTPUTS_PER_DEVICE
        assert [o.count for o in layout.devices[0].outputs] == [MAX_PIXELS_PER_OUTPUT] * 8

    def test_a_single_device_sends_on_the_broadcast_channel(self, layout):
        # Channel 0 is what fcserver's stock configuration expects.
        assert [(m.opc_channel, m.frame_slice) for m in layout.channel_maps()] == [
            (0, slice(0, 512))
        ]

    def test_derived_arrays_have_one_entry_per_pixel(self, layout):
        for array in (layout.u, layout.segment, layout.segment_u):
            assert array.shape == (512,)
        assert layout.positions.shape == (512, 3)
        assert layout.normalized.shape == (512, 3)


class TestCoordinates:
    def test_u_spans_the_whole_run(self, layout):
        assert layout.u[0] == pytest.approx(0.0)
        assert layout.u[-1] == pytest.approx(1.0)
        assert np.all(np.diff(layout.u) > 0)

    def test_normalised_positions_stay_in_the_unit_box(self, layout):
        assert layout.normalized.min() >= 0.0
        assert layout.normalized.max() <= 1.0

    def test_a_degenerate_axis_does_not_divide_by_zero(self, layout):
        # A straight strip along x has zero extent in y and z.
        assert np.isfinite(layout.normalized).all()
        assert np.all(layout.normalized[:, 1] == 0.0)

    def test_outputs_are_laid_end_to_end_in_space(self):
        # 64 pixels at the layout's own pitch, so output 1 starts where output
        # 0 ended. Nothing in the code may assume a particular density.
        for density in (30.0, 60.0, 144.0):
            laid = simple_layout(128, pixels_per_metre=density)
            assert laid.positions[64][0] == pytest.approx(64 / density)
            assert laid.pixels_per_metre == density

    def test_segment_u_restarts_at_each_output(self, layout):
        for segment in range(layout.segment_count):
            values = layout.segment_u[layout.segment == segment]
            assert values[0] == pytest.approx(0.0)
            assert values[-1] == pytest.approx(1.0)

    def test_explicit_points_are_used_verbatim(self):
        points = [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]]
        layout = build_layout(one_output(4, points=points))
        np.testing.assert_allclose(layout.positions, points)

    def test_reverse_flips_an_output(self):
        forward = build_layout(one_output(4, step=[1, 0, 0]))
        backward = build_layout(one_output(4, step=[1, 0, 0], reverse=True))
        np.testing.assert_allclose(forward.positions, backward.positions[::-1])

    def test_single_pixel_layout_is_valid(self):
        layout = simple_layout(1)
        assert layout.pixel_count == 1
        assert layout.u.tolist() == [0.0]
        assert np.isfinite(layout.normalized).all()


class TestValidation:
    def test_empty_document_is_rejected(self):
        with pytest.raises(LayoutError, match="devices"):
            build_layout({})

    def test_device_with_no_outputs_is_rejected(self):
        with pytest.raises(LayoutError, match="at least one output"):
            build_layout({"devices": [{"id": "fc0", "outputs": []}]})

    def test_output_index_must_be_on_the_board(self):
        with pytest.raises(LayoutError, match=r"outside 0\.\.7"):
            build_layout({"devices": [{"id": "fc0", "outputs": [{"index": 8, "count": 4}]}]})

    @pytest.mark.parametrize("count", [0, -1, MAX_PIXELS_PER_OUTPUT + 1])
    def test_pixel_count_per_output_is_bounded_by_the_hardware(self, count):
        # A Fadecandy output drives at most 64 pixels; more silently goes dark.
        with pytest.raises(LayoutError, match=r"outside 1\.\.64"):
            build_layout({"devices": [{"id": "fc0", "outputs": [{"index": 0, "count": count}]}]})

    def test_duplicate_output_index_is_rejected(self):
        with pytest.raises(LayoutError, match="listed twice"):
            build_layout(
                {
                    "devices": [
                        {
                            "id": "fc0",
                            "outputs": [{"index": 0, "count": 4}, {"index": 0, "count": 4}],
                        }
                    ]
                }
            )

    def test_duplicate_device_ids_are_rejected(self):
        with pytest.raises(LayoutError, match="unique"):
            build_layout(
                {
                    "devices": [
                        {"id": "fc0", "outputs": [{"index": 0, "count": 4}]},
                        {"id": "fc0", "opc_channel": 1, "outputs": [{"index": 0, "count": 4}]},
                    ]
                }
            )

    def test_points_must_match_the_pixel_count(self):
        with pytest.raises(LayoutError, match="points for count"):
            build_layout(one_output(4, points=[[0, 0, 0], [1, 0, 0]]))

    def test_points_must_be_three_dimensional(self):
        with pytest.raises(LayoutError, match=r"\[x, y, z\]"):
            build_layout(one_output(2, points=[[0, 0], [1, 0]]))

    def test_missing_count_is_reported_clearly(self):
        with pytest.raises(LayoutError, match="integer 'index' and 'count'"):
            build_layout({"devices": [{"id": "fc0", "outputs": [{"index": 0}]}]})

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_non_positive_pixel_count_is_refused(self, bad):
        with pytest.raises(LayoutError, match="must be positive"):
            simple_layout(bad)

    def test_a_non_positive_density_is_refused(self):
        with pytest.raises(LayoutError, match="pixels_per_metre must be positive"):
            build_layout({"pixels_per_metre": 0, "devices": [
                {"id": "fc0", "outputs": [{"index": 0, "count": 4}]}]})


class TestPixelDensity:
    """Strip density is an unconfirmed estimate, so it must never be hardcoded."""

    def test_the_default_is_only_a_default(self):
        from fclights.layout import DEFAULT_PIXELS_PER_METRE

        assert simple_layout(8).pixels_per_metre == DEFAULT_PIXELS_PER_METRE
        assert simple_layout(8, pixels_per_metre=144.0).pixels_per_metre == 144.0

    def test_outputs_inherit_the_layout_density(self):
        layout = build_layout(
            {
                "pixels_per_metre": 100.0,
                "devices": [{"id": "fc0", "outputs": [{"index": 0, "count": 3}]}],
            }
        )
        np.testing.assert_allclose(layout.positions[:, 0], [0.0, 0.01, 0.02])

    def test_an_output_may_override_the_layout_density(self):
        # Mixed-density installations are a real thing; a per-output step wins.
        layout = build_layout(
            {
                "pixels_per_metre": 30.0,
                "devices": [
                    {
                        "id": "fc0",
                        "outputs": [{"index": 0, "count": 3, "step": [0.5, 0.0, 0.0]}],
                    }
                ],
            }
        )
        np.testing.assert_allclose(layout.positions[:, 0], [0.0, 0.5, 1.0])

    def test_density_is_reported_to_clients(self):
        assert simple_layout(8, pixels_per_metre=42.0).to_dict()["pixels_per_metre"] == 42.0


class TestMultiDeviceGrowth:
    """Multi-Fadecandy support is not built, but the design must not preclude it."""

    def test_a_run_longer_than_one_board_spans_boards(self):
        # The real installation is around 18 runs, which needs three boards.
        # Nothing may assume a single device.
        layout = simple_layout(1152)
        assert len(layout.devices) == 3
        assert layout.segment_count == 18
        assert all(o.count <= MAX_PIXELS_PER_OUTPUT for d in layout.devices for o in d.outputs)

    def test_spanning_boards_gives_each_its_own_channel_and_slice(self):
        layout = simple_layout(1152)
        assert [(m.opc_channel, m.frame_slice) for m in layout.channel_maps()] == [
            (1, slice(0, 512)),
            (2, slice(512, 1024)),
            (3, slice(1024, 1152)),
        ]

    def test_a_single_board_still_uses_the_broadcast_channel(self):
        # fcserver's stock configuration expects channel 0 for one device.
        assert simple_layout(512).fcserver_map() == [[0, 0, 0, 512]]

    def test_boards_are_laid_out_continuously_in_space(self):
        layout = simple_layout(1024, pixels_per_metre=30.0)
        assert layout.positions[512][0] == pytest.approx(512 / 30.0)

    def test_two_devices_get_their_own_channels_and_slices(self):
        layout = build_layout(
            {
                "devices": [
                    {"id": "fc0", "opc_channel": 1, "outputs": [{"index": 0, "count": 64}]},
                    {"id": "fc1", "opc_channel": 2, "outputs": [{"index": 0, "count": 32}]},
                ]
            }
        )
        assert layout.pixel_count == 96
        assert [(m.opc_channel, m.frame_slice) for m in layout.channel_maps()] == [
            (1, slice(0, 64)),
            (2, slice(64, 96)),
        ]
        assert layout.fcserver_map() == [[1, 0, 0, 64], [2, 0, 0, 32]]

    def test_two_devices_may_not_share_a_channel(self):
        with pytest.raises(LayoutError, match="own opc_channel"):
            build_layout(
                {
                    "devices": [
                        {"id": "fc0", "opc_channel": 1, "outputs": [{"index": 0, "count": 4}]},
                        {"id": "fc1", "opc_channel": 1, "outputs": [{"index": 0, "count": 4}]},
                    ]
                }
            )

    def test_broadcast_channel_is_refused_with_multiple_devices(self):
        # Channel 0 goes to every board, so a second device on it would mirror
        # the first rather than continuing the run.
        with pytest.raises(LayoutError, match="broadcast-only"):
            build_layout(
                {
                    "devices": [
                        {"id": "fc0", "opc_channel": 0, "outputs": [{"index": 0, "count": 4}]},
                        {"id": "fc1", "opc_channel": 1, "outputs": [{"index": 0, "count": 4}]},
                    ]
                }
            )


class TestFileLoading:
    def test_round_trips_through_a_file(self, tmp_path):
        path = tmp_path / "layout.json"
        path.write_text(json.dumps(one_output(12)))
        assert load_layout(path).pixel_count == 12

    def test_missing_file_is_reported_by_path(self, tmp_path):
        with pytest.raises(LayoutError, match="not found"):
            load_layout(tmp_path / "nope.json")

    def test_malformed_json_is_reported_as_such(self, tmp_path):
        path = tmp_path / "layout.json"
        path.write_text("{not json")
        with pytest.raises(LayoutError, match="not valid JSON"):
            load_layout(path)

    def test_shipped_example_layout_is_valid(self):
        from pathlib import Path

        example = Path(__file__).resolve().parents[1] / "config" / "layout.example.json"
        if not example.exists():
            pytest.skip("example layout not present")
        layout = load_layout(example)
        assert layout.pixel_count == 512

    def test_api_serialisation_is_json_clean(self, layout):
        payload = layout.to_dict()
        json.dumps(payload)
        assert payload["pixel_count"] == 512
        assert len(payload["devices"][0]["outputs"]) == 8


NON_FINITE_LITERALS = ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"]


class TestNonFiniteNumbersAreRefused:
    """A layout file is a document from outside the process.

    A non-finite number in one fails nowhere useful: it spreads silently through
    the derived position arrays, and `Layout.to_dict` then serves it to the phone
    as `null` over REST and as a bare `NaN` token over the WebSocket, which is
    not valid JSON. `int(inf)` also raises OverflowError rather than ValueError,
    which used to escape `LayoutError` entirely and surface as a traceback.
    """

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_density_is_refused(self, value):
        with pytest.raises(LayoutError, match="finite"):
            build_layout({"pixels_per_metre": value, **one_output(8)})

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    @pytest.mark.parametrize("key", ["origin", "step"])
    def test_a_non_finite_coordinate_is_refused(self, value, key):
        with pytest.raises(LayoutError, match="finite"):
            build_layout(one_output(8, **{key: [value, 0.0, 0.0]}))

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_explicit_point_is_refused(self, value):
        with pytest.raises(LayoutError, match="finite"):
            build_layout(one_output(2, points=[[value, 0.0, 0.0], [1.0, 0.0, 0.0]]))

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    @pytest.mark.parametrize("key", ["index", "count"])
    def test_a_non_finite_output_integer_is_refused(self, value, key):
        raw = {"devices": [{"id": "fc0", "outputs": [{"index": 0, "count": 8, key: value}]}]}
        with pytest.raises(LayoutError):
            build_layout(raw)

    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_a_non_finite_opc_channel_is_refused(self, value):
        raw = {"devices": [{"id": "fc0", "opc_channel": value,
                            "outputs": [{"index": 0, "count": 8}]}]}
        with pytest.raises(LayoutError):
            build_layout(raw)

    @pytest.mark.parametrize("literal", NON_FINITE_LITERALS)
    def test_the_loader_refuses_the_document(self, tmp_path, literal):
        path = tmp_path / "layout.json"
        path.write_text(
            f'{{"pixels_per_metre": {literal}, "devices": [{{"id": "fc0", '
            f'"outputs": [{{"index": 0, "count": 8}}]}}]}}'
        )
        with pytest.raises(LayoutError):
            load_layout(path)

    @pytest.mark.parametrize("literal", NON_FINITE_LITERALS)
    def test_the_loader_refuses_it_in_an_integer_position_too(self, tmp_path, literal):
        path = tmp_path / "layout.json"
        path.write_text(
            f'{{"devices": [{{"id": "fc0", "outputs": [{{"index": 0, "count": {literal}}}]}}]}}'
        )
        with pytest.raises(LayoutError):
            load_layout(path)

    def test_a_valid_layout_still_loads(self, tmp_path):
        path = tmp_path / "layout.json"
        path.write_text(
            json.dumps(
                {
                    "pixels_per_metre": 30.0,
                    "devices": [{"id": "fc0", "outputs": [{"index": 0, "count": 8}]}],
                }
            )
        )
        layout = load_layout(path)
        assert layout.pixel_count == 8
        assert np.isfinite(layout.positions).all()

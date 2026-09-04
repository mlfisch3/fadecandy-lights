"""Control API tests.

These pin the contract in ``docs/api.md``.  The Android app is a separate task
built against that document, so a change here that is not reflected there is a
broken client.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from fclights import __version__, effects
from fclights.api import create_app
from fclights.service import build_service


@pytest.fixture
def client(config):
    service = build_service(config)
    with TestClient(create_app(service.controller)) as test_client:
        test_client.service = service
        yield test_client


def state_of(response) -> dict:
    return response.json().get("state", response.json())


class TestReadEndpoints:
    def test_health_reports_liveness_and_mode(self, client):
        body = client.get("/api/health").json()
        assert body == {
            "ok": True,
            "version": __version__,
            "simulated": True,
            "opc_connected": True,
        }

    def test_state_returns_the_whole_picture(self, client):
        body = client.get("/api/state").json()
        assert body["type"] == "state"
        assert set(body["state"]) == {
            "power",
            "brightness",
            "effect",
            "params",
            "scenes",
            "active_scene",
            "revision",
        }

    def test_layout_describes_the_installation(self, client):
        body = client.get("/api/layout").json()
        assert body["pixel_count"] == 512
        assert len(body["devices"]) == 1
        assert len(body["devices"][0]["outputs"]) == 8
        assert "bounds" in body

    def test_layout_reports_the_strip_density(self, client):
        # An estimate, and one a client should be told rather than assume.
        assert client.get("/api/layout").json()["pixels_per_metre"] > 0

    def test_status_reports_the_power_model(self, client):
        body = client.get("/api/status").json()
        assert body["pixel_count"] == 512
        assert body["power_model"]["limit_amps"] == 10.0
        assert body["power_model"]["full_white_amps"] == pytest.approx(31.232)

    def test_status_reports_whether_dithering_is_on(self, client):
        assert client.get("/api/status").json()["dither"] is True

    def test_info_bundles_layout_and_status(self, client):
        body = client.get("/api/info").json()
        assert body["service"] == "fclights"
        assert body["layout"]["pixel_count"] == 512
        assert "status" in body


class TestEffectDiscovery:
    def test_every_effect_is_listed_with_a_schema(self, client):
        listed = client.get("/api/effects").json()["effects"]
        assert {e["name"] for e in listed} == set(effects.names())

    def test_schemas_carry_what_a_client_needs_to_build_a_control(self, client):
        for schema in client.get("/api/effects").json()["effects"]:
            assert schema["display_name"]
            for param in schema["params"]:
                assert {"name", "type", "default", "label"} <= set(param)
                if param["type"] in {"float", "int"}:
                    assert "minimum" in param and "maximum" in param
                if param["type"] == "enum":
                    assert param["choices"]

    def test_the_response_is_plain_json(self, client):
        json.dumps(client.get("/api/effects").json())

    def test_colour_controls_advertise_the_kelvin_slider(self, client):
        colour_params = [
            param
            for schema in client.get("/api/effects").json()["effects"]
            for param in schema["params"]
            if param["type"] == "color"
        ]
        assert colour_params, "expected at least one colour control"
        for param in colour_params:
            assert param["supports_kelvin"] is True
            assert param["kelvin_range"] == [1800.0, 6500.0]


class TestColorTemperature:
    """A kelvin value is a first-class way to name a colour over the wire."""

    def test_a_colour_can_be_set_as_a_temperature(self, client):
        body = state_of(
            client.put(
                "/api/effect",
                json={"effect": "solid", "params": {"color": {"kelvin": 2700}}},
            )
        )
        colour = body["params"]["color"]
        assert colour["mode"] == "kelvin"
        assert colour["kelvin"] == 2700.0
        assert len(colour["rgb"]) == 3, "a swatch must come back with it"

    def test_the_temperature_survives_a_read_back(self, client):
        # Otherwise the phone could not put the warm-to-cool slider back where
        # the user left it.
        client.put(
            "/api/effect", json={"effect": "solid", "params": {"color": {"kelvin": 3200}}}
        )
        assert state_of(client.get("/api/state"))["params"]["color"]["kelvin"] == 3200.0

    def test_rgb_and_hex_still_work(self, client):
        for value, expected in (([255, 128, 0], [255, 128, 0]), ("#00ff80", [0, 255, 128])):
            body = state_of(
                client.put(
                    "/api/effect", json={"effect": "solid", "params": {"color": value}}
                )
            )
            assert body["params"]["color"] == {"mode": "rgb", "rgb": expected}

    def test_a_temperature_outside_the_range_is_refused(self, client):
        response = client.put(
            "/api/effect", json={"effect": "solid", "params": {"color": {"kelvin": 12000}}}
        )
        assert response.status_code == 400
        assert "range" in response.json()["detail"]

    def test_a_temperature_survives_a_scene_round_trip(self, client):
        client.put(
            "/api/effect", json={"effect": "solid", "params": {"color": {"kelvin": 2400}}}
        )
        scene = client.post("/api/scenes", json={"name": "Candle"}).json()["scene"]
        client.put("/api/effect", json={"effect": "rainbow"})

        recalled = state_of(client.post(f"/api/scenes/{scene['id']}/recall"))
        assert recalled["params"]["color"]["kelvin"] == 2400.0

    def test_the_slow_fade_effect_is_available_for_natural_light(self, client):
        names = {e["name"] for e in client.get("/api/effects").json()["effects"]}
        assert "slowfade" in names

        body = state_of(
            client.put(
                "/api/effect",
                json={
                    "effect": "slowfade",
                    "params": {
                        "color_a": {"kelvin": 2700},
                        "color_b": {"kelvin": 3400},
                        "period": 1800.0,
                    },
                },
            )
        )
        assert body["params"]["period"] == 1800.0
        assert body["params"]["color_a"]["kelvin"] == 2700.0


class TestCommands:
    def test_power_can_be_switched(self, client):
        assert state_of(client.put("/api/power", json={"on": False}))["power"] is False
        assert state_of(client.put("/api/power", json={"on": True}))["power"] is True

    def test_brightness_can_be_set(self, client):
        assert state_of(client.put("/api/brightness", json={"brightness": 0.4}))[
            "brightness"
        ] == 0.4

    @pytest.mark.parametrize("value", [-0.1, 1.5])
    def test_out_of_range_brightness_is_refused(self, client, value):
        response = client.put("/api/brightness", json={"brightness": value})
        assert response.status_code == 422
        assert "brightness" in response.json()["detail"]

    def test_effect_selection_fills_in_defaults(self, client):
        body = state_of(client.put("/api/effect", json={"effect": "rainbow"}))
        assert body["effect"] == "rainbow"
        assert body["params"] == effects.get("rainbow").defaults()

    def test_effect_selection_accepts_parameters(self, client):
        body = state_of(
            client.put("/api/effect", json={"effect": "rainbow", "params": {"speed": 0.9}})
        )
        assert body["params"]["speed"] == 0.9

    def test_unknown_effect_is_a_404(self, client):
        response = client.put("/api/effect", json={"effect": "disco"})
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_bad_parameter_is_a_400_naming_the_parameter(self, client):
        response = client.put(
            "/api/effect", json={"effect": "rainbow", "params": {"saturation": 9}}
        )
        assert response.status_code == 400
        assert "saturation" in response.json()["detail"]

    def test_unknown_parameter_is_rejected_rather_than_ignored(self, client):
        response = client.put(
            "/api/effect", json={"effect": "rainbow", "params": {"speeed": 1.0}}
        )
        assert response.status_code == 400
        assert "speeed" in response.json()["detail"]

    def test_patching_params_leaves_the_others_alone(self, client):
        client.put("/api/effect", json={"effect": "rainbow", "params": {"cycles": 4.0}})
        body = state_of(client.patch("/api/effect/params", json={"params": {"speed": 0.7}}))
        assert body["params"]["speed"] == 0.7
        assert body["params"]["cycles"] == 4.0

    def test_unknown_body_fields_are_rejected(self, client):
        # A strict body means a client typo fails loudly instead of silently.
        assert client.put("/api/power", json={"on": True, "extra": 1}).status_code == 422

    def test_every_command_bumps_the_revision(self, client):
        first = state_of(client.get("/api/state"))["revision"]
        client.put("/api/brightness", json={"brightness": 0.2})
        second = state_of(client.get("/api/state"))["revision"]
        assert second > first


class TestScenes:
    def test_save_list_recall_delete(self, client):
        client.put("/api/effect", json={"effect": "fire", "params": {"cooling": 0.8}})
        client.put("/api/brightness", json={"brightness": 0.6})

        created = client.post("/api/scenes", json={"name": "Hearth"})
        assert created.status_code == 201
        scene = created.json()["scene"]
        assert scene["name"] == "Hearth"
        assert scene["effect"] == "fire"

        listed = client.get("/api/scenes").json()
        assert [s["id"] for s in listed["scenes"]] == [scene["id"]]

        client.put("/api/effect", json={"effect": "rainbow"})
        client.put("/api/brightness", json={"brightness": 0.1})

        recalled = state_of(client.post(f"/api/scenes/{scene['id']}/recall"))
        assert recalled["effect"] == "fire"
        assert recalled["params"]["cooling"] == 0.8
        assert recalled["brightness"] == 0.6
        assert recalled["active_scene"] == scene["id"]

        assert client.delete(f"/api/scenes/{scene['id']}").status_code == 200
        assert client.get("/api/scenes").json()["scenes"] == []

    def test_reading_one_scene(self, client):
        scene = client.post("/api/scenes", json={"name": "Evening"}).json()["scene"]
        assert client.get(f"/api/scenes/{scene['id']}").json()["scene"] == scene

    def test_renaming_keeps_the_stored_look(self, client):
        client.put("/api/effect", json={"effect": "fire", "params": {"cooling": 0.8}})
        scene = client.post("/api/scenes", json={"name": "Hearth"}).json()["scene"]

        # Change the live look, then rename. The rename must not quietly
        # redefine what the scene shows.
        client.put("/api/effect", json={"effect": "rainbow"})
        renamed = client.put(f"/api/scenes/{scene['id']}", json={"name": "Fireplace"}).json()

        assert renamed["scene"]["name"] == "Fireplace"
        assert renamed["scene"]["effect"] == "fire"
        assert renamed["scene"]["params"]["cooling"] == 0.8

    def test_renaming_leaves_the_live_look_alone(self, client):
        # docs/api.md: "PUT with only name renames without changing what the
        # scene shows". It must not change what the STRIP shows either.
        client.put("/api/effect", json={"effect": "fire", "params": {"cooling": 0.8}})
        client.put("/api/brightness", json={"brightness": 0.6})
        scene = client.post("/api/scenes", json={"name": "Hearth"}).json()["scene"]

        client.put("/api/effect", json={"effect": "rainbow"})
        client.put("/api/brightness", json={"brightness": 0.1})
        live_before = state_of(client.get("/api/state"))

        client.put(f"/api/scenes/{scene['id']}", json={"name": "Fireplace"})
        live_after = state_of(client.get("/api/state"))

        assert live_after["effect"] == live_before["effect"] == "rainbow"
        assert live_after["params"] == live_before["params"]
        assert live_after["brightness"] == live_before["brightness"] == 0.1
        assert live_after["active_scene"] == live_before["active_scene"]

    def test_recapturing_overwrites_the_look_and_keeps_the_id(self, client):
        client.put("/api/effect", json={"effect": "fire"})
        scene = client.post("/api/scenes", json={"name": "Hearth"}).json()["scene"]

        client.put("/api/effect", json={"effect": "rainbow"})
        updated = client.put(
            f"/api/scenes/{scene['id']}", json={"capture": True}
        ).json()["scene"]

        assert updated["id"] == scene["id"]
        assert updated["effect"] == "rainbow"
        assert updated["name"] == "Hearth"

    def test_an_empty_scene_update_is_refused(self, client):
        scene = client.post("/api/scenes", json={"name": "Hearth"}).json()["scene"]
        assert client.put(f"/api/scenes/{scene['id']}", json={}).status_code == 400

    def test_unknown_scene_ids_are_404s(self, client):
        assert client.get("/api/scenes/ghost").status_code == 404
        assert client.delete("/api/scenes/ghost").status_code == 404
        assert client.post("/api/scenes/ghost/recall").status_code == 404
        assert client.put("/api/scenes/ghost", json={"name": "x"}).status_code == 404

    def test_empty_scene_names_are_refused(self, client):
        assert client.post("/api/scenes", json={"name": "   "}).status_code == 400
        assert client.post("/api/scenes", json={"name": ""}).status_code == 422


class TestPersistenceThroughTheApi:
    def test_changes_reach_disk_and_come_back_after_a_restart(self, config):
        service = build_service(config)
        with TestClient(create_app(service.controller)) as client:
            client.put("/api/effect", json={"effect": "fire", "params": {"cooling": 0.9}})
            client.put("/api/brightness", json={"brightness": 0.42})
            client.post("/api/scenes", json={"name": "Hearth"})
        # Shutdown flushes whatever the debounce had not yet written.
        assert config.state_path.exists()

        restarted = build_service(config)
        with TestClient(create_app(restarted.controller)) as client:
            body = state_of(client.get("/api/state"))
            assert body["effect"] == "fire"
            assert body["params"]["cooling"] == 0.9
            assert body["brightness"] == 0.42
            assert [s["name"] for s in body["scenes"]] == ["Hearth"]


class TestWebSocket:
    def test_a_new_client_is_handed_everything_it_needs(self, client):
        with client.websocket_connect("/api/ws") as ws:
            hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["version"] == __version__
        assert hello["state"]["effect"]
        assert hello["layout"]["pixel_count"] == 512
        assert {e["name"] for e in hello["effects"]} == set(effects.names())

    def test_state_changes_are_pushed(self, client):
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()  # hello
            client.put("/api/brightness", json={"brightness": 0.77})
            message = ws.receive_json()

        assert message["type"] == "state"
        assert message["state"]["brightness"] == 0.77

    def test_two_clients_both_see_a_third_partys_change(self, client):
        # The reason there is a socket at all: several phones stay in agreement
        # instead of fighting over the last write.
        with client.websocket_connect("/api/ws") as first, client.websocket_connect(
            "/api/ws"
        ) as second:
            first.receive_json()
            second.receive_json()

            client.put("/api/effect", json={"effect": "twinkle"})

            assert first.receive_json()["state"]["effect"] == "twinkle"
            assert second.receive_json()["state"]["effect"] == "twinkle"

    def test_it_answers_a_ping(self, client):
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
            ws.send_text("ping")
            assert ws.receive_json() == {"type": "pong"}

    def test_a_disconnect_does_not_break_later_broadcasts(self, client):
        with client.websocket_connect("/api/ws") as ws:
            ws.receive_json()
        # The dropped client must not stop the next command from succeeding.
        assert client.put("/api/brightness", json={"brightness": 0.5}).status_code == 200


class TestErrorShape:
    def test_errors_share_one_shape(self, client):
        body = client.get("/api/scenes/ghost").json()
        assert set(body) == {"error", "detail"}
        assert body["error"] == "not_found"

    def test_unknown_routes_are_404s_in_the_same_shape(self, client):
        body = client.get("/api/nope").json()
        assert set(body) == {"error", "detail"}
        assert body["error"] == "not_found"

    def test_validation_failures_share_the_shape_and_name_the_field(self, client):
        response = client.put("/api/brightness", json={"brightness": "bright"})
        assert response.status_code == 422
        body = response.json()
        assert set(body) == {"error", "detail"}
        assert body["error"] == "unprocessable_entity"
        assert "brightness" in body["detail"]

    def test_a_wrong_method_shares_the_shape(self, client):
        body = client.post("/api/brightness", json={"brightness": 0.5}).json()
        assert set(body) == {"error", "detail"}

    def test_an_unhandled_failure_shares_the_shape(self, config, monkeypatch):
        # The Android app has one error parser. Starlette's stock 500 is plain
        # text, which that parser cannot read.
        service = build_service(config)
        with TestClient(
            create_app(service.controller), raise_server_exceptions=False
        ) as test_client:
            def explode() -> dict:
                raise RuntimeError("the render loop ate the telemetry")

            monkeypatch.setattr(service.engine, "status", explode)
            response = test_client.get("/api/status")

            assert response.status_code == 500
            body = response.json()
            assert set(body) == {"error", "detail"}
            assert body["error"] == "internal_error"

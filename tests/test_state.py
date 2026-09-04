"""State mutation and persistence.

Persistence matters more than it looks: this is a fixed installation, so the
rig has to come back on the scene it was showing before the power cut without
anyone reaching for a phone.
"""

from __future__ import annotations

import json
import math

import pytest

from fclights import effects
from fclights.state import (
    MAX_SCENES,
    STATE_VERSION,
    StateError,
    StateStore,
    default_state,
    state_from_dict,
)


@pytest.fixture
def store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state.json")


class TestDefaults:
    def test_default_state_is_internally_consistent(self):
        state = default_state()
        effect_cls = effects.get(state.effect)
        assert state.params == effect_cls.defaults()

    def test_default_brightness_is_conservative(self):
        # 512 pixels at full white wants ~30 A. Booting dim means a first power
        # up on unknown wiring is uneventful.
        assert 0.0 < default_state().brightness <= 0.5


class TestMutation:
    def test_power_toggles(self, store):
        assert store.set_power(False).power is False
        assert store.set_power(True).power is True

    def test_brightness_is_range_checked(self, store):
        assert store.set_brightness(0.5).brightness == 0.5
        for bad in (-0.1, 1.1):
            with pytest.raises(StateError, match="between 0 and 1"):
                store.set_brightness(bad)

    def test_non_numeric_brightness_is_rejected(self, store):
        with pytest.raises(StateError, match="must be a number"):
            store.set_brightness("bright")

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 10**400])
    def test_non_finite_brightness_is_rejected(self, store, bad):
        with pytest.raises(StateError, match="finite"):
            store.set_brightness(bad)
        assert store.state.brightness == default_state().brightness

    def test_selecting_an_effect_fills_in_defaults(self, store):
        state = store.set_effect("rainbow", {"speed": 0.5})
        assert state.effect == "rainbow"
        assert state.params["speed"] == 0.5
        assert state.params["cycles"] == effects.get("rainbow").defaults()["cycles"]

    def test_unknown_effect_is_rejected(self, store):
        with pytest.raises(effects.UnknownEffectError):
            store.set_effect("disco")

    def test_partial_parameter_update_keeps_the_rest(self, store):
        store.set_effect("rainbow", {"speed": 0.5, "cycles": 3.0})
        state = store.update_params({"speed": 0.9})
        assert state.params == {"speed": 0.9, "cycles": 3.0, "saturation": 1.0, "axis": "run"}

    def test_bad_parameter_update_leaves_state_untouched(self, store):
        store.set_effect("rainbow", {"speed": 0.5})
        before = store.state
        with pytest.raises(effects.ParamError):
            store.update_params({"speed": 99.0})
        assert store.state is before

    def test_every_change_bumps_the_revision(self, store):
        revisions = [store.state.revision]
        store.set_power(False)
        revisions.append(store.state.revision)
        store.set_brightness(0.2)
        revisions.append(store.state.revision)
        store.set_effect("fire")
        revisions.append(store.state.revision)
        assert revisions == sorted(set(revisions))


class TestScenes:
    def test_saving_captures_the_live_look(self, store):
        store.set_effect("fire", {"cooling": 0.8})
        store.set_brightness(0.6)
        _, scene = store.save_scene("Hearth")

        assert scene.name == "Hearth"
        assert scene.effect == "fire"
        assert scene.params["cooling"] == 0.8
        assert scene.brightness == 0.6

    def test_the_saved_scene_becomes_the_active_one(self, store):
        _, scene = store.save_scene("Now")
        assert store.state.active_scene == scene.id

    def test_recall_restores_effect_params_and_brightness(self, store):
        store.set_effect("fire", {"cooling": 0.8})
        store.set_brightness(0.6)
        _, scene = store.save_scene("Hearth")

        store.set_effect("rainbow")
        store.set_brightness(0.1)
        state = store.recall_scene(scene.id)

        assert state.effect == "fire"
        assert state.params["cooling"] == 0.8
        assert state.brightness == 0.6
        assert state.active_scene == scene.id

    def test_editing_after_recall_clears_the_active_scene(self, store):
        # Otherwise a phone would keep showing "Hearth" highlighted after the
        # user has dragged a slider away from it.
        _, scene = store.save_scene("Hearth")
        store.recall_scene(scene.id)
        assert store.state.active_scene == scene.id
        store.set_brightness(0.9)
        assert store.state.active_scene is None

    def test_master_power_does_not_clear_the_active_scene(self, store):
        _, scene = store.save_scene("Hearth")
        store.recall_scene(scene.id)
        store.set_power(False)
        assert store.state.active_scene == scene.id

    def test_overwriting_a_scene_keeps_its_id_and_creation_time(self, store):
        _, first = store.save_scene("Hearth")
        store.set_effect("rainbow")
        _, second = store.save_scene("Hearth", scene_id=first.id)

        assert second.id == first.id
        assert second.created_at == first.created_at
        assert second.updated_at >= first.updated_at
        assert second.effect == "rainbow"
        assert len(store.state.scenes) == 1

    def test_deleting_removes_it_and_clears_active(self, store):
        _, scene = store.save_scene("Hearth")
        store.recall_scene(scene.id)
        state = store.delete_scene(scene.id)
        assert state.scenes == ()
        assert state.active_scene is None

    def test_unknown_scene_ids_are_reported(self, store):
        for call in (store.recall_scene, store.delete_scene):
            with pytest.raises(StateError, match="no scene with id"):
                call("nope")

    def test_scene_names_are_trimmed_and_must_not_be_empty(self, store):
        _, scene = store.save_scene("  Evening  ")
        assert scene.name == "Evening"
        with pytest.raises(StateError, match="must not be empty"):
            store.save_scene("   ")

    def test_overlong_scene_names_are_rejected(self, store):
        with pytest.raises(StateError, match="at most"):
            store.save_scene("x" * 500)

    def test_renaming_touches_neither_the_live_look_nor_the_stored_one(self, store):
        store.set_effect("fire", {"cooling": 0.8})
        store.set_brightness(0.6)
        _, scene = store.save_scene("Hearth")

        store.set_effect("rainbow")
        store.set_brightness(0.1)
        before = store.state

        state, renamed = store.rename_scene(scene.id, "  Fireplace  ")

        assert renamed.name == "Fireplace"
        assert renamed.effect == "fire"
        assert renamed.params == scene.params
        assert renamed.brightness == 0.6
        assert renamed.id == scene.id
        assert renamed.created_at == scene.created_at

        assert state.effect == before.effect
        assert state.params == before.params
        assert state.brightness == before.brightness
        assert state.active_scene == before.active_scene

    def test_renaming_rejects_an_empty_name(self, store):
        _, scene = store.save_scene("Hearth")
        with pytest.raises(StateError, match="empty"):
            store.rename_scene(scene.id, "   ")

    def test_renaming_an_unknown_scene_is_refused(self, store):
        with pytest.raises(StateError, match="no scene"):
            store.rename_scene("ghost", "Nope")

    def test_scene_ids_are_unique(self, store):
        ids = {store.save_scene(f"scene {i}")[1].id for i in range(20)}
        assert len(ids) == 20

    def test_the_scene_list_is_bounded(self, store):
        for i in range(MAX_SCENES):
            store.save_scene(f"scene {i}")
        with pytest.raises(StateError, match="limit"):
            store.save_scene("one too many")


class TestPersistence:
    def test_state_survives_a_restart(self, tmp_path):
        path = tmp_path / "state.json"
        first = StateStore(path)
        first.set_effect("fire", {"cooling": 0.9})
        first.set_brightness(0.42)
        first.save_scene("Hearth")
        first.save()

        second = StateStore(path)
        restored = second.load()

        assert restored.effect == "fire"
        assert restored.params["cooling"] == 0.9
        assert restored.brightness == 0.42
        assert [s.name for s in restored.scenes] == ["Hearth"]

    def test_the_file_is_versioned(self, store):
        store.save()
        assert json.loads(store.path.read_text())["version"] == STATE_VERSION

    def test_writes_are_atomic(self, store):
        # A rename leaves either the old file or the new one, never a truncated
        # one, which is what makes a power cut mid-save survivable.
        store.set_brightness(0.3)
        store.save()
        assert list(store.path.parent.glob("*.tmp")) == []
        json.loads(store.path.read_text())

    def test_dirty_tracking_avoids_pointless_writes(self, store):
        assert store.dirty is False
        store.set_brightness(0.3)
        assert store.dirty is True
        store.save()
        assert store.dirty is False

    def test_loading_with_no_file_yields_defaults(self, store):
        assert store.load() == default_state()

    def test_a_corrupt_file_does_not_stop_the_lights_coming_up(self, store):
        store.path.write_text("{ this is not json")
        restored = store.load()
        assert restored.effect == default_state().effect

    def test_an_unwritable_path_is_logged_not_raised(self, tmp_path):
        # A read-only filesystem must not take the installation down.
        store = StateStore(tmp_path / "missing-dir" / "sub" / "state.json")
        store.set_brightness(0.5)
        store.save()

    def test_persistence_can_be_disabled(self, tmp_path):
        store = StateStore(tmp_path / "state.json", persist=False)
        store.set_brightness(0.5)
        store.save()
        assert not (tmp_path / "state.json").exists()


class TestRestoringDamagedDocuments:
    """Restoring the last look must never be the reason the rig fails to boot."""

    def test_an_effect_that_no_longer_exists_falls_back(self):
        state = state_from_dict({"effect": "discontinued", "params": {"x": 1}})
        assert state.effect == default_state().effect
        assert state.params == effects.get(state.effect).defaults()

    def test_parameters_outside_their_current_range_fall_back(self):
        state = state_from_dict({"effect": "rainbow", "params": {"saturation": 99}})
        assert state.params == effects.get("rainbow").defaults()

    def test_unusable_scenes_are_dropped_and_the_rest_kept(self):
        state = state_from_dict(
            {
                "scenes": [
                    {"id": "a", "name": "good", "effect": "solid", "params": {}},
                    {"id": "b", "name": "gone", "effect": "discontinued", "params": {}},
                ]
            }
        )
        assert [s.name for s in state.scenes] == ["good"]

    def test_an_active_scene_pointing_at_nothing_is_cleared(self):
        assert state_from_dict({"active_scene": "ghost"}).active_scene is None

    def test_out_of_range_brightness_is_clamped(self):
        assert state_from_dict({"brightness": 5.0}).brightness == 1.0
        assert state_from_dict({"brightness": -1.0}).brightness == 0.0

    def test_a_newer_state_version_is_refused_rather_than_guessed_at(self):
        with pytest.raises(StateError, match="version"):
            state_from_dict({"version": STATE_VERSION + 1})

    def test_a_non_object_document_is_refused(self):
        with pytest.raises(StateError, match="JSON object"):
            state_from_dict(["not", "an", "object"])


HOSTILE_DOCUMENTS = [
    pytest.param({"revision": "abc"}, id="revision-is-a-string"),
    pytest.param({"revision": None}, id="revision-is-null"),
    pytest.param({"revision": {"nested": "garbage"}}, id="revision-is-an-object"),
    pytest.param({"version": "one"}, id="version-is-a-string"),
    pytest.param({"version": None}, id="version-is-null"),
    pytest.param({"brightness": None}, id="brightness-is-null"),
    pytest.param({"brightness": "half"}, id="brightness-is-a-string"),
    pytest.param({"brightness": [0.5]}, id="brightness-is-a-list"),
    pytest.param({"effect": None}, id="effect-is-null"),
    pytest.param({"effect": {"name": "solid"}}, id="effect-is-an-object"),
    pytest.param({"params": "not-a-mapping"}, id="params-is-a-string"),
    pytest.param({"params": [1, 2, 3]}, id="params-is-a-list"),
    pytest.param({"params": {"color": {"mode": "kelvin", "kelvin": None}}}, id="param-is-null"),
    pytest.param({"scenes": 5}, id="scenes-is-a-number"),
    pytest.param({"scenes": {"a": 1}}, id="scenes-is-an-object"),
    pytest.param({"scenes": ["not-an-object"]}, id="scene-is-a-string"),
    pytest.param({"scenes": [None]}, id="scene-is-null"),
    pytest.param({"scenes": [[1, 2]]}, id="scene-is-a-list"),
    pytest.param({"scenes": [{"name": "x", "effect": "solid", "brightness": None}]},
                 id="scene-brightness-is-null"),
    pytest.param({"scenes": [{"name": None, "effect": "solid"}]}, id="scene-name-is-null"),
    pytest.param({"scenes": [{"name": "x", "effect": "solid", "created_at": "soon"}]},
                 id="scene-timestamp-is-a-string"),
    pytest.param({"active_scene": {"id": "ghost"}}, id="active-scene-is-an-object"),
    pytest.param({"brightness": float("nan")}, id="brightness-is-nan"),
    pytest.param(
        {"scenes": [{"name": "x", "effect": "solid", "created_at": float("nan")}]},
        id="scene-created-at-is-nan",
    ),
    pytest.param(
        {"scenes": [{"name": "x", "effect": "solid", "updated_at": float("inf")}]},
        id="scene-updated-at-is-inf",
    ),
    pytest.param(
        {"scenes": [{"name": "x", "effect": "solid", "created_at": 10**400}]},
        id="scene-created-at-overflows-a-float",
    ),
    pytest.param({"brightness": float("inf")}, id="brightness-is-inf"),
    pytest.param({"revision": float("inf")}, id="revision-is-inf"),
    pytest.param({"effect": "breathe", "params": {"speed": float("nan")}}, id="param-is-nan"),
    pytest.param({"effect": "twinkle", "params": {"seed": float("inf")}}, id="int-param-is-inf"),
    pytest.param(
        {"scenes": [{"name": "x", "effect": "solid", "brightness": float("nan")}]},
        id="scene-brightness-is-nan",
    ),
    pytest.param({"power": {"on": True}}, id="power-is-an-object"),
    pytest.param({"scenes": [{"name": "x", "effect": "solid", "params": [1]}]},
                 id="scene-params-is-a-list"),
]


class TestHostileStateFilesNeverStopTheLightsComingUp:
    """The unit runs unattended under Restart=always.

    A state file the operator has hand-edited into nonsense - and setup.sh tells
    them where it lives - must degrade to defaults rather than crash-loop the
    installation into the dark.
    """

    @pytest.mark.parametrize("document", HOSTILE_DOCUMENTS)
    def test_load_recovers_rather_than_raising(self, tmp_path, document):
        path = tmp_path / "state.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        state = StateStore(path).load()

        # Whatever it fell back to, it is a state the engine can render.
        assert isinstance(state.revision, int)
        assert math.isfinite(state.brightness)
        assert 0.0 <= state.brightness <= 1.0
        assert effects.get(state.effect) is not None
        assert state.params == effects.get(state.effect).coerce_params(state.params)
        assert all(
            math.isfinite(v) for v in state.params.values() if isinstance(v, (int, float))
        )

    def test_a_hand_edited_revision_falls_back_to_zero(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"revision": "abc", "effect": "fire"}), encoding="utf-8")

        state = StateStore(path).load()

        assert state.revision == 0
        assert state.effect == "fire"

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    @pytest.mark.parametrize("key", ["created_at", "updated_at"])
    def test_a_scene_with_a_non_finite_timestamp_is_dropped_not_kept(
        self, tmp_path, key, literal
    ):
        # A NaN here used to reach live state and come back out of the WebSocket
        # as a bare `NaN` token, which is not valid JSON, so the first frame the
        # phone receives was unparseable - and StateStore.save wrote it back.
        path = tmp_path / "state.json"
        path.write_text(
            '{"scenes": ['
            f'{{"id": "a", "name": "poisoned", "effect": "solid", "{key}": {literal}}},'
            '{"id": "b", "name": "ok", "effect": "solid"}]}',
            encoding="utf-8",
        )

        state = StateStore(path).load()

        assert [scene.name for scene in state.scenes] == ["ok"]

    def test_a_scene_timestamp_too_large_for_a_float_loses_only_that_scene(self, tmp_path):
        # float(10**400) raises OverflowError, which is not a ValueError, so it
        # used to escape past the per-scene guard and cost the whole document.
        path = tmp_path / "state.json"
        path.write_text(
            '{"scenes": ['
            '{"id": "a", "name": "poisoned", "effect": "solid", "created_at": 1'
            + "0" * 400
            + '},{"id": "b", "name": "ok", "effect": "solid"}]}',
            encoding="utf-8",
        )

        state = StateStore(path).load()

        assert [scene.name for scene in state.scenes] == ["ok"]

    def test_an_oversized_param_costs_the_effect_not_the_scenes(self, tmp_path):
        # state.py promises per-field repair. math.isfinite() raises
        # OverflowError for a very wide int, which escaped the ParamError guard
        # and took the whole document - and every saved scene - down with it.
        path = tmp_path / "state.json"
        path.write_text(
            '{"effect": "breathe", "params": {"speed": 1'
            + "0" * 400
            + '}, "scenes": [{"id": "b", "name": "keep", "effect": "solid"}]}',
            encoding="utf-8",
        )

        state = StateStore(path).load()

        assert [scene.name for scene in state.scenes] == ["keep"]
        assert state.params == effects.get(state.effect).defaults()

    def test_restored_state_is_always_serialisable_as_strict_json(self, tmp_path):
        # docs/api.md types every scene timestamp and brightness as a number,
        # and the WebSocket hello frame is json.dumps of exactly this payload.
        path = tmp_path / "state.json"
        path.write_text(
            '{"brightness": NaN, "scenes": ['
            '{"id": "a", "name": "bad", "effect": "solid", "created_at": Infinity},'
            '{"id": "b", "name": "ok", "effect": "solid"}]}',
            encoding="utf-8",
        )

        payload = StateStore(path).load().to_dict()

        # allow_nan=False is what Starlette's JSONResponse uses; the WebSocket
        # path is the permissive default, so the payload itself has to be clean.
        json.dumps(payload, allow_nan=False)

    def test_a_persisted_scene_with_a_non_finite_value_is_dropped(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps(
                {
                    "scenes": [
                        {"id": "a", "name": "good", "effect": "solid", "params": {}},
                        {
                            "id": "b",
                            "name": "poisoned",
                            "effect": "breathe",
                            "params": {"speed": float("nan")},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        state = StateStore(path).load()

        assert [scene.name for scene in state.scenes] == ["good"]

    def test_a_document_of_pure_garbage_still_boots(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{\"scenes\": [[[[1]]]], \"revision\": [], \"version\": {}}",
                        encoding="utf-8")

        state = StateStore(path).load()

        assert state == default_state()

    def test_truncated_json_still_boots(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"effect": "fi', encoding="utf-8")
        assert StateStore(path).load() == default_state()


class TestSerialisation:
    def test_state_payload_is_json_clean(self, store):
        store.set_effect("twinkle", {"seed": 3})
        store.save_scene("Sparkle")
        json.dumps(store.state.to_dict())

    def test_payload_carries_everything_a_client_needs(self, store):
        payload = store.state.to_dict()
        assert set(payload) == {
            "power",
            "brightness",
            "effect",
            "params",
            "scenes",
            "active_scene",
            "revision",
        }

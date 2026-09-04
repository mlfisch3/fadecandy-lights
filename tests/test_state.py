"""State mutation and persistence.

Persistence matters more than it looks: this is a fixed installation, so the
rig has to come back on the scene it was showing before the power cut without
anyone reaching for a phone.
"""

from __future__ import annotations

import json

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

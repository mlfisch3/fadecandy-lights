"""Runtime state and its persistence.

This is everything a phone can change: master power, global brightness, the
selected effect and its parameters, and saved scenes.  It is written to disk as
JSON so the rig comes back up on the scene it was showing before the power cut,
which for a fixed installation is the difference between a light fitting and a
science project.

Writes are atomic (temp file plus rename) and coalesced by the caller, so a
finger dragging a brightness slider at 60 Hz does not thrash the SD card.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from fclights import effects

log = logging.getLogger(__name__)

STATE_VERSION = 1
MAX_SCENES = 256
MAX_SCENE_NAME = 120


class StateError(ValueError):
    """Raised when a state mutation or a persisted document is not usable."""


@dataclass(frozen=True)
class Scene:
    """A saved look: an effect, its parameters, and the brightness to show it at."""

    id: str
    name: str
    effect: str
    params: dict[str, Any]
    brightness: float
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scene:
        try:
            effect_name = str(raw["effect"])
            effect_cls = effects.get(effect_name)
            now = time.time()
            return cls(
                id=str(raw.get("id") or uuid.uuid4().hex[:12]),
                name=str(raw["name"]),
                effect=effect_name,
                params=effect_cls.coerce_params(raw.get("params") or {}),
                brightness=_clamp_unit(float(raw.get("brightness", 1.0))),
                created_at=float(raw.get("created_at", now)),
                updated_at=float(raw.get("updated_at", now)),
            )
        except (KeyError, TypeError, ValueError, effects.UnknownEffectError) as exc:
            raise StateError(f"unusable scene: {exc}") from exc


@dataclass(frozen=True)
class State:
    """The live, phone-visible state of the installation."""

    power: bool = True
    brightness: float = 0.35
    """Global master brightness, 0..1, applied before the power governor.

    The default is modest on purpose. Full brightness on 512 pixels wants ~30 A;
    starting dim means the first boot on unknown wiring is uneventful.
    """

    effect: str = effects.DEFAULT_EFFECT
    params: dict[str, Any] = field(default_factory=dict)
    scenes: tuple[Scene, ...] = ()
    active_scene: str | None = None
    """Id of the scene currently showing, cleared as soon as anything is tweaked."""

    revision: int = 0
    """Bumped on every change. Lets a client tell stale WebSocket frames apart."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "power": self.power,
            "brightness": self.brightness,
            "effect": self.effect,
            "params": dict(self.params),
            "scenes": [s.to_dict() for s in self.scenes],
            "active_scene": self.active_scene,
            "revision": self.revision,
        }

    def scene(self, scene_id: str) -> Scene:
        for candidate in self.scenes:
            if candidate.id == scene_id:
                return candidate
        raise StateError(f"no scene with id {scene_id!r}")


def _clamp_unit(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def default_state() -> State:
    """A valid starting state: the default effect at its default parameters."""
    effect_cls = effects.get(effects.DEFAULT_EFFECT)
    return State(effect=effect_cls.name, params=effect_cls.defaults())


def state_from_dict(raw: dict[str, Any]) -> State:
    """Rebuild state from a persisted document, repairing what it safely can.

    Restoring the last scene must never be the reason the rig fails to boot, so
    anything unrecognisable here falls back to a default rather than raising:
    an effect that was removed since the state was written, a parameter whose
    range narrowed, a scene list from a newer version.
    """
    if not isinstance(raw, dict):
        raise StateError("state document must be a JSON object")

    version = int(raw.get("version", STATE_VERSION))
    if version > STATE_VERSION:
        raise StateError(
            f"state file is version {version}, but this build only understands {STATE_VERSION}"
        )

    base = default_state()

    effect_name = str(raw.get("effect", base.effect))
    try:
        effect_cls = effects.get(effect_name)
    except effects.UnknownEffectError:
        log.warning("persisted effect %r is no longer registered; falling back to %r",
                    effect_name, base.effect)
        effect_cls = effects.get(base.effect)

    try:
        params = effect_cls.coerce_params(raw.get("params") or {})
    except effects.ParamError as exc:
        log.warning("persisted parameters for %r are unusable (%s); using defaults",
                    effect_cls.name, exc)
        params = effect_cls.defaults()

    scenes: list[Scene] = []
    for scene_raw in raw.get("scenes") or []:
        try:
            scenes.append(Scene.from_dict(scene_raw))
        except StateError as exc:
            log.warning("dropping unusable saved scene: %s", exc)

    active = raw.get("active_scene")
    if active is not None and not any(s.id == active for s in scenes):
        active = None

    try:
        brightness = _clamp_unit(float(raw.get("brightness", base.brightness)))
    except (TypeError, ValueError):
        brightness = base.brightness

    return State(
        power=bool(raw.get("power", base.power)),
        brightness=brightness,
        effect=effect_cls.name,
        params=params,
        scenes=tuple(scenes),
        active_scene=active,
        revision=int(raw.get("revision", 0)),
    )


class StateStore:
    """Holds the live state and mirrors it to disk.

    Every mutator returns the new state and bumps ``revision``.  Callers are
    expected to broadcast the result; see :mod:`fclights.api`.
    """

    def __init__(self, path: Path | str | None, *, persist: bool = True) -> None:
        self.path = Path(path) if path is not None else None
        self.persist = persist and self.path is not None
        self._state = default_state()
        self._dirty = False

    @property
    def state(self) -> State:
        return self._state

    def load(self) -> State:
        """Read persisted state, falling back to defaults if there is none."""
        if self.path is None or not self.path.exists():
            return self._state
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._state = state_from_dict(raw)
            log.info("restored state from %s (revision %d)", self.path, self._state.revision)
        except (OSError, json.JSONDecodeError, StateError) as exc:
            # A corrupt state file is not worth refusing to light up over.
            log.error("could not restore state from %s (%s); starting from defaults",
                      self.path, exc)
            self._state = default_state()
        return self._state

    def save(self) -> None:
        """Write state to disk atomically. Never raises; a failed save is logged."""
        if not self.persist or self.path is None:
            self._dirty = False
            return
        document = self._state.to_dict() | {"version": STATE_VERSION}
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(document, indent=2), encoding="utf-8")
            # Rename is atomic within a filesystem, so a power cut mid-write
            # leaves the previous good state rather than a truncated one.
            tmp.replace(self.path)
            self._dirty = False
        except OSError as exc:
            log.error("could not persist state to %s: %s", self.path, exc)
            tmp.unlink(missing_ok=True)

    @property
    def dirty(self) -> bool:
        return self._dirty

    def save_if_dirty(self) -> None:
        if self._dirty:
            self.save()

    def _commit(self, new: State, *, clear_scene: bool = True) -> State:
        if clear_scene:
            new = replace(new, active_scene=None)
        self._state = replace(new, revision=self._state.revision + 1)
        self._dirty = True
        return self._state

    # -- mutators -------------------------------------------------------

    def set_power(self, on: bool) -> State:
        # Power is a master switch over whatever is showing, so it does not
        # count as editing the scene.
        return self._commit(replace(self._state, power=bool(on)), clear_scene=False)

    def set_brightness(self, brightness: float) -> State:
        try:
            value = float(brightness)
        except (TypeError, ValueError) as exc:
            raise StateError(f"brightness must be a number, got {brightness!r}") from exc
        if not 0.0 <= value <= 1.0:
            raise StateError(f"brightness must be between 0 and 1, got {value}")
        return self._commit(replace(self._state, brightness=value))

    def set_effect(self, name: str, params: dict[str, Any] | None = None) -> State:
        effect_cls = effects.get(name)
        coerced = effect_cls.coerce_params(params)
        return self._commit(replace(self._state, effect=effect_cls.name, params=coerced))

    def update_params(self, params: dict[str, Any]) -> State:
        """Merge a partial parameter update into the current effect."""
        effect_cls = effects.get(self._state.effect)
        merged = dict(self._state.params) | dict(params)
        coerced = effect_cls.coerce_params(merged)
        return self._commit(replace(self._state, params=coerced))

    def save_scene(self, name: str, scene_id: str | None = None) -> tuple[State, Scene]:
        """Capture the current look as a scene, or overwrite an existing one."""
        clean = str(name).strip()
        if not clean:
            raise StateError("scene name must not be empty")
        if len(clean) > MAX_SCENE_NAME:
            raise StateError(f"scene name must be at most {MAX_SCENE_NAME} characters")

        now = time.time()
        existing = None
        if scene_id is not None:
            existing = self._state.scene(scene_id)

        scene = Scene(
            id=existing.id if existing else uuid.uuid4().hex[:12],
            name=clean,
            effect=self._state.effect,
            params=dict(self._state.params),
            brightness=self._state.brightness,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

        if existing:
            scenes = tuple(scene if s.id == scene.id else s for s in self._state.scenes)
        else:
            if len(self._state.scenes) >= MAX_SCENES:
                raise StateError(f"scene limit of {MAX_SCENES} reached; delete one first")
            scenes = (*self._state.scenes, scene)

        # The live look is now exactly this scene, so mark it active.
        new = self._commit(
            replace(self._state, scenes=scenes, active_scene=scene.id), clear_scene=False
        )
        return new, scene

    def delete_scene(self, scene_id: str) -> State:
        scene = self._state.scene(scene_id)
        scenes = tuple(s for s in self._state.scenes if s.id != scene.id)
        active = None if self._state.active_scene == scene.id else self._state.active_scene
        return self._commit(
            replace(self._state, scenes=scenes, active_scene=active), clear_scene=False
        )

    def recall_scene(self, scene_id: str) -> State:
        """Make a saved scene the live look."""
        scene = self._state.scene(scene_id)
        try:
            effect_cls = effects.get(scene.effect)
            params = effect_cls.coerce_params(scene.params)
        except (effects.UnknownEffectError, effects.ParamError) as exc:
            raise StateError(f"scene {scene.name!r} can no longer be shown: {exc}") from exc

        return self._commit(
            replace(
                self._state,
                effect=effect_cls.name,
                params=params,
                brightness=scene.brightness,
                active_scene=scene.id,
            ),
            clear_scene=False,
        )

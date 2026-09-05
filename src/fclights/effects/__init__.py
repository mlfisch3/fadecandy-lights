"""Effect registry.

The registry is what makes effects discoverable: the control API serves
:func:`schemas` straight to the Android app, so a new effect module registered
here shows up in the app's picker with working controls and no client change.
"""

from __future__ import annotations

from typing import Any

from fclights.effects.base import (
    Effect,
    ParamError,
    ParamSpec,
    color_to_rgb,
    hsv_to_rgb_array,
)
from fclights.effects.basic import (
    Breathe,
    ColorWipe,
    Fire,
    Gradient,
    Rainbow,
    SlowFade,
    Solid,
    Twinkle,
)

_REGISTRY: dict[str, type[Effect]] = {}


class UnknownEffectError(KeyError):
    """Raised when an effect name is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"unknown effect {self.name!r}; known effects: {', '.join(sorted(_REGISTRY))}"


def register(effect: type[Effect]) -> type[Effect]:
    """Add an effect to the registry. Also usable as a decorator."""
    if not getattr(effect, "name", None):
        raise ValueError(f"{effect.__name__} must set a class-level 'name'")
    existing = _REGISTRY.get(effect.name)
    if existing is not None and existing is not effect:
        raise ValueError(
            f"effect name {effect.name!r} is already registered to {existing.__name__}"
        )
    _REGISTRY[effect.name] = effect
    return effect


def get(name: str) -> type[Effect]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownEffectError(name) from None


def names() -> list[str]:
    return sorted(_REGISTRY)


def all_effects() -> list[type[Effect]]:
    return [_REGISTRY[n] for n in names()]


def schemas() -> list[dict[str, Any]]:
    """Machine-readable descriptions of every registered effect."""
    return [effect.schema() for effect in all_effects()]


for _effect in (Solid, SlowFade, Gradient, Breathe, ColorWipe, Rainbow, Twinkle, Fire):
    register(_effect)

DEFAULT_EFFECT = Solid.name

__all__ = [
    "DEFAULT_EFFECT",
    "Breathe",
    "ColorWipe",
    "Effect",
    "Fire",
    "Gradient",
    "ParamError",
    "ParamSpec",
    "Rainbow",
    "SlowFade",
    "Solid",
    "Twinkle",
    "UnknownEffectError",
    "all_effects",
    "color_to_rgb",
    "get",
    "hsv_to_rgb_array",
    "names",
    "register",
    "schemas",
]

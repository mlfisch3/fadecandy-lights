"""Effect plugin interface and parameter schema.

Effects are plugins.  Each one declares a machine-readable parameter schema, and
the control API serves those schemas verbatim, so the Android app renders its
controls from what the Pi reports rather than from a hardcoded list.  Adding an
effect here makes it appear in the app with no client change.

Rendering contract
------------------
``render`` receives a preallocated ``(N, 3)`` float32 buffer and must fill every
pixel.  Values are 0..1 in the strip's own (pre-gamma) space; gamma and
whitepoint are fcserver's job.  Work over whole frames with numpy - this loop is
meant to run 60 times a second for months on a Pi 3 B+, and a per-pixel Python
loop would spend the board's headroom on nothing.
"""

from __future__ import annotations

import colorsys
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

import numpy as np

from fclights.color import (
    DEFAULT_KELVIN,
    MAX_KELVIN,
    MIN_KELVIN,
    ColorError,
    color_to_array,
    parse_color,
)
from fclights.layout import Layout

ParamType = Literal["float", "int", "bool", "color", "enum"]


class ParamError(ValueError):
    """Raised when a supplied parameter value is not usable."""


@dataclass(frozen=True)
class ParamSpec:
    """One tunable knob on an effect, described well enough to build a UI from."""

    name: str
    type: ParamType
    default: Any
    label: str = ""
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    choices: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        # Normalise a colour default to the canonical object at construction, so
        # `default`, `defaults()` and the published schema all agree with what a
        # coerced value looks like. Otherwise a client reading the schema would
        # see a bare [r, g, b] and a state read would see an object.
        if self.type == "color":
            object.__setattr__(self, "default", parse_color(self.default, field=self.name))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "label": self.label or self.name.replace("_", " ").title(),
            "description": self.description,
        }
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.step is not None:
            out["step"] = self.step
        if self.unit:
            out["unit"] = self.unit
        if self.choices is not None:
            out["choices"] = list(self.choices)
        if self.type == "color":
            # Every colour control here can be driven as a colour temperature,
            # which for apartment lighting is the control that gets used.
            out["supports_kelvin"] = True
            out["kelvin_range"] = [MIN_KELVIN, MAX_KELVIN]
            out["kelvin_default"] = DEFAULT_KELVIN
        return out

    def coerce(self, value: Any) -> Any:
        """Validate and normalise a value for this parameter."""
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            raise ParamError(f"{self.name}: expected true or false, got {value!r}")

        if self.type == "enum":
            text = str(value)
            if self.choices and text not in self.choices:
                raise ParamError(
                    f"{self.name}: {text!r} is not one of {', '.join(self.choices)}"
                )
            return text

        if self.type == "color":
            try:
                return parse_color(value, field=self.name)
            except ColorError as exc:
                raise ParamError(str(exc)) from exc

        # Numeric.  bool is a subclass of int, and silently accepting True as 1
        # hides client bugs, so reject it.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParamError(f"{self.name}: expected a number, got {value!r}")
        # NaN and the infinities have to go before the range check, not after:
        # every comparison against NaN is False, so it would pass any minimum
        # and any maximum and then propagate through the frame into the power
        # governor and the temporal dither's residual. An integer literal wider
        # than a float is the same class of input - json.loads builds those from
        # ordinary JSON, and isfinite() raises OverflowError rather than
        # returning False for them.
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ParamError(f"{self.name}: expected a finite number, got {value!r}")
        number = round(value) if self.type == "int" else float(value)
        if self.minimum is not None and number < self.minimum:
            raise ParamError(f"{self.name}: {number} is below minimum {self.minimum}")
        if self.maximum is not None and number > self.maximum:
            raise ParamError(f"{self.name}: {number} is above maximum {self.maximum}")
        return number


def color_to_rgb(value: Any) -> np.ndarray:
    """Convert a coerced colour parameter to a display-space float32 RGB triple."""
    return color_to_array(value)


def hsv_to_rgb_array(h: np.ndarray, s: np.ndarray | float, v: np.ndarray | float) -> np.ndarray:
    """Vectorised HSV to RGB over an ``(N,)`` hue array.

    ``colorsys`` is scalar-only; this is the whole-frame equivalent.  Hue wraps,
    so callers may pass any real value.
    """
    h = np.asarray(h, dtype=np.float32) % 1.0
    s = np.clip(np.asarray(s, dtype=np.float32), 0.0, 1.0)
    v = np.clip(np.asarray(v, dtype=np.float32), 0.0, 1.0)

    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))

    sector = (i % 6).astype(np.int32)
    zeros = np.zeros_like(h)
    r = np.select(
        [sector == 0, sector == 1, sector == 2, sector == 3, sector == 4, sector == 5],
        [v + zeros, q + zeros, p + zeros, p + zeros, t + zeros, v + zeros],
    )
    g = np.select(
        [sector == 0, sector == 1, sector == 2, sector == 3, sector == 4, sector == 5],
        [t + zeros, v + zeros, v + zeros, q + zeros, p + zeros, p + zeros],
    )
    b = np.select(
        [sector == 0, sector == 1, sector == 2, sector == 3, sector == 4, sector == 5],
        [p + zeros, p + zeros, t + zeros, v + zeros, v + zeros, q + zeros],
    )
    return np.stack([r, g, b], axis=-1).astype(np.float32)


class Effect(ABC):
    """Base class for animation plugins.

    Subclasses set the class-level metadata and implement :meth:`render`.
    Instances are constructed once when the effect is selected and again
    whenever its parameters change, so ``__init__`` is the right place to
    precompute anything that depends only on the layout and the parameters.
    """

    name: ClassVar[str]
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    params: ClassVar[tuple[ParamSpec, ...]] = ()

    def __init__(self, layout: Layout, params: dict[str, Any]) -> None:
        self.layout = layout
        self.values = params

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if getattr(cls, "name", None) and cls.params:
            seen = [p.name for p in cls.params]
            if len(set(seen)) != len(seen):
                raise TypeError(f"effect {cls.name!r} declares a duplicate parameter name")

    @abstractmethod
    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        """Fill ``frame`` for animation time ``t`` seconds, ``dt`` since the last call.

        Implementations must write every pixel; the buffer is reused between
        frames and is not cleared for you.
        """

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """The machine-readable description the control API serves."""
        return {
            "name": cls.name,
            "display_name": cls.display_name or cls.name.replace("_", " ").title(),
            "description": cls.description,
            "params": [p.to_dict() for p in cls.params],
        }

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {p.name: p.default for p in cls.params}

    @classmethod
    def coerce_params(cls, supplied: dict[str, Any] | None) -> dict[str, Any]:
        """Merge ``supplied`` over the defaults, validating every value.

        Unknown keys are an error rather than a silent no-op: a phone sending a
        misspelled parameter should be told, not left wondering why the slider
        does nothing.
        """
        supplied = supplied or {}
        by_name = {p.name: p for p in cls.params}
        unknown = set(supplied) - set(by_name)
        if unknown:
            raise ParamError(
                f"effect {cls.name!r} has no parameter(s): {', '.join(sorted(unknown))}"
            )
        return {
            spec.name: spec.coerce(supplied[spec.name]) if spec.name in supplied else spec.default
            for spec in cls.params
        }


def rgb_from_hsv_scalar(h: float, s: float, v: float) -> tuple[float, float, float]:
    """Scalar HSV helper, for building constant colours at construction time."""
    return colorsys.hsv_to_rgb(h % 1.0, s, v)

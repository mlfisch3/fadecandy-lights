"""Colour: correlated colour temperature, and the colour value model.

This installation is apartment lighting meant to approximate natural light, not
a display piece, so a colour temperature in kelvin is a first-class way to name
a colour here - not a derived convenience. A warm-to-cool slider is the control
that actually gets used day to day; the colour wheel is the special case.

What space are these numbers in?
--------------------------------
Values throughout the engine are 0..1 in *display* space: the same space a
colour picker hands you, and the same space a ``#rrggbb`` literal is in. They
are not linear light. fcserver applies gamma downstream, which is what converts
display space to light output, so a CCT converted here has to be gamma-encoded
to sit in the same space as a hex colour the user picked. Doing anything else
would make ``#ff8000`` and a 2700 K white disagree about what "half" means.

The blackbody conversion therefore runs kelvin -> CIE XYZ -> linear sRGB ->
sRGB transfer function, and stops there. It does not apply fcserver's gamma;
that would be correcting twice.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# The range offered as a warm-to-cool control. 1800 K is candlelight, 2700 K a
# warm domestic bulb, 4000 K neutral, 6500 K overcast daylight.
MIN_KELVIN = 1800.0
MAX_KELVIN = 6500.0
DEFAULT_KELVIN = 2700.0

# The Planckian locus approximation below is only valid over this range.
_LOCUS_MIN_KELVIN = 1667.0
_LOCUS_MAX_KELVIN = 25000.0

# CIE XYZ (D65) to linear sRGB.
_XYZ_TO_LINEAR_SRGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    dtype=np.float64,
)


class ColorError(ValueError):
    """Raised when a colour value cannot be understood."""


def _planckian_xy(kelvin: float) -> tuple[float, float]:
    """CIE 1931 xy chromaticity on the Planckian locus, by the Kim et al. fit.

    This is the standard cubic-spline approximation to the blackbody locus,
    accurate to well under a just-noticeable difference across its range - far
    better than the popular piecewise-log curve fits, and no more code.
    """
    t = float(np.clip(kelvin, _LOCUS_MIN_KELVIN, _LOCUS_MAX_KELVIN))
    inv = 1000.0 / t

    if t <= 4000.0:
        x = -0.2661239 * inv**3 - 0.2343589 * inv**2 + 0.8776956 * inv + 0.179910
    else:
        x = -3.0258469 * inv**3 + 2.1070379 * inv**2 + 0.2226347 * inv + 0.240390

    if t <= 2222.0:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif t <= 4000.0:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483

    return x, y


def _encode_srgb(linear: np.ndarray) -> np.ndarray:
    """Apply the sRGB transfer function, taking linear light to display space."""
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(
        linear <= 0.0031308,
        linear * 12.92,
        1.055 * np.power(linear, 1.0 / 2.4) - 0.055,
    )


def kelvin_to_rgb(kelvin: float) -> np.ndarray:
    """Convert a colour temperature to a display-space float32 RGB triple, 0..1.

    Normalised so the brightest channel is 1.0: this is "the whitest white this
    strip can make at that temperature", and how bright it actually is is the
    business of global brightness and the power governor.
    """
    x, y = _planckian_xy(kelvin)
    if y <= 0.0:
        raise ColorError(f"{kelvin} K does not land on the blackbody locus")

    # xyY with Y = 1, into XYZ.
    xyz = np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64)
    linear = _XYZ_TO_LINEAR_SRGB @ xyz

    # A blackbody at these temperatures sits outside the sRGB gamut at the
    # extremes, giving a small negative channel. Clipping is the right move:
    # the strip cannot make that colour, and the nearest in-gamut one is what
    # the eye reads as that temperature anyway.
    linear = np.clip(linear, 0.0, None)
    peak = float(linear.max())
    if peak <= 0.0:
        raise ColorError(f"{kelvin} K converts to no light at all")
    linear /= peak

    encoded = _encode_srgb(linear)
    return (encoded / float(encoded.max())).astype(np.float32)


def kelvin_to_rgb255(kelvin: float) -> list[int]:
    """Colour temperature as 0..255 components, for API responses."""
    return [round(float(c) * 255.0) for c in kelvin_to_rgb(kelvin)]


# -- the colour value model -------------------------------------------------
#
# A colour parameter's canonical value is an object that remembers *how* the
# colour was chosen. That matters: if a 2700 K white were stored as plain RGB,
# the phone could not put the warm-to-cool slider back where the user left it,
# and every read-back would silently demote a temperature to a swatch.


def parse_color(value: Any, *, field: str = "color") -> dict[str, Any]:
    """Normalise any accepted colour form to the canonical object.

    Accepted on input:

    - ``[r, g, b]`` with components 0..255
    - ``"#rrggbb"`` or ``"#rgb"``
    - ``{"kelvin": 2700}``
    - a canonical object from a previous response

    Always returns ``{"mode": ..., "rgb": [r, g, b]}``, plus ``"kelvin"`` when
    the colour was named as a temperature. ``rgb`` is always present so a client
    can draw a swatch without reimplementing the blackbody maths.
    """
    if isinstance(value, dict):
        return _parse_color_object(value, field)
    if isinstance(value, str):
        return {"mode": "rgb", "rgb": _parse_hex(value, field)}
    if isinstance(value, (list, tuple)):
        return {"mode": "rgb", "rgb": _parse_rgb_sequence(value, field)}
    raise ColorError(
        f"{field}: expected [r, g, b], '#rrggbb', or {{\"kelvin\": K}}, got {value!r}"
    )


def _parse_color_object(value: dict[str, Any], field: str) -> dict[str, Any]:
    mode = value.get("mode")
    if mode == "kelvin" or (mode is None and "kelvin" in value):
        kelvin = value.get("kelvin")
        if isinstance(kelvin, bool) or not isinstance(kelvin, (int, float)):
            raise ColorError(f"{field}: kelvin must be a number, got {kelvin!r}")
        try:
            kelvin = float(kelvin)
        except OverflowError as exc:
            raise ColorError(f"{field}: kelvin is not a usable number: {kelvin!r}") from exc
        if not MIN_KELVIN <= kelvin <= MAX_KELVIN:
            raise ColorError(
                f"{field}: {kelvin:g} K is outside the supported "
                f"{MIN_KELVIN:g}..{MAX_KELVIN:g} K range"
            )
        return {"mode": "kelvin", "kelvin": kelvin, "rgb": kelvin_to_rgb255(kelvin)}

    if mode == "rgb" or (mode is None and "rgb" in value):
        return {"mode": "rgb", "rgb": _parse_rgb_sequence(value.get("rgb"), field)}

    raise ColorError(
        f"{field}: a colour object needs \"kelvin\" or \"rgb\", got {sorted(value)}"
    )


def _parse_hex(text: str, field: str) -> list[int]:
    body = text.strip().lstrip("#")
    if len(body) == 3:
        body = "".join(c * 2 for c in body)
    if len(body) != 6:
        raise ColorError(f"{field}: {text!r} is not a #rrggbb colour")
    try:
        return [int(body[i : i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        raise ColorError(f"{field}: {text!r} is not a #rrggbb colour") from None


def _parse_rgb_sequence(value: Any, field: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ColorError(f"{field}: rgb must be three components, got {value!r}")
    out: list[int] = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise ColorError(f"{field}: rgb components must be numbers 0..255")
        try:
            finite = math.isfinite(component)
        except OverflowError:
            finite = False
        if not finite:
            raise ColorError(f"{field}: rgb components must be finite, got {component!r}")
        out.append(int(np.clip(round(component), 0, 255)))
    return out


def color_to_array(value: dict[str, Any]) -> np.ndarray:
    """Canonical colour object to a display-space float32 RGB triple, 0..1.

    A kelvin colour is recomputed from the temperature rather than read from the
    stored ``rgb``, so it keeps full float precision instead of being rounded
    through 8 bits. That matters for the slow near-identical white fades this
    installation exists for.
    """
    if value.get("mode") == "kelvin":
        return kelvin_to_rgb(float(value["kelvin"]))
    return np.asarray(value["rgb"], dtype=np.float32) / 255.0

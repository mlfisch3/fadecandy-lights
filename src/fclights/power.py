"""Power governor.

WS2812B pixels are three independent constant-current LEDs behind a small
controller die.  Each colour channel draws roughly 20 mA when its PWM duty is
100%, so a pixel at full white is about 60 mA and a 512-pixel run is about
30 A at 5 V.  That is far more than a typical supply, and a strip that browns
out mid-frame does not fail gracefully: the data line gets marginal, the first
pixels flicker, and a sustained overload cooks the supply.

So this is a hard clamp, not a warning.  Every frame, after global brightness
and before the frame leaves the engine, we predict the draw from the actual RGB
buffer and scale the whole frame down if it would exceed the configured supply
ceiling.  Scaling the whole frame keeps hue and relative brightness intact; the
scene just dims.

Gamma note
----------
Gamma correction lives in fcserver, downstream of us (see ``docs/api.md``).
Because gamma > 1 only ever *lowers* an 8-bit value, predicting current from our
pre-gamma buffer over-estimates the real draw.  That is the safe direction, and
it is the default (``gamma = 1.0``).  If you would rather not leave that
headroom on the table you can set the governor's gamma to match fcserver's, at
which point the prediction tracks reality closely - but a mismatch there
under-predicts, so the default stays conservative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Datasheet-ish figures for a WS2812B (5050 package, 5 V).
DEFAULT_MA_PER_CHANNEL = 20.0
"""Current drawn by one colour channel of one pixel at full duty, in mA."""

DEFAULT_IDLE_MA_PER_PIXEL = 1.0
"""Quiescent draw of the pixel's controller die with all channels off, in mA."""

SAFETY_MARGIN = 1.0 - 1e-5
"""Shaved off the clamp scale so float32 rounding cannot land above the ceiling.

Frames are float32, so multiplying by an exactly-fitting ratio can round a few
values up and leave the frame microamps over the limit. That is physically
irrelevant but it makes the ceiling an approximation rather than a guarantee,
and a guarantee is easier to reason about and to test. The cost is a hundredth
of a percent of brightness.
"""


class PowerConfigError(ValueError):
    """Raised when the governor is configured such that it can never pass a frame."""


@dataclass(frozen=True)
class PowerReport:
    """What the governor did to one frame."""

    requested_amps: float
    """Predicted draw of the frame as the engine rendered it."""

    delivered_amps: float
    """Predicted draw of the frame after clamping."""

    limit_amps: float
    scale: float
    """Factor applied to the frame's colour channels. 1.0 means untouched."""

    clamped: bool

    @property
    def headroom_amps(self) -> float:
        return max(0.0, self.limit_amps - self.delivered_amps)

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "requested_amps": round(self.requested_amps, 4),
            "delivered_amps": round(self.delivered_amps, 4),
            "limit_amps": round(self.limit_amps, 4),
            "headroom_amps": round(self.headroom_amps, 4),
            "scale": round(self.scale, 6),
            "clamped": self.clamped,
        }


class PowerGovernor:
    """Predicts strip current and clamps frames that would exceed the supply.

    Parameters
    ----------
    limit_amps:
        Supply ceiling at 5 V.  Set this to the *usable* current of the supply
        feeding the strip, not its nameplate rating - see the sizing section of
        ``README.md``.
    pixel_count:
        Number of pixels the governor is accounting for.
    ma_per_channel:
        Full-duty current of a single colour channel, in mA.
    idle_ma_per_pixel:
        Per-pixel quiescent draw, in mA.  This is spent whether or not anything
        is lit, so it comes off the top of the budget.
    gamma:
        Exponent fcserver will apply downstream.  Leave at 1.0 for a
        conservative estimate; see the module docstring.
    """

    def __init__(
        self,
        limit_amps: float,
        pixel_count: int,
        *,
        ma_per_channel: float = DEFAULT_MA_PER_CHANNEL,
        idle_ma_per_pixel: float = DEFAULT_IDLE_MA_PER_PIXEL,
        gamma: float = 1.0,
    ) -> None:
        if not all(
            math.isfinite(value)
            for value in (limit_amps, ma_per_channel, idle_ma_per_pixel, gamma)
        ):
            # A NaN ceiling would compare False against every frame and turn the
            # hard clamp into a NaN multiply, which is worse than no governor.
            raise PowerConfigError("power figures must be finite numbers")
        if limit_amps <= 0:
            raise PowerConfigError(f"limit_amps must be positive, got {limit_amps}")
        if pixel_count < 0:
            raise PowerConfigError(f"pixel_count must not be negative, got {pixel_count}")
        if ma_per_channel < 0 or idle_ma_per_pixel < 0:
            raise PowerConfigError("current figures must not be negative")
        if gamma <= 0:
            raise PowerConfigError(f"gamma must be positive, got {gamma}")

        self.limit_amps = float(limit_amps)
        self.pixel_count = int(pixel_count)
        self.ma_per_channel = float(ma_per_channel)
        self.idle_ma_per_pixel = float(idle_ma_per_pixel)
        self.gamma = float(gamma)

        self._amps_per_channel = self.ma_per_channel / 1000.0
        self.idle_amps = self.pixel_count * self.idle_ma_per_pixel / 1000.0

        if self.idle_amps >= self.limit_amps:
            raise PowerConfigError(
                f"supply ceiling {self.limit_amps:.2f} A cannot even cover the "
                f"{self.idle_amps:.2f} A quiescent draw of {self.pixel_count} pixels; "
                "raise power.limit_amps or split the run across supplies"
            )

    @property
    def full_white_amps(self) -> float:
        """Draw if every pixel were driven to full white. The worst case."""
        return self.idle_amps + self.pixel_count * 3 * self._amps_per_channel

    def predict_amps(self, frame: np.ndarray) -> float:
        """Predict the draw of a frame of linear 0..1 RGB values, in amps."""
        if frame.size == 0:
            return self.idle_amps
        duty = np.clip(frame, 0.0, 1.0)
        if self.gamma != 1.0:
            duty = duty**self.gamma
        return self.idle_amps + float(duty.sum()) * self._amps_per_channel

    def apply(self, frame: np.ndarray) -> PowerReport:
        """Clamp ``frame`` in place so it fits the supply ceiling.

        ``frame`` is an ``(N, 3)`` float array of 0..1 RGB values, already
        scaled by global brightness.  Returns what was done to it.
        """
        requested = self.predict_amps(frame)
        if requested <= self.limit_amps:
            return PowerReport(
                requested_amps=requested,
                delivered_amps=requested,
                limit_amps=self.limit_amps,
                scale=1.0,
                clamped=False,
            )

        # Idle draw is not something we can scale away, so the budget the lit
        # pixels have to fit into is what is left after it.
        lit_budget = self.limit_amps - self.idle_amps
        lit_requested = requested - self.idle_amps
        # lit_requested > lit_budget > 0 here, so the ratio is a real 0..1 value.
        ratio = lit_budget / lit_requested

        # Scaling the buffer by `ratio` scales the *duty*, which is what current
        # is proportional to.  With a gamma exponent in play, the buffer value
        # has to move by the gamma-th root of the duty ratio to land there.
        scale = ratio ** (1.0 / self.gamma) if self.gamma != 1.0 else ratio
        scale *= SAFETY_MARGIN

        np.multiply(frame, scale, out=frame)
        delivered = self.predict_amps(frame)

        return PowerReport(
            requested_amps=requested,
            delivered_amps=delivered,
            limit_amps=self.limit_amps,
            scale=scale,
            clamped=True,
        )

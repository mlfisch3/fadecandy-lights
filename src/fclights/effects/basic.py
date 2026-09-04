"""The v1 effect set.

Every ``render`` here is whole-frame numpy: no Python loop touches a pixel.
"""

from __future__ import annotations

import numpy as np

from fclights.color import DEFAULT_KELVIN
from fclights.effects.base import Effect, ParamSpec, color_to_rgb, hsv_to_rgb_array
from fclights.layout import Layout

AUTO_SEED = -1
"""Seed value meaning "draw a fresh one each time".

It sits below the usable range on purpose. Every seed a client can pick from
0..2**31-1 replays exactly, which is what the parameter's published description
promises; a sentinel *inside* that range would make one ordinary-looking value -
and it was the default - the single one that did not.
"""


def _seeded_rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(None if seed <= AUTO_SEED else seed)


def _speed_spec(default: float = 0.2, maximum: float = 5.0) -> ParamSpec:
    return ParamSpec(
        name="speed",
        type="float",
        default=default,
        minimum=0.0,
        maximum=maximum,
        step=0.01,
        unit="Hz",
        description="Cycles per second. 0 freezes the animation.",
    )


class Solid(Effect):
    name = "solid"
    display_name = "Solid Colour"
    description = "One steady colour, or one steady white, across the whole installation."
    params = (
        ParamSpec(
            name="color",
            type="color",
            default={"kelvin": DEFAULT_KELVIN},
            label="Colour",
            description="The colour to hold. Set it as a temperature for plain white light.",
        ),
    )

    def __init__(self, layout: Layout, params: dict) -> None:
        super().__init__(layout, params)
        self._rgb = color_to_rgb(params["color"])

    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        frame[:] = self._rgb


class Gradient(Effect):
    name = "gradient"
    display_name = "Gradient Sweep"
    description = "A two-colour gradient that slides along the run."
    params = (
        ParamSpec(name="color_a", type="color", default={"kelvin": 2200.0}, label="Start Colour"),
        ParamSpec(name="color_b", type="color", default={"kelvin": 5000.0}, label="End Colour"),
        _speed_spec(0.1),
        ParamSpec(
            name="cycles",
            type="float",
            default=1.0,
            minimum=0.1,
            maximum=10.0,
            step=0.1,
            description="How many full colour A to B to A repeats fit along the run.",
        ),
        ParamSpec(
            name="axis",
            type="enum",
            default="run",
            choices=("run", "x", "y", "z"),
            description="Sweep along the strip order, or along a spatial axis.",
        ),
    )

    def __init__(self, layout: Layout, params: dict) -> None:
        super().__init__(layout, params)
        self._a = color_to_rgb(params["color_a"])
        self._b = color_to_rgb(params["color_b"])
        self._pos = _axis_positions(layout, params["axis"])
        self._cycles = float(params["cycles"])
        self._speed = float(params["speed"])

    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        # One "cycle" is a full colour A to colour B and back along the run.
        # A triangle wave rather than a sawtooth, so the gradient reverses at
        # the ends instead of snapping back and showing a seam as it slides.
        phase = 2.0 * (self._pos * self._cycles - t * self._speed)
        mix = (1.0 - np.abs((phase % 2.0) - 1.0)).astype(np.float32)[:, None]
        np.multiply(self._b - self._a, mix, out=frame)
        np.add(frame, self._a, out=frame)


class Breathe(Effect):
    name = "breathe"
    display_name = "Breathe"
    description = "A colour pulsing smoothly between two brightness levels."
    params = (
        ParamSpec(name="color", type="color", default={"kelvin": DEFAULT_KELVIN},
                  label="Colour"),
        _speed_spec(0.25, maximum=3.0),
        ParamSpec(
            name="minimum",
            type="float",
            default=0.05,
            minimum=0.0,
            maximum=1.0,
            step=0.01,
            label="Dimmest",
            description="Brightness at the bottom of the breath.",
        ),
        ParamSpec(
            name="maximum",
            type="float",
            default=1.0,
            minimum=0.0,
            maximum=1.0,
            step=0.01,
            label="Brightest",
            description="Brightness at the top of the breath.",
        ),
    )

    def __init__(self, layout: Layout, params: dict) -> None:
        super().__init__(layout, params)
        self._rgb = color_to_rgb(params["color"])
        self._speed = float(params["speed"])
        lo = float(params["minimum"])
        hi = float(params["maximum"])
        # Tolerate a client that sets the floor above the ceiling.
        self._lo, self._hi = min(lo, hi), max(lo, hi)

    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        # A raised cosine spends longer near the extremes than a sine does,
        # which is what reads as a breath rather than a throb.
        wave = 0.5 - 0.5 * np.cos(2.0 * np.pi * self._speed * t)
        level = self._lo + (self._hi - self._lo) * float(wave)
        frame[:] = self._rgb * level


class ColorWipe(Effect):
    name = "wipe"
    display_name = "Colour Wipe"
    description = "A colour front travelling along the run, over a background."
    params = (
        ParamSpec(name="color", type="color", default={"kelvin": 4000.0}, label="Wipe Colour"),
        ParamSpec(name="background", type="color", default=[0, 0, 0]),
        _speed_spec(0.4),
        ParamSpec(
            name="softness",
            type="float",
            default=0.03,
            minimum=0.0,
            maximum=0.5,
            step=0.005,
            description="Width of the fade at the leading edge, as a fraction of the run.",
        ),
        ParamSpec(
            name="bounce",
            type="bool",
            default=False,
            description="Sweep back and forth instead of restarting from the beginning.",
        ),
        ParamSpec(name="axis", type="enum", default="run", choices=("run", "x", "y", "z")),
    )

    def __init__(self, layout: Layout, params: dict) -> None:
        super().__init__(layout, params)
        self._fg = color_to_rgb(params["color"])
        self._bg = color_to_rgb(params["background"])
        self._pos = _axis_positions(layout, params["axis"])
        self._speed = float(params["speed"])
        self._bounce = bool(params["bounce"])
        # A zero-width edge would make the mask a hard step; keep a floor so the
        # division below stays finite and the front still looks intentional.
        self._softness = max(float(params["softness"]), 1e-4)

    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        phase = (t * self._speed) % 1.0
        if self._bounce:
            cycle = (t * self._speed) % 2.0
            phase = cycle if cycle <= 1.0 else 2.0 - cycle
        # Everything behind the front is wiped; the edge fades over `softness`.
        mix = np.clip((phase - self._pos) / self._softness + 1.0, 0.0, 1.0)[:, None]
        np.multiply(self._fg - self._bg, mix, out=frame)
        np.add(frame, self._bg, out=frame)


class Rainbow(Effect):
    name = "rainbow"
    display_name = "Rainbow"
    description = "A hue sweep rolling along the run."
    params = (
        _speed_spec(0.15),
        ParamSpec(
            name="cycles",
            type="float",
            default=1.0,
            minimum=0.1,
            maximum=10.0,
            step=0.1,
            description="How many full rainbows fit along the run.",
        ),
        ParamSpec(
            name="saturation", type="float", default=1.0, minimum=0.0, maximum=1.0, step=0.01
        ),
        ParamSpec(name="axis", type="enum", default="run", choices=("run", "x", "y", "z")),
    )

    def __init__(self, layout: Layout, params: dict) -> None:
        super().__init__(layout, params)
        self._pos = _axis_positions(layout, params["axis"])
        self._speed = float(params["speed"])
        self._cycles = float(params["cycles"])
        self._saturation = float(params["saturation"])

    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        hue = self._pos * self._cycles + t * self._speed
        frame[:] = hsv_to_rgb_array(hue, self._saturation, 1.0)


class Twinkle(Effect):
    name = "twinkle"
    display_name = "Twinkle"
    description = "Pixels igniting at random and fading out over a warm base."
    params = (
        ParamSpec(name="color", type="color", default={"kelvin": 3200.0}, label="Spark Colour"),
        ParamSpec(name="background", type="color", default=[6, 4, 12]),
        ParamSpec(
            name="density",
            type="float",
            default=6.0,
            minimum=0.0,
            maximum=200.0,
            step=0.5,
            unit="sparks/s",
            description="New sparks lit per second across the whole installation.",
        ),
        ParamSpec(
            name="decay",
            type="float",
            default=1.2,
            minimum=0.05,
            maximum=10.0,
            step=0.05,
            unit="s",
            label="Fade Time",
            description="Seconds for a spark to fade to roughly a tenth of its peak.",
        ),
        ParamSpec(
            name="color_jitter",
            type="float",
            default=0.05,
            minimum=0.0,
            maximum=0.5,
            step=0.01,
            description="Random hue spread applied to each spark.",
        ),
        ParamSpec(
            name="seed",
            type="int",
            default=0,
            minimum=AUTO_SEED,
            maximum=2**31 - 1,
            description=(
                "Random seed. The same seed replays the same twinkle. "
                "-1 draws a fresh one each time instead."
            ),
        ),
    )

    def __init__(self, layout: Layout, params: dict) -> None:
        super().__init__(layout, params)
        self._fg = color_to_rgb(params["color"])
        self._bg = color_to_rgb(params["background"])
        self._density = float(params["density"])
        self._jitter = float(params["color_jitter"])
        # Jitter is a hue spread, so a jittered spark keeps the chosen colour's
        # saturation and brightness and only its hue moves.
        self._fg_hue = _rgb_to_hue(self._fg)
        self._fg_saturation = _rgb_to_saturation(self._fg)
        self._fg_value = float(self._fg.max())
        # Exponential decay to 1/10 over `decay` seconds.
        self._decay_rate = np.log(10.0) / float(params["decay"])
        self._rng = _seeded_rng(int(params["seed"]))
        n = layout.pixel_count
        self._energy = np.zeros(n, dtype=np.float32)
        self._tint = np.tile(self._fg, (n, 1))

    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        n = self.layout.pixel_count
        if dt > 0:
            self._energy *= np.exp(-self._decay_rate * dt, dtype=np.float32)

            # Sparks arrive as a Poisson process; draw the count for this frame
            # and pick that many pixels, rather than rolling a die per pixel.
            expected = self._density * dt
            count = int(self._rng.poisson(expected)) if expected > 0 else 0
            if count:
                idx = self._rng.integers(0, n, size=min(count, n))
                self._energy[idx] = 1.0
                if self._jitter > 0:
                    hue_shift = self._rng.uniform(-self._jitter, self._jitter, size=idx.size)
                    self._tint[idx] = hsv_to_rgb_array(
                        self._fg_hue + hue_shift, self._fg_saturation, self._fg_value
                    )

        e = self._energy[:, None]
        np.multiply(self._tint, e, out=frame)
        np.add(frame, self._bg * (1.0 - e), out=frame)


class Fire(Effect):
    name = "fire"
    display_name = "Fire"
    description = "A flickering flame gradient, hottest at the base of each run."
    params = (
        ParamSpec(
            name="cooling",
            type="float",
            default=0.55,
            minimum=0.0,
            maximum=1.0,
            step=0.01,
            description="How fast heat bleeds away. Higher makes a shorter flame.",
        ),
        ParamSpec(
            name="sparking",
            type="float",
            default=0.5,
            minimum=0.0,
            maximum=1.0,
            step=0.01,
            description="Chance per frame of a new ember at the base of a run.",
        ),
        _speed_spec(1.0, maximum=4.0),
        ParamSpec(
            name="hue",
            type="float",
            default=0.05,
            minimum=0.0,
            maximum=1.0,
            step=0.005,
            description="Base hue. 0.05 is orange fire, 0.55 is a cold blue flame.",
        ),
        ParamSpec(
            name="per_segment",
            type="bool",
            default=True,
            description="Burn each output as its own flame instead of one run-long flame.",
        ),
        ParamSpec(
            name="seed",
            type="int",
            default=0,
            minimum=AUTO_SEED,
            maximum=2**31 - 1,
            description=(
                "Random seed. The same seed replays the same flame. "
                "-1 draws a fresh one each time instead."
            ),
        ),
    )

    def __init__(self, layout: Layout, params: dict) -> None:
        super().__init__(layout, params)
        self._cooling = float(params["cooling"])
        self._sparking = float(params["sparking"])
        self._speed = float(params["speed"])
        self._hue = float(params["hue"])
        self._rng = _seeded_rng(int(params["seed"]))

        n = layout.pixel_count
        self._heat = np.zeros(n, dtype=np.float32)

        # Precompute the "one pixel closer to the base" gather index, so the
        # heat-rises step is a single vectorised take rather than a shift loop.
        groups = layout.segment if params["per_segment"] else np.zeros(n, dtype=np.int32)
        # Cooling is scaled by the length of the run a pixel belongs to, not by
        # the size of the installation: a flame has to look the same on a given
        # output whether that output is the only one or one of twenty-four.
        self._group_length = np.maximum(np.bincount(groups)[groups], 1).astype(np.float32)
        idx = np.arange(n, dtype=np.int64)
        below = np.maximum(idx - 1, 0)
        # At a segment boundary there is no pixel below, so it feeds from itself.
        below = np.where(groups[below] == groups, below, idx)
        self._below = below
        two_below = np.maximum(below - 1, 0)
        self._below2 = np.where(groups[two_below] == groups, two_below, below)
        self._is_base = groups != groups[np.maximum(idx - 1, 0)]
        self._is_base[0] = True
        self._base_idx = np.flatnonzero(self._is_base)
        self._accumulator = 0.0

    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        # Fire is a cellular automaton, so it steps at a fixed rate rather than
        # with wall-clock dt; `speed` scales how many steps a second buys.
        self._accumulator += dt * self._speed * 30.0
        steps = int(self._accumulator)
        self._accumulator -= steps
        for _ in range(min(steps, 4)):
            self._step()

        # Map heat onto a black-body-ish ramp: dark red through orange to white.
        heat = np.clip(self._heat, 0.0, 1.0)
        hue = self._hue + heat * 0.09
        saturation = np.clip(1.6 - heat * 1.6, 0.0, 1.0)
        value = np.clip(heat * 1.4, 0.0, 1.0)
        frame[:] = hsv_to_rgb_array(hue, saturation, value)

    def _step(self) -> None:
        n = self._heat.size

        # Cool every cell by a random amount, more for cells further from base.
        cooling = (
            self._rng.random(n).astype(np.float32)
            * self._cooling
            * (10.0 / self._group_length)
        )
        np.subtract(self._heat, cooling, out=self._heat)
        np.clip(self._heat, 0.0, 1.0, out=self._heat)

        # Heat drifts up the run: each cell becomes a blend of the two below it.
        self._heat = (self._heat[self._below] * 0.55 + self._heat[self._below2] * 0.45).astype(
            np.float32
        )

        # Random embers at the base of each run.
        if self._sparking > 0 and self._base_idx.size:
            lit = self._rng.random(self._base_idx.size) < self._sparking
            if lit.any():
                targets = self._base_idx[lit]
                self._heat[targets] = np.maximum(
                    self._heat[targets],
                    self._rng.uniform(0.6, 1.0, size=targets.size).astype(np.float32),
                )


def _axis_positions(layout: Layout, axis: str) -> np.ndarray:
    """0..1 positions to animate along, for the named axis."""
    if axis == "run":
        return layout.u
    return layout.normalized[:, {"x": 0, "y": 1, "z": 2}[axis]]


def _rgb_to_hue(rgb: np.ndarray) -> float:
    r, g, b = (float(c) for c in rgb)
    hi, lo = max(r, g, b), min(r, g, b)
    if hi == lo:
        return 0.0
    delta = hi - lo
    if hi == r:
        hue = ((g - b) / delta) % 6.0
    elif hi == g:
        hue = (b - r) / delta + 2.0
    else:
        hue = (r - g) / delta + 4.0
    return hue / 6.0


def _rgb_to_saturation(rgb: np.ndarray) -> float:
    hi = float(max(rgb))
    lo = float(min(rgb))
    return 0.0 if hi == 0 else (hi - lo) / hi


class SlowFade(Effect):
    """The effect this installation mostly exists for.

    A very slow crossfade between two colours, typically two near-identical
    warm whites, over minutes rather than seconds. This is the hardest case for
    an 8-bit strip: over a ten minute fade between 2700 K and 2900 K a channel
    moves by a handful of codes, and a naive pipeline shows a visible staircase
    with each step held for a minute at a time.

    Nothing special is needed here to fix that, because the pipeline works in
    float end to end and the temporal dither at the 8-bit encode carries the
    sub-step precision (see :mod:`fclights.opc`). What this effect must do is
    not quantise on its own: it interpolates between float colours, never
    between their 8-bit forms.
    """

    name = "slowfade"
    display_name = "Slow Fade"
    description = "A very slow crossfade between two colours, over minutes."
    params = (
        ParamSpec(
            name="color_a",
            type="color",
            default={"kelvin": 2700.0},
            label="From",
            description="The colour the cycle starts and ends on.",
        ),
        ParamSpec(
            name="color_b",
            type="color",
            default={"kelvin": 3400.0},
            label="To",
            description="The colour at the far end of the cycle.",
        ),
        ParamSpec(
            name="period",
            type="float",
            default=900.0,
            minimum=10.0,
            maximum=21600.0,
            step=10.0,
            unit="s",
            label="Cycle Length",
            description=(
                "Seconds for a full round trip, from the first colour to the "
                "second and back. 900 is fifteen minutes; the maximum is six hours."
            ),
        ),
        ParamSpec(
            name="hold",
            type="float",
            default=0.2,
            minimum=0.0,
            maximum=0.5,
            step=0.05,
            label="Dwell",
            description=(
                "Fraction of the cycle spent sitting at each end before moving "
                "again. 0 fades continuously; 0.5 is the maximum, because two "
                "ends of half a cycle each already fill the cycle."
            ),
        ),
        ParamSpec(
            name="easing",
            type="enum",
            default="smooth",
            choices=("smooth", "linear"),
            description="Smooth eases in and out of each end; linear ramps evenly.",
        ),
    )

    def __init__(self, layout: Layout, params: dict) -> None:
        super().__init__(layout, params)
        self._a = color_to_rgb(params["color_a"])
        self._b = color_to_rgb(params["color_b"])
        self._period = float(params["period"])
        self._hold = float(params["hold"])
        self._smooth = params["easing"] == "smooth"

    def _mix_at(self, t: float) -> float:
        # Triangle over the cycle: out on the first half, back on the second.
        phase = (t % self._period) / self._period
        ramp = phase * 2.0 if phase < 0.5 else (1.0 - phase) * 2.0

        if self._hold > 0.0:
            # `hold` is the fraction of the cycle parked at *each* end, so the
            # two dwells cost 2 * hold between them and the travel is compressed
            # into what is left.
            travel = 1.0 - 2.0 * self._hold
            if travel <= 0.0:
                # hold == 0.5: the two dwells fill the cycle exactly and there
                # is no travel left to spread the crossfade over.
                ramp = 0.0 if ramp < self._hold else 1.0
            else:
                ramp = float(np.clip((ramp - self._hold) / travel, 0.0, 1.0))

        if self._smooth:
            # Smoothstep, so the fade eases out of one end and into the other
            # instead of changing direction abruptly at the turnaround.
            ramp = ramp * ramp * (3.0 - 2.0 * ramp)
        return ramp

    def render(self, frame: np.ndarray, t: float, dt: float) -> None:
        mix = np.float32(self._mix_at(t))
        # Interpolating the float colours, never their 8-bit forms.
        frame[:] = self._a + (self._b - self._a) * mix

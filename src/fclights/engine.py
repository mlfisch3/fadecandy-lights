"""The render loop.

One asyncio task renders frames at a fixed rate and pushes them to the OPC sink.
The pipeline for every frame is:

1. the active effect fills an ``(N, 3)`` float32 buffer with 0..1 values
2. global brightness scales it
3. the power governor clamps it to the supply ceiling  (hard, not advisory)
4. it is encoded to 8-bit OPC messages, one per device channel, and sent

Gamma and whitepoint are deliberately absent: they live in fcserver's ``color``
block, downstream of us.  Applying them here as well would double-correct and
crush the low end, which is exactly the range the Fadecandy's dithering exists
to rescue.

Everything is whole-frame numpy.  At 512 pixels and 60 fps that should cost a
Pi 3 B+ a low single-digit percentage of one core, which is what should let this
run for months without cooking the board and leaves room to grow past one
Fadecandy.  That is reasoned from the frame arithmetic, not measured on a board;
``GET /api/status`` reports ``render_ms`` so it can be checked on the real one.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from fclights import effects
from fclights.config import Config
from fclights.layout import Layout
from fclights.opc import FrameSink, TemporalDither, encode_frame, quantize_plain
from fclights.power import PowerGovernor, PowerReport
from fclights.state import State

log = logging.getLogger(__name__)


@dataclass
class EngineStats:
    """What the engine is doing right now, for ``GET /api/status``."""

    frames_rendered: int = 0
    frames_sent: int = 0
    frames_dropped: int = 0
    measured_fps: float = 0.0
    render_ms: float = 0.0
    late_frames: int = 0
    """Frames whose deadline had already passed when we got to them."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames_rendered": self.frames_rendered,
            "frames_sent": self.frames_sent,
            "frames_dropped": self.frames_dropped,
            "measured_fps": round(self.measured_fps, 2),
            "render_ms": round(self.render_ms, 3),
            "late_frames": self.late_frames,
        }


class Engine:
    """Renders the current state to the strip, continuously.

    The engine does not own state; it is handed a :class:`~fclights.state.State`
    and rebuilds its effect instance only when the effect's own identity - its
    name and its parameters - changes.  That keeps the API free to mutate state
    without reaching into the render loop, and it means a brightness or master
    power change cannot reset a stateful effect's accumulated heat or energy.
    """

    def __init__(
        self,
        layout: Layout,
        sink: FrameSink,
        config: Config,
        *,
        governor: PowerGovernor | None = None,
    ) -> None:
        self.layout = layout
        self.sink = sink
        self.config = config
        self.fps = config.fps
        self.governor = governor or PowerGovernor(
            limit_amps=config.power.limit_amps,
            pixel_count=layout.pixel_count,
            ma_per_channel=config.power.ma_per_channel,
            idle_ma_per_pixel=config.power.idle_ma_per_pixel,
            gamma=config.power.gamma,
        )

        self._frame = np.zeros((layout.pixel_count, 3), dtype=np.float32)
        # A Fadecandy addresses output n from device pixel 64 * n, but the frame
        # packs outputs back to back, so a short output has to be expanded into
        # the board's address space on the way out. Gaps are board pixel slots
        # with no LED wired to them and stay black for the life of the process.
        self._channels = layout.channel_maps()
        self._channel_buffers: list[np.ndarray | None] = [
            None
            if cmap.contiguous
            else np.zeros((cmap.device_pixel_count, 3), dtype=np.uint8)
            for cmap in self._channels
        ]
        # fcserver's OPC input is 8-bit only, so this is where float precision
        # would be lost. See fclights.opc for why that matters here.
        self._dither = (
            TemporalDither((layout.pixel_count, 3)) if config.dither else None
        )
        self._effect: effects.Effect | None = None
        self._effect_key: tuple[str, dict[str, Any]] | None = None
        self._state = State()
        self._animation_time = 0.0
        self._last_render: float | None = None

        self.stats = EngineStats()
        self.last_report: PowerReport | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # -- state plumbing -------------------------------------------------

    def apply_state(self, state: State) -> None:
        """Adopt a new state. Rebuilds the effect only if it actually changed."""
        self._state = state

    def _ensure_effect(self) -> effects.Effect:
        # Keyed on what actually determines the effect. The revision counter is
        # bumped by brightness, power and scene edits too, and rebuilding on
        # those would zero a Fire's heat or a Twinkle's energy every frame the
        # slider moves.
        key = (self._state.effect, self._state.params)
        if self._effect is not None and self._effect_key == key:
            return self._effect
        try:
            effect_cls = effects.get(self._state.effect)
            params = effect_cls.coerce_params(self._state.params)
            self._effect = effect_cls(self.layout, params)
        except (effects.UnknownEffectError, effects.ParamError) as exc:
            # Never let a bad parameter take the lights down; hold the last
            # good effect and say so.
            log.error("cannot build effect %r (%s); keeping the previous one",
                      self._state.effect, exc)
            if self._effect is None:
                fallback = effects.get(effects.DEFAULT_EFFECT)
                self._effect = fallback(self.layout, fallback.defaults())
        # Recorded even when the build failed, so a bad parameter is reported
        # once rather than once per frame.
        self._effect_key = (self._state.effect, copy.deepcopy(self._state.params))
        return self._effect

    # -- rendering ------------------------------------------------------

    def render_frame(self, dt: float) -> PowerReport:
        """Render, brightness-scale, and clamp one frame into the frame buffer.

        Separated from the loop so tests can drive it deterministically.
        """
        if not self._state.power:
            # Master off means black, but the frame still goes out: the strip
            # holds its last value until told otherwise.
            self._frame[:] = 0.0
        else:
            effect = self._ensure_effect()
            self._animation_time += dt
            effect.render(self._frame, self._animation_time, dt)
            np.clip(self._frame, 0.0, 1.0, out=self._frame)
            if self._state.brightness != 1.0:
                np.multiply(self._frame, self._state.brightness, out=self._frame)

        report = self.governor.apply(self._frame)
        self.last_report = report
        self.stats.frames_rendered += 1
        return report

    def encode(self) -> list[bytes]:
        """Encode the current frame buffer as one OPC message per device channel.

        Quantisation happens once, over the whole frame, before it is split
        across channels: the dither carries residuals per pixel, and slicing
        first would give each device its own accumulator for no reason.

        Each message is indexed by *device* pixel, not by frame position, so
        fcserver's map is the identity over a board's pixels no matter how the
        runs are split across its outputs.  See :meth:`Layout.fcserver_map`.
        """
        quantized = (
            self._dither.quantize(self._frame)
            if self._dither is not None
            else quantize_plain(self._frame)
        )
        messages: list[bytes] = []
        for cmap, buffer in zip(self._channels, self._channel_buffers, strict=True):
            block = quantized[cmap.frame_slice]
            if buffer is None:
                payload = block
            else:
                buffer[cmap.device_indices] = block
                payload = buffer
            messages.append(encode_frame(cmap.opc_channel, payload))
        return messages

    @property
    def frame(self) -> np.ndarray:
        """The last rendered frame. Read-only by convention; tests use it."""
        return self._frame

    async def render_once(self, dt: float) -> PowerReport:
        """Render one frame and send it. The unit of work the loop repeats."""
        report = self.render_frame(dt)
        await self.sink.send(self.encode())
        self.stats.frames_sent = getattr(self.sink, "frames_sent", self.stats.frames_sent)
        self.stats.frames_dropped = getattr(self.sink, "frames_dropped", 0)
        return report

    # -- loop -----------------------------------------------------------

    async def run(self) -> None:
        """Render at ``fps`` until stopped."""
        period = 1.0 / self.fps
        next_deadline = time.perf_counter()
        self._last_render = None
        fps_window_start = time.perf_counter()
        fps_window_frames = 0

        log.info(
            "render loop starting: %d pixels, %.1f fps, %.2f A ceiling (%.2f A at full white)",
            self.layout.pixel_count,
            self.fps,
            self.governor.limit_amps,
            self.governor.full_white_amps,
        )

        while not self._stopping.is_set():
            now = time.perf_counter()
            dt = period if self._last_render is None else min(now - self._last_render, 0.25)
            self._last_render = now

            started = time.perf_counter()
            try:
                await self.render_once(dt)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A render loop that dies takes the whole installation dark and
                # needs a service restart to come back. Log and carry on.
                log.exception("frame render failed; continuing")
            self.stats.render_ms = (time.perf_counter() - started) * 1000.0

            fps_window_frames += 1
            elapsed = time.perf_counter() - fps_window_start
            if elapsed >= 1.0:
                self.stats.measured_fps = fps_window_frames / elapsed
                fps_window_frames = 0
                fps_window_start = time.perf_counter()

            next_deadline += period
            sleep_for = next_deadline - time.perf_counter()
            if sleep_for < 0:
                # We fell behind. Resync rather than trying to catch up, which
                # would just queue a burst of frames and make it worse.
                self.stats.late_frames += 1
                next_deadline = time.perf_counter()
                sleep_for = 0
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=sleep_for)

        log.info("render loop stopped after %d frames", self.stats.frames_rendered)

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            raise RuntimeError("engine is already running")
        self._stopping.clear()
        self._task = asyncio.create_task(self.run(), name="fclights-render")
        return self._task

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        await self.sink.close()

    def status(self) -> dict[str, Any]:
        return {
            "fps_target": self.fps,
            "pixel_count": self.layout.pixel_count,
            "connected": bool(getattr(self.sink, "connected", False)),
            "dither": self._dither is not None,
            "sink": getattr(self.sink, "endpoint", "unknown"),
            "engine": self.stats.to_dict(),
            "power": (self.last_report.to_dict() if self.last_report else None),
            "power_model": {
                "limit_amps": self.governor.limit_amps,
                "full_white_amps": round(self.governor.full_white_amps, 3),
                "idle_amps": round(self.governor.idle_amps, 4),
                "ma_per_channel": self.governor.ma_per_channel,
                "gamma": self.governor.gamma,
            },
        }

"""Open Pixel Control client.

We do not talk to the Fadecandy hardware.  ``fcserver`` (the stock upstream
Fadecandy server) owns the USB link and listens for OPC on localhost; we render
frames and hand them over.  That indirection is the whole point of using a
Fadecandy: fcserver's temporal dithering and interpolation are what make a 5%
brightness scene and a thirty-second fade look smooth on an 8-bit WS2812B, and
bit-banging the strip ourselves would throw that away.

Wire format (OPC, one message per channel per frame)::

    byte 0   channel   0 = broadcast to every channel, 1..255 = one channel
    byte 1   command   0 = set 8-bit pixel colours
    byte 2   length high byte    ) length of the data that follows,
    byte 3   length low byte     ) in bytes, i.e. 3 * pixel count
    byte 4.. data      R, G, B per pixel

Reconnection is the client's job and is deliberately non-fatal: if fcserver is
restarted or the Fadecandy is unplugged, the render loop keeps running and this
client keeps retrying on a backoff, so the rig recovers on its own.

Why we dither on the way out
----------------------------
fcserver's OPC input is 8 bit and only 8 bit: its ``OPC::Command`` enum defines
``SetPixelColors = 0x00`` and ``SystemExclusive = 0xFF``, and nothing else, so
the 16-bit set-pixels command the OPC specification describes is not a path that
exists here. Everything upstream of this module works in float, and this is
where that precision would otherwise be thrown away.

For a fast animation that does not matter. For the slow near-identical white
fades this installation is actually for, it matters a lot: a fifteen minute fade
from 2700 K to 3400 K moves the blue channel by about 45 codes, so plain
rounding holds each code for ten seconds and the result visibly walks up a
staircase. fcserver's interpolation cannot rescue that, because it would be
interpolating between two frames we already rounded to the same value.

:class:`TemporalDither` fixes it at the source by carrying the rounding residual
into the next frame, so a channel sitting at 128.3 emits 128 and 129 in the
right proportion and *averages* to 128.3. This composes with fcserver's own
dithering rather than fighting it: ours puts the sub-code information into the
sequence of frames, and fcserver's interpolation and 400 Hz output dithering
smooth that sequence on the way to the LEDs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

OPC_HEADER_BYTES = 4
OPC_SET_PIXELS = 0
OPC_BROADCAST_CHANNEL = 0

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7890


def encode_frame(channel: int, pixels: np.ndarray) -> bytes:
    """Encode an ``(N, 3)`` pixel buffer as one OPC set-pixels message.

    Accepts either a float 0..1 buffer, which is rounded without dithering, or
    an already-quantised uint8 buffer from :class:`TemporalDither`.  We
    deliberately do *not* apply gamma here; that lives in fcserver's ``color``
    block.
    """
    if not 0 <= channel <= 255:
        raise ValueError(f"OPC channel must be 0..255, got {channel}")
    if pixels.ndim != 2 or pixels.shape[1] != 3:
        raise ValueError(f"expected an (N, 3) pixel buffer, got shape {pixels.shape}")

    count = pixels.shape[0]
    length = count * 3
    if length > 0xFFFF:
        raise ValueError(f"{count} pixels exceeds the 21845-pixel OPC message limit")

    body = pixels if pixels.dtype == np.uint8 else quantize_plain(pixels)

    header = bytes((channel, OPC_SET_PIXELS, (length >> 8) & 0xFF, length & 0xFF))
    return header + body.tobytes()


class TemporalDither:
    """First-order error feedback, so 8-bit frames average to the float value.

    Each frame the rounding residual is carried into the next one. A channel
    held at 128.3 emits 128, 128, 129, 128, 128, 129 ... and averages to 128.3,
    which is what makes a very slow fade move continuously instead of stepping.

    The quantisation noise this produces is first-order shaped, meaning it sits
    at high frequency, near the frame rate, where the LED and the eye both
    ignore it. A value that lands exactly on a code, which is every solid colour
    the user picks, leaves the residual at zero and emits nothing at all.
    """

    def __init__(self, shape: tuple[int, ...]) -> None:
        self._error = np.zeros(shape, dtype=np.float32)

    def reset(self) -> None:
        self._error[:] = 0.0

    def quantize(self, pixels: np.ndarray) -> np.ndarray:
        """Convert a float 0..1 buffer to uint8, carrying the residual forward."""
        target = _to_codes(pixels)
        biased = target + self._error
        out = np.rint(biased)
        np.clip(out, 0.0, 255.0, out=out)
        # The residual is what the *biased* value could not express, which is
        # what carries the unspent fraction forward. Measuring against the
        # unbiased target instead would throw away the correction just applied
        # and lock the output into a two-code oscillation around the wrong mean.
        np.subtract(biased, out, out=self._error)
        # At a clamped extreme the residual would otherwise run away and then
        # dump a burst of wrong values when the frame moves back in range.
        np.clip(self._error, -1.0, 1.0, out=self._error)
        return out.astype(np.uint8)


def quantize_plain(pixels: np.ndarray) -> np.ndarray:
    """Round a float 0..1 buffer to uint8 with no dithering."""
    return np.rint(_to_codes(pixels)).astype(np.uint8)


def _to_codes(pixels: np.ndarray) -> np.ndarray:
    """A float 0..1 buffer as float32 0..255, with non-finite values blacked out.

    Nothing upstream should ever hand us a NaN - the parameter validators reject
    non-finite input - but this is the last gate before eight bits, and the
    dither's residual is carried across frames. One NaN reaching it would make
    every later frame NaN too and take the installation dark until the service
    restarted, so the invariant is enforced here rather than assumed.
    """
    codes = np.nan_to_num(pixels, nan=0.0, posinf=1.0, neginf=0.0).astype(
        np.float32, copy=False
    )
    np.clip(codes, 0.0, 1.0, out=codes)
    codes *= 255.0
    return codes


class FrameSink(Protocol):
    """Where the engine sends finished frames."""

    async def send(self, messages: list[bytes]) -> None: ...

    async def close(self) -> None: ...

    @property
    def connected(self) -> bool: ...


class OPCClient:
    """Asyncio OPC client with transparent reconnection.

    ``send`` never raises for connection trouble.  A frame that cannot be
    delivered is dropped and the connection is retried on a backoff; the caller
    is a real-time render loop and blocking it on a dead socket would be worse
    than losing a frame.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        connect_timeout: float = 2.0,
        min_retry: float = 0.25,
        max_retry: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.min_retry = min_retry
        self.max_retry = max_retry

        self._writer: asyncio.StreamWriter | None = None
        self._retry_at = 0.0
        self._retry_delay = min_retry
        self._logged_failure = False
        self.frames_sent = 0
        self.frames_dropped = 0

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    async def _connect(self) -> bool:
        now = time.monotonic()
        if now < self._retry_at:
            return False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.connect_timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            self._retry_at = now + self._retry_delay
            self._retry_delay = min(self._retry_delay * 2, self.max_retry)
            if not self._logged_failure:
                log.warning("OPC connect to %s failed (%s); retrying in the background",
                            self.endpoint, exc)
                self._logged_failure = True
            return False

        self._writer = writer
        # fcserver never sends anything back, so the reader is only here to hold
        # the transport. We rely on asyncio marking the writer as closing when
        # the peer goes away, which is what drives the reconnect below. That is
        # sound because this socket is always localhost: fcserver either exits,
        # and the kernel closes its end, or it is up. There is no network in
        # between to partition and leave the connection half-dead.
        del reader
        self._retry_delay = self.min_retry
        if self._logged_failure:
            log.info("OPC connection to %s restored", self.endpoint)
            self._logged_failure = False
        else:
            log.info("connected to OPC server at %s", self.endpoint)
        return True

    async def send(self, messages: list[bytes]) -> None:
        if not messages:
            return
        if not self.connected and not await self._connect():
            self.frames_dropped += 1
            return

        writer = self._writer
        assert writer is not None
        try:
            writer.write(b"".join(messages))
            await writer.drain()
        except (OSError, RuntimeError) as exc:
            log.warning("OPC write to %s failed (%s); will reconnect", self.endpoint, exc)
            self.frames_dropped += 1
            await self._drop()
            return
        self.frames_sent += 1

    async def _drop(self) -> None:
        writer, self._writer = self._writer, None
        self._retry_at = time.monotonic() + self._retry_delay
        if writer is None:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except (OSError, RuntimeError):
            pass

    async def close(self) -> None:
        self._retry_at = 0.0
        await self._drop()


class NullSink:
    """Frame sink for ``--simulate``: counts frames and keeps the last one.

    This is what lets the whole service - API, engine, governor, persistence -
    run on a laptop with no Fadecandy and no fcserver, which is how the Android
    app gets built before the hardware is wired up.
    """

    def __init__(self) -> None:
        self.frames_sent = 0
        self.frames_dropped = 0
        self.last_messages: list[bytes] = []

    @property
    def connected(self) -> bool:
        return True

    @property
    def endpoint(self) -> str:
        return "simulated"

    async def send(self, messages: list[bytes]) -> None:
        self.last_messages = messages
        self.frames_sent += 1

    async def close(self) -> None:
        return None

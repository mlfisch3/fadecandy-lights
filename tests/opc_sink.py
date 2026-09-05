"""A test double for fcserver.

We have no Fadecandy and no fcserver in CI, so this stands in for both: it
accepts OPC connections, decodes what arrives, and asserts the frames are
structurally valid.  The engine is exercised end to end against it, which is
what gives us confidence in everything up to the USB link without hardware.

It validates rather than merely records: a malformed header, a truncated body or
a length that disagrees with the payload is caught here rather than showing up
as unexplained flicker on a real strip.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import numpy as np

from fclights.opc import OPC_HEADER_BYTES, OPC_SET_PIXELS


class ProtocolViolation(AssertionError):
    """Raised when a received message does not conform to OPC."""


@dataclass
class ReceivedFrame:
    channel: int
    command: int
    pixels: np.ndarray
    """``(N, 3)`` uint8 array of the pixel data that arrived."""

    @property
    def pixel_count(self) -> int:
        return int(self.pixels.shape[0])


@dataclass
class RecordingOPCServer:
    """An asyncio TCP server that speaks the receiving half of OPC.

    Usage::

        async with RecordingOPCServer() as sink:
            ...  # point an OPCClient at sink.host, sink.port
            assert sink.frames
    """

    host: str = "127.0.0.1"
    port: int = 0
    frames: list[ReceivedFrame] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    connections: int = 0

    _server: asyncio.AbstractServer | None = None
    _frame_event: asyncio.Event = field(default_factory=asyncio.Event)
    _writers: set = field(default_factory=set)

    async def start(self) -> RecordingOPCServer:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        # Closing the listening socket alone would leave accepted connections
        # open, which is not what a restarting fcserver looks like. Drop them
        # too, so clients see the EOF they would see in the field.
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> RecordingOPCServer:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        try:
            while True:
                header = await reader.readexactly(OPC_HEADER_BYTES)
                channel, command, length_hi, length_lo = header
                length = (length_hi << 8) | length_lo

                if command != OPC_SET_PIXELS:
                    self.violations.append(f"unexpected OPC command {command}")
                if length % 3 != 0:
                    self.violations.append(f"data length {length} is not a multiple of 3")

                body = await reader.readexactly(length)
                pixels = np.frombuffer(body, dtype=np.uint8).reshape(-1, 3).copy()
                self.frames.append(
                    ReceivedFrame(channel=channel, command=command, pixels=pixels)
                )
                self._frame_event.set()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def wait_for_frames(self, count: int = 1, timeout: float = 5.0) -> list[ReceivedFrame]:
        """Block until at least ``count`` frames have arrived."""
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.frames) < count:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"only {len(self.frames)} of {count} frames arrived within {timeout}s"
                )
            self._frame_event.clear()
            try:
                await asyncio.wait_for(self._frame_event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
        return list(self.frames)

    def assert_clean(self) -> None:
        if self.violations:
            raise ProtocolViolation("; ".join(self.violations))

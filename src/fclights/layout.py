"""Physical layout of the installation.

The layout describes which Fadecandy devices exist, which of each device's eight
outputs are populated, how many pixels hang off each output, and where those
pixels sit in space.  Effects consume the derived coordinate arrays rather than
raw pixel indices, so that a "sweep left to right" looks the same whether the
strips are one long run or eight parallel bars.

The file format is JSON.  See ``config/layout.example.json`` and ``docs/api.md``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from fclights.jsonio import JSONDocumentError
from fclights.jsonio import loads as load_json_document

# A Fadecandy board has eight outputs, and each output is hard-capped at 64
# pixels by the hardware. 512 pixels per board is a ceiling, not a guideline.
OUTPUTS_PER_DEVICE = 8
MAX_PIXELS_PER_OUTPUT = 64
PIXELS_PER_DEVICE = OUTPUTS_PER_DEVICE * MAX_PIXELS_PER_OUTPUT

DEFAULT_PIXELS_PER_METRE = 30.3
"""Fallback strip density, used only when the layout does not state one.

Measured on the actual reels at 33 mm centre to centre, which is 30.3 LEDs/m.
Nothing in the code assumes it: it is the default of a config value, it only
affects the spatial coordinates effects animate along, and it is wrong for any
strip of a different density. Measure the pitch on your own strip - there is a
cut pad at every LED - and put the answer in the layout file.
"""


class LayoutError(ValueError):
    """Raised when a layout file is structurally invalid."""


@dataclass(frozen=True)
class Output:
    """One of the eight data lines coming off a Fadecandy board."""

    index: int
    count: int
    name: str = ""
    # Spatial position of the first pixel and the per-pixel step vector, in
    # metres.  A straight strip is fully described by these two; anything more
    # exotic can be expressed by ``points``.
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    step: tuple[float, float, float] = (1.0 / DEFAULT_PIXELS_PER_METRE, 0.0, 0.0)
    points: tuple[tuple[float, float, float], ...] | None = None
    reverse: bool = False

    def coordinates(self) -> np.ndarray:
        """Return an ``(count, 3)`` float array of pixel positions in metres."""
        if self.points is not None:
            pts = np.asarray(self.points, dtype=np.float64)
        else:
            offsets = np.arange(self.count, dtype=np.float64)[:, None]
            pts = np.asarray(self.origin, dtype=np.float64) + offsets * np.asarray(
                self.step, dtype=np.float64
            )
        if self.reverse:
            pts = pts[::-1]
        return pts


@dataclass(frozen=True)
class Device:
    """One Fadecandy board.

    ``opc_channel`` is the Open Pixel Control channel this board's pixels are
    sent on.  A single-board rig uses channel 0 (the OPC broadcast channel),
    which matches fcserver's stock configuration.  Additional boards get their
    own channels; that is the seam a multi-Fadecandy build grows through.
    """

    id: str
    outputs: tuple[Output, ...]
    opc_channel: int = 0
    serial: str | None = None

    @property
    def pixel_count(self) -> int:
        return sum(o.count for o in self.outputs)


@dataclass(frozen=True)
class ChannelMap:
    """How one board's pixels sit in the frame and where they land on the board.

    ``frame_slice`` is the contiguous span of the render buffer the board owns.
    ``device_indices`` gives, for every pixel in that span, the Fadecandy pixel
    index it must be written to: a board addresses output *n* starting at pixel
    ``64 * n`` whether or not the preceding outputs are full, so an output with
    fewer than 64 pixels leaves a gap in the device's address space that the
    frame does not have.  The engine expands the span through these indices
    before encoding, which is what keeps a short output from shifting every
    output after it.
    """

    opc_channel: int
    frame_slice: slice
    device_indices: np.ndarray = field(repr=False)
    device_pixel_count: int
    """Device pixels the encoded message spans, gaps included."""

    @property
    def contiguous(self) -> bool:
        """True when the frame span already is the device's pixel order."""
        return self.device_pixel_count == self.frame_slice.stop - self.frame_slice.start


@dataclass(frozen=True)
class Layout:
    """The whole installation, plus the derived arrays effects render against."""

    name: str
    devices: tuple[Device, ...]
    pixel_count: int
    pixels_per_metre: float
    # (N, 3) pixel positions in metres, in strip order.
    positions: np.ndarray = field(repr=False)
    # (N,) position along the logical run, normalised to 0..1.
    u: np.ndarray = field(repr=False)
    # (N, 3) positions normalised to 0..1 per axis over the bounding box.
    normalized: np.ndarray = field(repr=False)
    # (N,) index of the output each pixel belongs to, as a flat output ordinal.
    segment: np.ndarray = field(repr=False)
    # (N,) 0..1 position within the pixel's own output.
    segment_u: np.ndarray = field(repr=False)

    @property
    def segment_count(self) -> int:
        return int(self.segment.max()) + 1 if self.pixel_count else 0

    def channel_maps(self) -> list[ChannelMap]:
        """Return one :class:`ChannelMap` per device, in layout order.

        Pixels are laid out device by device, in the order the layout file lists
        them, so each device owns one contiguous slice of the frame.
        """
        out: list[ChannelMap] = []
        start = 0
        for device in self.devices:
            indices = np.concatenate(
                [
                    np.arange(
                        o.index * MAX_PIXELS_PER_OUTPUT,
                        o.index * MAX_PIXELS_PER_OUTPUT + o.count,
                        dtype=np.int32,
                    )
                    for o in device.outputs
                ]
            )
            end = start + device.pixel_count
            out.append(
                ChannelMap(
                    opc_channel=device.opc_channel,
                    frame_slice=slice(start, end),
                    device_indices=indices,
                    device_pixel_count=int(indices[-1]) + 1,
                )
            )
            start = end
        return out

    def fcserver_map(self) -> list[list[int]]:
        """The ``devices[].map`` entries fcserver needs for this layout.

        Because the engine sends each board a frame already indexed by device
        pixel, every entry is the identity mapping over that board's pixels:
        ``[opc_channel, 0, 0, device_pixel_count]``.  Nothing in the map has to
        track how the runs are split across outputs.
        """
        return [[m.opc_channel, 0, 0, m.device_pixel_count] for m in self.channel_maps()]

    def to_dict(self) -> dict[str, Any]:
        """Serialise the layout for the control API."""
        return {
            "name": self.name,
            "pixel_count": self.pixel_count,
            "pixels_per_metre": self.pixels_per_metre,
            "devices": [
                {
                    "id": d.id,
                    "opc_channel": d.opc_channel,
                    "serial": d.serial,
                    "pixel_count": d.pixel_count,
                    "outputs": [
                        {
                            "index": o.index,
                            "count": o.count,
                            "name": o.name,
                            "reverse": o.reverse,
                        }
                        for o in d.outputs
                    ],
                }
                for d in self.devices
            ],
            "bounds": {
                "min": self.positions.min(axis=0).tolist() if self.pixel_count else [0, 0, 0],
                "max": self.positions.max(axis=0).tolist() if self.pixel_count else [0, 0, 0],
            },
        }


def _is_sequence(value: Any) -> bool:
    """True for a JSON array. A string is iterable but is never a coordinate."""
    return not isinstance(value, (str, bytes)) and isinstance(value, (list, tuple))


def _finite(value: Any, what: str) -> float:
    """Coerce a coordinate-ish number, refusing NaN and the infinities.

    A non-finite coordinate does not fail loudly anywhere downstream: it spreads
    through the derived position arrays, and ``Layout.to_dict`` then serves it
    to the phone as ``null`` over REST and as a bare ``NaN`` token over the
    WebSocket, which is not valid JSON at all.
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LayoutError(f"{what} must be a number: {exc}") from exc
    if not math.isfinite(number):
        raise LayoutError(f"{what} must be a finite number, got {value!r}")
    return number


def _output_from_dict(
    raw: dict[str, Any], device_id: str, pitch: float
) -> Output:
    if not isinstance(raw, dict):
        raise LayoutError(f"device {device_id!r}: each output must be a JSON object")
    try:
        index = int(raw["index"])
        count = int(raw["count"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        # int() raises OverflowError, not ValueError, for an infinity, and
        # float() does the same for an integer literal too large to represent.
        raise LayoutError(
            f"device {device_id!r}: output needs integer 'index' and 'count'"
        ) from exc

    if not 0 <= index < OUTPUTS_PER_DEVICE:
        raise LayoutError(
            f"device {device_id!r}: output index {index} outside 0..{OUTPUTS_PER_DEVICE - 1}"
        )
    if not 0 < count <= MAX_PIXELS_PER_OUTPUT:
        raise LayoutError(
            f"device {device_id!r} output {index}: count {count} outside 1..{MAX_PIXELS_PER_OUTPUT}"
        )

    points = raw.get("points")
    if points is not None:
        where = f"device {device_id!r} output {index}: points"
        if not _is_sequence(points) or not all(_is_sequence(p) for p in points):
            raise LayoutError(f"{where} must be a list of [x, y, z] triples")
        pts = tuple(tuple(_finite(v, where) for v in p) for p in points)
        if len(pts) != count:
            raise LayoutError(
                f"device {device_id!r} output {index}: {len(pts)} points for count {count}"
            )
        if any(len(p) != 3 for p in pts):
            raise LayoutError(f"device {device_id!r} output {index}: points must be [x, y, z]")
    else:
        pts = None

    def vec(key: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
        value = raw.get(key, default)
        where = f"device {device_id!r} output {index}: {key}"
        if not _is_sequence(value):
            raise LayoutError(f"{where} must be [x, y, z]")
        seq = tuple(_finite(v, where) for v in value)
        if len(seq) != 3:
            raise LayoutError(f"{where} must be [x, y, z]")
        return seq

    name = raw.get("name", "")
    if not isinstance(name, str):
        raise LayoutError(f"device {device_id!r} output {index}: name must be a string")

    reverse = raw.get("reverse", False)
    if not isinstance(reverse, bool):
        # bool("false") is True, so a coerced string would silently render the
        # whole run backwards with nothing to show for it.
        raise LayoutError(
            f"device {device_id!r} output {index}: reverse must be true or false, "
            f"got {reverse!r}"
        )

    return Output(
        index=index,
        count=count,
        name=name,
        origin=vec("origin", (0.0, 0.0, 0.0)),
        # An output that does not state its own step inherits the layout's
        # pixel density, so the density is stated once rather than copied into
        # every output where it could drift out of step.
        step=vec("step", (pitch, 0.0, 0.0)),
        points=pts,
        reverse=reverse,
    )


def _device_from_dict(raw: dict[str, Any], pitch: float) -> Device:
    if not isinstance(raw, dict):
        raise LayoutError(f"each device must be a JSON object, got {raw!r}")
    device_id = raw.get("id", "fc0")
    if not isinstance(device_id, str):
        raise LayoutError(f"device id must be a string, got {device_id!r}")
    serial = raw.get("serial")
    if serial is not None and not isinstance(serial, str):
        raise LayoutError(f"device {device_id!r}: serial must be a string or null")

    outputs_raw = raw.get("outputs")
    if not _is_sequence(outputs_raw):
        raise LayoutError(f"device {device_id!r}: 'outputs' must be a list")
    if not outputs_raw:
        raise LayoutError(f"device {device_id!r}: needs at least one output")

    outputs = tuple(_output_from_dict(o, device_id, pitch) for o in outputs_raw)
    seen: set[int] = set()
    for o in outputs:
        if o.index in seen:
            raise LayoutError(f"device {device_id!r}: output index {o.index} listed twice")
        seen.add(o.index)

    try:
        channel = int(raw.get("opc_channel", 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise LayoutError(f"device {device_id!r}: opc_channel must be an integer: {exc}") from exc
    if not 0 <= channel <= 255:
        raise LayoutError(f"device {device_id!r}: opc_channel {channel} outside 0..255")

    return Device(
        id=device_id,
        outputs=tuple(sorted(outputs, key=lambda o: o.index)),
        opc_channel=channel,
        serial=serial,
    )


def build_layout(raw: dict[str, Any]) -> Layout:
    """Validate a parsed layout document and derive the effect coordinate arrays."""
    if not isinstance(raw, dict):
        raise LayoutError("layout must be a JSON object")

    devices_raw = raw.get("devices")
    if not _is_sequence(devices_raw) or not devices_raw:
        # Iterating a JSON object yields its keys, so an object here would be
        # silently walked as a list of strings rather than reported.
        raise LayoutError("layout needs a non-empty 'devices' list")

    name = raw.get("name", "strip")
    if not isinstance(name, str):
        raise LayoutError(f"layout name must be a string, got {name!r}")

    pixels_per_metre = _finite(
        raw.get("pixels_per_metre", DEFAULT_PIXELS_PER_METRE), "pixels_per_metre"
    )
    if pixels_per_metre <= 0:
        raise LayoutError(f"pixels_per_metre must be positive, got {pixels_per_metre}")

    devices = tuple(_device_from_dict(d, 1.0 / pixels_per_metre) for d in devices_raw)

    ids = [d.id for d in devices]
    if len(set(ids)) != len(ids):
        raise LayoutError("device ids must be unique")

    channels = [d.opc_channel for d in devices]
    if len(set(channels)) != len(channels):
        raise LayoutError("each device needs its own opc_channel")
    if len(devices) > 1 and 0 in channels:
        # Channel 0 is the OPC broadcast channel; it would light every board.
        raise LayoutError(
            "opc_channel 0 is broadcast-only and cannot be used with multiple devices"
        )

    coords: list[np.ndarray] = []
    segment_ids: list[np.ndarray] = []
    segment_u: list[np.ndarray] = []
    ordinal = 0
    for device in devices:
        for output in device.outputs:
            coords.append(output.coordinates())
            segment_ids.append(np.full(output.count, ordinal, dtype=np.int32))
            if output.count == 1:
                segment_u.append(np.zeros(1, dtype=np.float32))
            else:
                segment_u.append(np.linspace(0.0, 1.0, output.count, dtype=np.float32))
            ordinal += 1

    positions = np.concatenate(coords).astype(np.float64)
    pixel_count = positions.shape[0]

    lo = positions.min(axis=0)
    hi = positions.max(axis=0)
    span = np.where(hi - lo > 1e-9, hi - lo, 1.0)
    normalized = ((positions - lo) / span).astype(np.float32)

    if pixel_count == 1:
        u = np.zeros(1, dtype=np.float32)
    else:
        u = np.linspace(0.0, 1.0, pixel_count, dtype=np.float32)

    return Layout(
        name=name,
        devices=devices,
        pixel_count=pixel_count,
        pixels_per_metre=pixels_per_metre,
        positions=positions,
        u=u,
        normalized=normalized,
        segment=np.concatenate(segment_ids),
        segment_u=np.concatenate(segment_u),
    )


def load_layout(path: str | Path) -> Layout:
    """Read and validate a layout JSON file."""
    path = Path(path)
    try:
        raw = load_json_document(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LayoutError(f"layout file not found: {path}") from exc
    except OSError as exc:
        raise LayoutError(f"layout file {path} cannot be read: {exc}") from exc
    except JSONDocumentError as exc:
        raise LayoutError(f"layout file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LayoutError(f"layout file {path} is not valid JSON: {exc}") from exc
    return build_layout(raw)


def simple_layout(
    pixel_count: int,
    name: str = "simulated",
    *,
    pixels_per_metre: float = DEFAULT_PIXELS_PER_METRE,
) -> Layout:
    """Build a straight run, chunked across outputs of 64 and boards of 512.

    Used by ``--simulate`` when no layout file is supplied, and by tests.  It
    spans as many Fadecandy boards as the pixel count needs rather than stopping
    at one, because the real installation is around 18 runs and will not fit on
    a single board.
    """
    if pixel_count <= 0:
        raise LayoutError("pixel_count must be positive")
    if pixels_per_metre <= 0:
        raise LayoutError("pixels_per_metre must be positive")

    pitch = 1.0 / pixels_per_metre
    devices: list[dict[str, Any]] = []
    remaining = pixel_count
    offset = 0.0
    device_index = 0

    while remaining > 0:
        outputs: list[dict[str, Any]] = []
        for index in range(OUTPUTS_PER_DEVICE):
            if remaining <= 0:
                break
            count = min(MAX_PIXELS_PER_OUTPUT, remaining)
            outputs.append(
                {
                    "index": index,
                    "count": count,
                    "name": f"run {device_index * OUTPUTS_PER_DEVICE + index}",
                    "origin": [offset, 0.0, 0.0],
                    "step": [pitch, 0.0, 0.0],
                }
            )
            offset += count * pitch
            remaining -= count
        devices.append(
            {
                "id": f"fc{device_index}",
                # A lone board uses channel 0, which is OPC broadcast and what
                # fcserver's stock configuration expects. Once there is more
                # than one, each board needs its own channel.
                "opc_channel": 0 if pixel_count <= PIXELS_PER_DEVICE else device_index + 1,
                "outputs": outputs,
            }
        )
        device_index += 1

    return build_layout(
        {"name": name, "pixels_per_metre": pixels_per_metre, "devices": devices}
    )

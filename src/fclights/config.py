"""Service configuration.

Configuration is a JSON file plus command line overrides.  It covers the things
an installer sets once - supply ceiling, frame rate, where fcserver is, where
state lives - and nothing that the API can change at runtime.  Anything a phone
can change belongs in :mod:`fclights.state`, which persists separately.

See ``config/fclights.example.json``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fclights.jsonio import JSONDocumentError
from fclights.jsonio import loads as load_json_document
from fclights.power import DEFAULT_IDLE_MA_PER_PIXEL, DEFAULT_MA_PER_CHANNEL

DEFAULT_CONFIG_PATH = Path("/etc/fclights/fclights.json")
DEFAULT_LAYOUT_PATH = Path("/etc/fclights/layout.json")
DEFAULT_STATE_PATH = Path("/var/lib/fclights/state.json")


class ConfigError(ValueError):
    """Raised when the config file is malformed or self-contradictory."""


LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")
"""Levels both consumers of this setting understand.

``configure_logging`` reads it through ``logging``, and ``fclights run`` hands
it to uvicorn. The two do not accept the same names: ``logging`` has WARN and
NOTSET, uvicorn has TRACE and rejects the first two with a KeyError. Anything
outside the intersection would let ``fclights check`` pass a config that then
crash-loops ``fclights run`` under Restart=always, so only the intersection is
allowed, with the two common aliases normalised into it.
"""

_LOG_LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL"}


def _log_level(value: str) -> str:
    level = _LOG_LEVEL_ALIASES.get(value.upper(), value.upper())
    if level not in LOG_LEVELS:
        raise ConfigError(
            f"log_level must be one of {', '.join(LOG_LEVELS)}, got {value!r}"
        )
    return level


@dataclass(frozen=True)
class PowerConfig:
    """Supply ceiling and the LED current model used to stay under it."""

    limit_amps: float = 24.0
    """Usable current of the supply feeding the strip, at 5 V.

    The default is the smaller of the two supplies on hand - a 5 V 30 A unit -
    derated to 80% of its nameplate rating.  Enclosed supplies of this class are
    rated for intermittent peaks, not for holding nameplate continuously in a
    warm cupboard for months, and 80% is the usual allowance for continuous
    duty.  It is the smaller supply on purpose: defaulting to the 60 A unit
    would happily overload the 30 A one if the two were swapped.

    Raise it only after measuring what your supply and wiring actually deliver.
    """

    ma_per_channel: float = DEFAULT_MA_PER_CHANNEL
    idle_ma_per_pixel: float = DEFAULT_IDLE_MA_PER_PIXEL
    gamma: float = 1.0
    """Downstream gamma to assume when predicting draw. 1.0 is conservative."""


@dataclass(frozen=True)
class OPCConfig:
    host: str = "127.0.0.1"
    port: int = 7890


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    """Bound to every interface so phones on the LAN can reach it."""

    port: int = 7891
    cors_origins: tuple[str, ...] = ()
    """Extra browser origins to allow. The Android app does not need any."""


@dataclass(frozen=True)
class Config:
    fps: float = 60.0
    dither: bool = True
    """Temporally dither the 8-bit OPC output.

    On by default.  fcserver's OPC input is 8-bit only, and this is what keeps a
    minutes-long fade between two near-identical whites moving continuously
    instead of stepping.  See :mod:`fclights.opc`.
    """

    power: PowerConfig = field(default_factory=PowerConfig)
    opc: OPCConfig = field(default_factory=OPCConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    layout_path: Path = DEFAULT_LAYOUT_PATH
    state_path: Path = DEFAULT_STATE_PATH
    simulate: bool = False
    simulate_pixels: int = 512
    log_level: str = "INFO"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fps": self.fps,
            "dither": self.dither,
            "power": {
                "limit_amps": self.power.limit_amps,
                "ma_per_channel": self.power.ma_per_channel,
                "idle_ma_per_pixel": self.power.idle_ma_per_pixel,
                "gamma": self.power.gamma,
            },
            "opc": {"host": self.opc.host, "port": self.opc.port},
            "server": {
                "host": self.server.host,
                "port": self.server.port,
                "cors_origins": list(self.server.cors_origins),
            },
            "layout_path": str(self.layout_path),
            "state_path": str(self.state_path),
            "simulate": self.simulate,
            "simulate_pixels": self.simulate_pixels,
            "log_level": self.log_level,
        }


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"config section {key!r} must be an object")
    return value


def _text(raw: dict[str, Any], key: str, default: Any, where: str) -> str:
    """Read a string field, refusing any other JSON type.

    ``raw.get(key, default)`` returns None when the key is present and null, so
    the default never applies; ``str(None)`` would then quietly become the
    string "None" and ``Path(None)`` a TypeError outside the ConfigError guard.
    """
    value = raw.get(key, default)
    if isinstance(value, Path):
        return str(value)
    if not isinstance(value, str):
        raise ConfigError(f"{where} must be a string, got {value!r}")
    return value


def _flag(raw: dict[str, Any], key: str, default: bool, where: str) -> bool:
    """Read a boolean field, refusing any other JSON type.

    ``bool()`` makes every non-empty string true, so ``"simulate": "false"``
    would wire a NullSink and leave the strip dark while every endpoint still
    answered normally - a fault that looks exactly like bad wiring.
    """
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where} must be true or false, got {value!r}")
    return value


def _origin(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"server.cors_origins entries must be strings, got {value!r}")
    return value


def _unknown(raw: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = set(raw) - allowed
    if extra:
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(sorted(extra))}")


def config_from_dict(raw: dict[str, Any]) -> Config:
    """Build a :class:`Config` from a parsed JSON document."""
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")

    _unknown(
        raw,
        {
            "fps",
            "dither",
            "power",
            "opc",
            "server",
            "layout_path",
            "state_path",
            "simulate",
            "simulate_pixels",
            "log_level",
        },
        "config",
    )

    power_raw = _section(raw, "power")
    _unknown(power_raw, {"limit_amps", "ma_per_channel", "idle_ma_per_pixel", "gamma"}, "power")
    opc_raw = _section(raw, "opc")
    _unknown(opc_raw, {"host", "port"}, "opc")
    server_raw = _section(raw, "server")
    _unknown(server_raw, {"host", "port", "cors_origins"}, "server")

    defaults = Config()
    try:
        fps = float(raw.get("fps", defaults.fps))
        power = PowerConfig(
            limit_amps=float(power_raw.get("limit_amps", defaults.power.limit_amps)),
            ma_per_channel=float(power_raw.get("ma_per_channel", defaults.power.ma_per_channel)),
            idle_ma_per_pixel=float(
                power_raw.get("idle_ma_per_pixel", defaults.power.idle_ma_per_pixel)
            ),
            gamma=float(power_raw.get("gamma", defaults.power.gamma)),
        )
        opc = OPCConfig(
            host=_text(opc_raw, "host", defaults.opc.host, "opc.host"),
            port=int(opc_raw.get("port", defaults.opc.port)),
        )
        origins_raw = server_raw.get("cors_origins", ())
        if isinstance(origins_raw, (str, bytes)) or not isinstance(origins_raw, (list, tuple)):
            raise ConfigError(f"server.cors_origins must be a list, got {origins_raw!r}")
        server = ServerConfig(
            host=_text(server_raw, "host", defaults.server.host, "server.host"),
            port=int(server_raw.get("port", defaults.server.port)),
            cors_origins=tuple(_origin(o) for o in origins_raw),
        )
        simulate_pixels = int(raw.get("simulate_pixels", defaults.simulate_pixels))
        layout_path = Path(_text(raw, "layout_path", defaults.layout_path, "layout_path"))
        state_path = Path(_text(raw, "state_path", defaults.state_path, "state_path"))
        log_level = _log_level(_text(raw, "log_level", defaults.log_level, "log_level"))
        dither = _flag(raw, "dither", defaults.dither, "dither")
        simulate = _flag(raw, "simulate", defaults.simulate, "simulate")
    except (TypeError, ValueError, OverflowError) as exc:
        # OverflowError is what int() raises for an infinity and what float()
        # raises for an integer literal too large to represent; neither is a
        # ValueError, so both would otherwise escape as a traceback.
        raise ConfigError(f"config contains a badly typed value: {exc}") from exc

    for key, number in (
        ("fps", fps),
        ("power.limit_amps", power.limit_amps),
        ("power.ma_per_channel", power.ma_per_channel),
        ("power.idle_ma_per_pixel", power.idle_ma_per_pixel),
        ("power.gamma", power.gamma),
    ):
        if not math.isfinite(number):
            raise ConfigError(f"{key} must be a finite number, got {number}")
    if not 1.0 <= fps <= 240.0:
        raise ConfigError(f"fps must be between 1 and 240, got {fps}")
    if power.limit_amps <= 0:
        raise ConfigError(f"power.limit_amps must be positive, got {power.limit_amps}")
    if not 0 < opc.port < 65536 or not 0 < server.port < 65536:
        raise ConfigError("ports must be in 1..65535")
    if simulate_pixels <= 0:
        raise ConfigError(f"simulate_pixels must be positive, got {simulate_pixels}")

    return Config(
        fps=fps,
        dither=dither,
        power=power,
        opc=opc,
        server=server,
        layout_path=layout_path,
        state_path=state_path,
        simulate=simulate,
        simulate_pixels=simulate_pixels,
        log_level=log_level,
    )


def load_config(path: str | Path | None) -> Config:
    """Load a config file, or return defaults when ``path`` is None.

    A path that was explicitly asked for and does not exist is an error; the
    default path being absent is not, so a bare ``fclights --simulate`` works.
    """
    if path is None:
        return Config()
    path = Path(path)
    try:
        raw = load_json_document(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"config file {path} cannot be read: {exc}") from exc
    except JSONDocumentError as exc:
        raise ConfigError(f"config file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid JSON: {exc}") from exc
    return config_from_dict(raw)


def apply_overrides(config: Config, **overrides: Any) -> Config:
    """Apply non-None command line overrides onto a loaded config.

    The result is revalidated, so a flag is held to the same rules as the file
    it overrides: ``--fps 0`` has to fail the same way ``{"fps": 0}`` does,
    with a message rather than a broken render loop.
    """
    supplied = {k: v for k, v in overrides.items() if v is not None}

    power_keys = {"limit_amps", "ma_per_channel", "idle_ma_per_pixel", "gamma"}
    power_changes = {k: supplied.pop(k) for k in list(supplied) if k in power_keys}
    if power_changes:
        config = replace(config, power=replace(config.power, **power_changes))

    opc_changes = {}
    if "opc_host" in supplied:
        opc_changes["host"] = supplied.pop("opc_host")
    if "opc_port" in supplied:
        opc_changes["port"] = supplied.pop("opc_port")
    if opc_changes:
        config = replace(config, opc=replace(config.opc, **opc_changes))

    server_changes = {}
    if "host" in supplied:
        server_changes["host"] = supplied.pop("host")
    if "port" in supplied:
        server_changes["port"] = supplied.pop("port")
    if server_changes:
        config = replace(config, server=replace(config.server, **server_changes))

    if supplied:
        config = replace(config, **supplied)
    return config_from_dict(config.to_dict())

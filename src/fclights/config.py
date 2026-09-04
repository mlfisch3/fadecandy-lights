"""Service configuration.

Configuration is a JSON file plus command line overrides.  It covers the things
an installer sets once - supply ceiling, frame rate, where fcserver is, where
state lives - and nothing that the API can change at runtime.  Anything a phone
can change belongs in :mod:`fclights.state`, which persists separately.

See ``config/fclights.example.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fclights.power import DEFAULT_IDLE_MA_PER_PIXEL, DEFAULT_MA_PER_CHANNEL

DEFAULT_CONFIG_PATH = Path("/etc/fclights/fclights.json")
DEFAULT_LAYOUT_PATH = Path("/etc/fclights/layout.json")
DEFAULT_STATE_PATH = Path("/var/lib/fclights/state.json")


class ConfigError(ValueError):
    """Raised when the config file is malformed or self-contradictory."""


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
            host=str(opc_raw.get("host", defaults.opc.host)),
            port=int(opc_raw.get("port", defaults.opc.port)),
        )
        server = ServerConfig(
            host=str(server_raw.get("host", defaults.server.host)),
            port=int(server_raw.get("port", defaults.server.port)),
            cors_origins=tuple(str(o) for o in server_raw.get("cors_origins", ())),
        )
        simulate_pixels = int(raw.get("simulate_pixels", defaults.simulate_pixels))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"config contains a badly typed value: {exc}") from exc

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
        dither=bool(raw.get("dither", defaults.dither)),
        power=power,
        opc=opc,
        server=server,
        layout_path=Path(raw.get("layout_path", defaults.layout_path)),
        state_path=Path(raw.get("state_path", defaults.state_path)),
        simulate=bool(raw.get("simulate", defaults.simulate)),
        simulate_pixels=simulate_pixels,
        log_level=str(raw.get("log_level", defaults.log_level)).upper(),
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
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid JSON: {exc}") from exc
    return config_from_dict(raw)


def apply_overrides(config: Config, **overrides: Any) -> Config:
    """Apply non-None command line overrides onto a loaded config."""
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

    return replace(config, **supplied) if supplied else config

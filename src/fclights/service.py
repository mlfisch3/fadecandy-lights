"""Wiring: turn a :class:`~fclights.config.Config` into a running service."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fclights.api import Controller, create_app
from fclights.config import Config
from fclights.engine import Engine
from fclights.layout import Layout, load_layout, simple_layout
from fclights.opc import FrameSink, NullSink, OPCClient
from fclights.power import PowerGovernor
from fclights.state import StateStore

log = logging.getLogger(__name__)


@dataclass
class Service:
    config: Config
    layout: Layout
    store: StateStore
    engine: Engine
    controller: Controller
    sink: FrameSink


def build_layout_for(config: Config) -> Layout:
    """Load the configured layout, or synthesise one in simulate mode.

    Simulate mode without a layout file is the common case for the Android
    developer, who has neither a Pi nor a strip; it should just work.
    """
    if config.simulate and not config.layout_path.exists():
        log.info(
            "simulating a straight %d-pixel strip (no layout file at %s)",
            config.simulate_pixels,
            config.layout_path,
        )
        return simple_layout(config.simulate_pixels)
    return load_layout(config.layout_path)


def build_service(config: Config) -> Service:
    """Construct every component and hand back the assembled service."""
    layout = build_layout_for(config)

    governor = PowerGovernor(
        limit_amps=config.power.limit_amps,
        pixel_count=layout.pixel_count,
        ma_per_channel=config.power.ma_per_channel,
        idle_ma_per_pixel=config.power.idle_ma_per_pixel,
        gamma=config.power.gamma,
    )
    log.info(
        "power ceiling %.2f A; %d pixels would draw %.2f A at full white",
        governor.limit_amps,
        layout.pixel_count,
        governor.full_white_amps,
    )
    if governor.full_white_amps > governor.limit_amps:
        log.info(
            "full white exceeds the ceiling, so frames will be clamped to about "
            "%.0f%% of full - this is expected and is what keeps the supply safe",
            100.0 * governor.limit_amps / governor.full_white_amps,
        )

    sink: FrameSink = (
        NullSink() if config.simulate else OPCClient(config.opc.host, config.opc.port)
    )

    store = StateStore(config.state_path)
    store.load()

    engine = Engine(layout, sink, config, governor=governor)
    engine.apply_state(store.state)

    controller = Controller(store, engine, layout, config)
    return Service(
        config=config,
        layout=layout,
        store=store,
        engine=engine,
        controller=controller,
        sink=sink,
    )


def build_app(config: Config):
    """Build the ASGI app for ``config``. The entry point uvicorn is handed."""
    return create_app(build_service(config).controller)

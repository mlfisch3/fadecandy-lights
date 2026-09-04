"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from fclights import __version__
from fclights.config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    apply_overrides,
    load_config,
)
from fclights.layout import LayoutError

# In simulate mode the packaged defaults point at /etc and /var, which a
# developer running this on a laptop cannot write. Fall back to the working
# directory so `fclights --simulate` needs no setup at all.
SIMULATE_STATE_PATH = Path("./fclights-state.json")


SUBCOMMANDS = ("run", "check", "announce")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fclights",
        description="Fadecandy-driven WS2812B lighting controller.",
    )
    parser.add_argument("--version", action="version", version=f"fclights {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    # Every subcommand takes the same options. They all need to resolve the same
    # config, and it avoids "that flag works on run but not on check" surprises.
    _add_run_arguments(sub.add_parser("run", help="run the control service (the default)"))

    check = sub.add_parser(
        "check", help="validate the config and layout, print a power summary, and exit"
    )
    _add_run_arguments(check)

    announce = sub.add_parser(
        "announce", help="write the Avahi mDNS service file and exit (needs root)"
    )
    _add_run_arguments(announce)
    announce.add_argument(
        "--services-dir",
        type=Path,
        default=None,
        help="where to write the service file (default /etc/avahi/services)",
    )

    return parser


def insert_default_command(argv: list[str]) -> list[str]:
    """Make ``run`` the implicit subcommand, so ``fclights --simulate`` works."""
    for token in argv:
        if token in SUBCOMMANDS or token in ("-h", "--help", "--version"):
            return argv
    return ["run", *argv]


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help=f"config file (default {DEFAULT_CONFIG_PATH} if it exists)",
    )
    parser.add_argument("--layout", type=Path, default=None, dest="layout_path")
    parser.add_argument("--state", type=Path, default=None, dest="state_path")
    parser.add_argument("--host", default=None, help="API bind address")
    parser.add_argument("--port", type=int, default=None, help="API port")
    parser.add_argument("--opc-host", default=None, help="fcserver host")
    parser.add_argument("--opc-port", type=int, default=None, help="fcserver OPC port")
    parser.add_argument("--fps", type=float, default=None, help="target frame rate")
    parser.add_argument(
        "--limit-amps",
        type=float,
        default=None,
        dest="limit_amps",
        help="supply ceiling at 5 V; frames are hard-clamped to fit it",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        default=None,
        help="run with no Fadecandy and no fcserver attached",
    )
    parser.add_argument(
        "--pixels",
        type=int,
        default=None,
        dest="simulate_pixels",
        help="pixel count to simulate when there is no layout file",
    )
    parser.add_argument("--log-level", default=None, dest="log_level")


def resolve_config(args: argparse.Namespace) -> Config:
    """Load the config file and fold the command line over it."""
    config_path = args.config
    if config_path is None and DEFAULT_CONFIG_PATH.exists():
        config_path = DEFAULT_CONFIG_PATH

    config = load_config(config_path)
    config = apply_overrides(
        config,
        **{
            key: getattr(args, key, None)
            for key in (
                "layout_path",
                "state_path",
                "host",
                "port",
                "opc_host",
                "opc_port",
                "fps",
                "limit_amps",
                "simulate",
                "simulate_pixels",
                "log_level",
            )
        },
    )

    if config.simulate and getattr(args, "state_path", None) is None:
        explicit = config_path is not None and config.state_path != Config().state_path
        if not explicit:
            config = apply_overrides(config, state_path=SIMULATE_STATE_PATH)

    return config


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def cmd_run(args: argparse.Namespace) -> int:
    import uvicorn

    from fclights.service import build_service

    config = resolve_config(args)
    configure_logging(config.log_level)
    log = logging.getLogger("fclights")

    service = build_service(config)
    from fclights.api import create_app

    app = create_app(service.controller)

    log.info(
        "serving the control API on http://%s:%d (OPC sink: %s)",
        config.server.host,
        config.server.port,
        getattr(service.sink, "endpoint", "unknown"),
    )
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.log_level.lower(),
        access_log=False,
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from fclights.power import PowerGovernor
    from fclights.service import build_layout_for

    config = resolve_config(args)
    configure_logging("WARNING")

    layout = build_layout_for(config)
    governor = PowerGovernor(
        limit_amps=config.power.limit_amps,
        pixel_count=layout.pixel_count,
        ma_per_channel=config.power.ma_per_channel,
        idle_ma_per_pixel=config.power.idle_ma_per_pixel,
        gamma=config.power.gamma,
    )

    print(f"layout          {layout.name}")
    print(f"devices         {len(layout.devices)}")
    print(f"pixels          {layout.pixel_count}")
    print(f"outputs         {layout.segment_count}")
    print(f"frame rate      {config.fps:g} fps")
    print(f"API             http://{config.server.host}:{config.server.port}")
    print(f"OPC sink        {config.opc.host}:{config.opc.port}")
    print()
    print(f"supply ceiling  {governor.limit_amps:.2f} A at 5 V")
    print(f"idle draw       {governor.idle_amps:.3f} A (all pixels off)")
    print(f"full white      {governor.full_white_amps:.2f} A")
    ratio = governor.limit_amps / governor.full_white_amps
    if ratio >= 1.0:
        print("headroom        the supply covers full white; no clamping will occur")
    else:
        print(
            f"headroom        frames are clamped at about {ratio * 100:.0f}% of full white "
            f"({governor.limit_amps * 5:.0f} W)"
        )
    return 0


def cmd_announce(args: argparse.Namespace) -> int:
    from fclights.discovery import AVAHI_SERVICES_DIR, write_service_file
    from fclights.service import build_layout_for

    config = resolve_config(args)
    configure_logging(config.log_level)

    layout = build_layout_for(config)
    directory = args.services_dir or AVAHI_SERVICES_DIR
    try:
        path = write_service_file(
            port=config.server.port,
            version=__version__,
            pixels=layout.pixel_count,
            directory=directory,
        )
    except PermissionError:
        print(f"cannot write to {directory}; rerun with sudo", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    print("run 'sudo systemctl reload avahi-daemon' to publish it")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(insert_default_command(list(sys.argv[1:] if argv is None else argv)))

    handlers = {"run": cmd_run, "check": cmd_check, "announce": cmd_announce}
    handler = handlers[args.command]

    try:
        return handler(args)
    except (ConfigError, LayoutError) as exc:
        print(f"fclights: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

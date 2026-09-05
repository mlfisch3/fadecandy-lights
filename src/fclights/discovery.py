"""mDNS service advertisement.

The captain's phone joins a normal home network with DHCP, so the Pi's address
is not knowable in advance and hardcoding one is a support call waiting to
happen.  Advertising over mDNS lets the Android app browse for ``_fclights._tcp``
and find the rig by name.

Advertising is done by Avahi, which is already on Raspberry Pi OS, via a static
service file dropped in ``/etc/avahi/services``.  That keeps a daemon out of our
process and means the rig is discoverable even while our service is restarting.
This module writes that file; ``deploy/setup.sh`` calls it during install.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

AVAHI_SERVICES_DIR = Path("/etc/avahi/services")
SERVICE_FILENAME = "fclights.service"
SERVICE_TYPE = "_fclights._tcp"

_TEMPLATE = """<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<!-- Written by fclights. Regenerate with: fclights announce -->
<service-group>
  <name replace-wildcards="yes">{name}</name>
  <service>
    <type>{service_type}</type>
    <port>{port}</port>
    <txt-record>path=/api</txt-record>
    <txt-record>version={version}</txt-record>
    <txt-record>pixels={pixels}</txt-record>
  </service>
</service-group>
"""


def render_service_file(
    *, port: int, version: str, pixels: int, name: str = "fclights on %h"
) -> str:
    """Render the Avahi static service document.

    ``%h`` is Avahi's substitution for the host name, so a second rig on the
    same network announces itself distinctly without any extra configuration.
    """
    return _TEMPLATE.format(
        name=name, service_type=SERVICE_TYPE, port=port, version=version, pixels=pixels
    )


def write_service_file(
    *,
    port: int,
    version: str,
    pixels: int,
    directory: Path = AVAHI_SERVICES_DIR,
    name: str = "fclights on %h",
) -> Path:
    """Write the Avahi service file. Returns the path written."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SERVICE_FILENAME
    path.write_text(
        render_service_file(port=port, version=version, pixels=pixels, name=name),
        encoding="utf-8",
    )
    log.info("advertised %s on port %d via %s", SERVICE_TYPE, port, path)
    return path

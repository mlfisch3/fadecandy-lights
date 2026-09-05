"""Reading JSON documents that came from outside this process.

``json.loads`` is more permissive than JSON itself in two ways that matter to a
service which has to come up unattended:

- it accepts the bare ``NaN``, ``Infinity`` and ``-Infinity`` literals as an
  extension, and
- it silently overflows an otherwise ordinary literal such as ``1e400`` to
  ``inf`` without ever consulting ``parse_constant``.

A non-finite number reaching the engine is never something to repair: it passes
every range check, because every comparison against NaN is False; it makes the
power governor's clamp a NaN multiply; and it comes back out of the WebSocket as
a bare ``NaN`` token, which is not valid JSON and which the Android client's
parser refuses outright.

So the config and layout loaders refuse it at the file boundary instead, where
the caller can still report it as a configuration mistake and exit cleanly.
Persisted *state* deliberately does not use this: a bad scene there is repaired
or dropped field by field, because failing to restore the last look must never
be the reason the lights do not come up.
"""

from __future__ import annotations

import json
import math
from typing import Any


class JSONDocumentError(ValueError):
    """Raised when a document parses as JSON but is not JSON we accept."""


def _reject_constant(token: str) -> float:
    raise JSONDocumentError(f"{token} is not a number this service accepts")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise JSONDocumentError(f"{text} is not a finite number")
    return value


def loads(text: str) -> Any:
    """Parse a JSON document, refusing a non-finite number anywhere in it."""
    return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)

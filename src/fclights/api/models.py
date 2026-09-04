"""Request and response bodies for the control API.

These are the wire contract the Android app is built against; ``docs/api.md``
is generated from the same shapes and is the normative description.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Reject unknown fields, so a client typo fails loudly instead of silently."""

    model_config = ConfigDict(extra="forbid")


class PowerRequest(StrictModel):
    on: bool = Field(description="True to light the strip, false for master off.")


class BrightnessRequest(StrictModel):
    brightness: float = Field(ge=0.0, le=1.0, description="Global master brightness, 0..1.")


class EffectRequest(StrictModel):
    effect: str = Field(description="Registered effect name, from GET /api/effects.")
    params: dict[str, Any] | None = Field(
        default=None,
        description="Parameter values. Anything omitted takes the effect's default.",
    )


class ParamsRequest(StrictModel):
    params: dict[str, Any] = Field(
        description="Partial parameter update, merged over the current values."
    )


class SceneCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120, description="Human-facing scene name.")


class SceneUpdateRequest(StrictModel):
    name: str | None = Field(
        default=None, min_length=1, max_length=120, description="New name for the scene."
    )
    capture: bool = Field(
        default=False,
        description="Overwrite the scene's effect, parameters and brightness with the live look.",
    )


class ErrorResponse(BaseModel):
    error: str
    detail: str

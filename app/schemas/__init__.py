"""Shared Pydantic base types for API boundary models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "APIModel",
    "ComponentStatus",
    "HealthResponse",
]


class APIModel(BaseModel):
    """Base class for all API boundary models: immutable, no unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


ComponentStatus = Literal["ok", "error"]


class HealthResponse(APIModel):
    """Response body for `GET /health`."""

    status: Literal["ok", "degraded"]
    db: ComponentStatus
    qdrant: ComponentStatus

"""Shared Pydantic base types for API boundary models."""

from typing import Literal

from app.schemas.base import APIModel
from app.schemas.input import CodeBlock, InputSource, StructuredInput
from app.schemas.intent import (
    LOW_CONFIDENCE_THRESHOLD,
    Intent,
    IntentResult,
    IntentSource,
    UnderstandResponse,
)

__all__ = [
    "APIModel",
    "ComponentStatus",
    "HealthResponse",
    "CodeBlock",
    "InputSource",
    "StructuredInput",
    "Intent",
    "IntentResult",
    "IntentSource",
    "LOW_CONFIDENCE_THRESHOLD",
    "UnderstandResponse",
]


ComponentStatus = Literal["ok", "error"]


class HealthResponse(APIModel):
    """Response body for `GET /health`."""

    status: Literal["ok", "degraded"]
    db: ComponentStatus
    qdrant: ComponentStatus

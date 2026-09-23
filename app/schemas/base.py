"""Base Pydantic model shared by all API boundary schemas."""

from pydantic import BaseModel, ConfigDict

__all__ = ["APIModel"]


class APIModel(BaseModel):
    """Base class for all API boundary models: immutable, no unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

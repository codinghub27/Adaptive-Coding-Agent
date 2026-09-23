"""Conversation message view schema.

Not part of the phase's originally declared file list; added because
`LearningEventView`-style read models need a matching view for `Message` rows
(e.g. for conversation history endpoints in a later packet).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict

from app.schemas.base import APIModel
from app.schemas.intent import Intent

__all__ = ["MessageView", "Role"]

Role = Literal["user", "assistant"]


class MessageView(APIModel):
    """A single stored message, returned to callers."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    seq: int
    role: Role
    content: str
    intent: Intent | None
    created_at: datetime

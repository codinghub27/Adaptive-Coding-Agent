"""Conversation message view schema.

Not part of the phase's originally declared file list; added because
`LearningEventView`-style read models need a matching view for `Message` rows
(e.g. for conversation history endpoints in a later packet).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.schemas.base import APIModel
from app.schemas.intent import Intent

__all__ = [
    "ConversationCreateRequest",
    "ConversationCreateResponse",
    "ConversationRenameRequest",
    "ConversationSummary",
    "MessageView",
    "Role",
]

Role = Literal["user", "assistant"]


class ConversationCreateRequest(APIModel):
    """Body of `POST /conversations`. `title` matches `start_conversation`'s
    own 200-character limit."""

    title: str | None = Field(default=None, max_length=200)


class ConversationCreateResponse(APIModel):
    """Response body of `POST /conversations`."""

    conversation_id: UUID
    title: str | None


class ConversationSummary(APIModel):
    """A conversation as it appears in a listing (sidebar), not its full history.

    `updated_at` is the conversation's most recent message `created_at`; if
    the conversation has no messages yet, it falls back to the conversation's
    own `created_at`. `message_count` counts every message in the
    conversation, regardless of role.
    """

    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int


class ConversationRenameRequest(APIModel):
    """Body of `PATCH /conversations/{conversation_id}`."""

    title: str = Field(min_length=1, max_length=200)


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

"""The `Message` ORM model: a single turn within a conversation."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db.base import Base

__all__ = ["Message"]


class Message(Base):
    """A single user or assistant turn within a conversation.

    The composite foreign key to `conversations` (on `conversation_id` and
    `user_id` together) guarantees a message's `user_id` always matches the
    owner of the conversation it belongs to.
    """

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["conversations.id", "conversations.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_id_seq"),
        CheckConstraint("role IN ('user','assistant')", name="role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: What changed in this reply because of the learner (fixed sentences from
    #: `app.tutoring.adaptation`), or NULL: a user message, or nothing changed.
    adaptation: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    #: The image the learner shared with this message, or NULL. Loaded only by
    #: the endpoint that serves it (`deferred`): history and prompts never
    #: carry the bytes.
    image: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    image_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: What was read from that image (the problem, code or error in it), so a
    #: later turn can still see what the learner showed.
    image_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

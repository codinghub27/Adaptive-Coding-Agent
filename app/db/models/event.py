"""The `LearningEvent` ORM model: an append-only log of learning activity.

`id` is caller-supplied (no server default) so callers can safely retry event
submission idempotently. `seq` is a separately assigned monotonic identity
column used for deterministic ordering and replay.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

__all__ = ["LearningEvent"]


class LearningEvent(Base):
    """A single recorded learning event (an attempt, hint request, etc.)."""

    __tablename__ = "learning_events"
    __table_args__ = (
        Index("ix_learning_events_user_id_seq", "user_id", "seq"),
        Index("ix_learning_events_conversation_id", "conversation_id"),
        # Composite FK (rather than a plain `conversation_id -> conversations.id`
        # FK) so the database itself enforces that an event's conversation is
        # owned by the same user as the event: `conversation_id` can only
        # reference a `(id, user_id)` pair that matches this row's `user_id`.
        # The PG15+ column-list `ON DELETE SET NULL (conversation_id)` form
        # nulls only `conversation_id` when the conversation is deleted,
        # leaving `user_id` (and the rest of the row) intact.
        ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["conversations.id", "conversations.user_id"],
            ondelete="SET NULL (conversation_id)",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    problem: Mapped[str | None] = mapped_column(String(200), nullable=True)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    pattern: Mapped[str | None] = mapped_column(String(64), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    requested_help: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hints_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needed_full_solution: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    errors: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # NULL means "topic encountered, outcome unknown" (e.g. a hint request)
    # -- distinct from `False` ("observed as unsolved"). See
    # `app.memory.profile.apply_event`.
    solved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    time_spent: Mapped[int | None] = mapped_column(Integer, nullable=True)  # minutes
    concepts: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

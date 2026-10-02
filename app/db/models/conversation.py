"""The `Conversation` ORM model: a thread of messages belonging to a user."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

__all__ = ["Conversation"]


class Conversation(Base):
    """A single conversation thread owned by a user."""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("id", "user_id", name="uq_conversations_id_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # --- The conversation's ACTIVE PROBLEM (ADAPTIVE-upgrade P1) -------------
    # The most recent problem statement this conversation worked on, so a bare
    # follow-up ("give full answer", "next hint") resolves to that problem and
    # topic instead of to nothing. `active_problem` is the stored
    # `StructuredInput` dump: UNTRUSTED learner data, stored exactly like
    # `messages.content` is, and only ever fed back into the same untrusted
    # slots it came from. `active_problem_key` is a hash of the statement (the
    # hint-ladder key); `active_topic` is a closed-vocabulary corpus slug.
    active_problem: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    active_problem_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active_topic: Mapped[str | None] = mapped_column(String(64), nullable=True)

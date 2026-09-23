"""The `Conversation` ORM model: a thread of messages belonging to a user."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
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

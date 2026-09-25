"""The `HintProgress` ORM model: per-conversation-topic hint-ladder progress.

This is conversation *state*, not evidence about the learner -- unlike
`LearningEvent`, a row here records how far the hint ladder has been climbed
for a `(user, conversation, topic)` triple, not an observed outcome. It is
upserted in place (see `app.memory.hint_progress`), never appended to.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

__all__ = ["HintProgress"]


class HintProgress(Base):
    """How far a learner has climbed the hint ladder on one conversation+topic."""

    __tablename__ = "hint_progress"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "conversation_id",
            "topic",
            name="uq_hint_progress_user_id_conversation_id_topic",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    solved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

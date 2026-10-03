"""The `LearnerProfile` ORM model: per-user aggregated learning state."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

__all__ = ["LearnerProfile"]


class LearnerProfile(Base):
    """Aggregated skill levels, preferences, and error patterns for a user."""

    __tablename__ = "learner_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    skill_levels: Mapped[dict[str, float]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    learning_preferences: Mapped[dict[str, bool]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    common_errors: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: skill key -> ISO time of its last OBSERVED outcome (P6): drives read-time
    #: decay and "current focus". Exposure (no outcome) never stamps it.
    skill_seen: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: error tag / misconception id -> ISO time it was last recorded
    #: (ADAPTIVE-tutoring G3: "count + last_seen"; counts stay in `common_errors`).
    common_errors_seen: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

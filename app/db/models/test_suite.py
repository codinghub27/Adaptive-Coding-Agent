"""The `CachedTestSuite` ORM model: a validated test suite kept per learner+subject.

A suite the sandbox already validated (its reference solution passed every
case) is reused the next time the same learner works on the same subject, so
the same code is judged against the same cases every time instead of whatever
the model proposes on that turn.

Scoped to the user on purpose: the cases derive from a learner-supplied
statement, so a cache shared across users would let one learner's crafted
"Two Sum" decide another learner's verdict.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

__all__ = ["CachedTestSuite"]


class CachedTestSuite(Base):
    """Validated cases for one `(user, subject)`; upserted, never appended."""

    __tablename__ = "test_suite_cache"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    #: A problem title slug (`t:valid-parenthesis-string`) or a hash of the
    #: statement / shared code. Never the text itself.
    subject_key: Mapped[str] = mapped_column(String(96), primary_key=True)
    #: `list[TestCase]` as JSON. Untrusted-derived data: only ever sent back
    #: into the sandbox as test input, never rendered as instructions.
    cases: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

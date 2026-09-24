"""The `User` ORM model: the root identity all other memory tables hang off."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

__all__ = ["User"]


class User(Base):
    """A learner account."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    handle: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    #: Bcrypt hash of the user's password only -- never the raw password.
    #: `None` for users created without a password (e.g. existing test rows);
    #: such users have no way to log in until a password is set.
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

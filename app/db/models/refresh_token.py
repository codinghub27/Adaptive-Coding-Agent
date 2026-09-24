"""The `RefreshToken` ORM model: persisted, hashed refresh-token state.

This table exists purely for validation and revocation of refresh tokens -- it
stores only a SHA-256 hash of each token, never the raw value. `id` is the
refresh JWT's `jti`, supplied by the caller. `session_id` stays constant
across every rotation of a single login (decision D in
`docs/features/AUTH-jwt.md`), which lets `session_is_active` immediately
revoke every access token tied to that session with one indexed query.

Only the composite `ix_refresh_tokens_user_id_session_id` index is created
(no separate single-column indexes on `user_id` or `session_id`): it covers
every query pattern used here -- `(user_id)` alone and `(user_id,
session_id)` -- as leading-column prefixes, and hash lookups use the unique
constraint on `token_hash` instead.

`revoked_reason` records *why* a row was revoked, which matters for reuse
detection: only a token revoked by rotation and then presented again
indicates theft (`app.auth.routes.refresh`). A token revoked by logout, or
by an earlier reuse revocation, is simply invalid on its own.
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

__all__ = ["RefreshToken", "RevokedReason"]

#: Why a refresh token row was revoked. `"rotated"` -- superseded by
#: `/auth/refresh`; presenting it again is reuse. `"logout"` -- revoked by
#: `/auth/logout`. `"reuse_detected"` -- revoked as a side effect of another
#: row's reuse being detected. `None` (unset) means the row is not revoked.
RevokedReason = Literal["rotated", "logout", "reuse_detected"]


class RefreshToken(Base):
    """A persisted, hashed refresh token belonging to a user's login session."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # Composite index for the per-request "is this session still active"
        # check (`session_is_active`), and for revoking all of a user's
        # sessions -- covers both `(user_id)` and `(user_id, session_id)`.
        Index("ix_refresh_tokens_user_id_session_id", "user_id", "session_id"),
        CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('rotated', 'logout', 'reuse_detected')",
            name="revoked_reason_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    #: SHA-256 hex digest of the raw token -- never the raw token itself.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Why this row was revoked; `None` while `revoked` is `False`. See
    #: `RevokedReason` for the meaning of each value.
    revoked_reason: Mapped[RevokedReason | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

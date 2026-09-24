"""User and refresh-token persistence.

None of the functions here commit the session -- callers own the transaction
and must `await session.commit()` (or roll back) themselves, per project
convention. All refresh-token timestamps are timezone-aware UTC.
"""

import re
import uuid
from datetime import datetime
from typing import Any, Final, cast

from sqlalchemy import CursorResult, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password_async, hash_token
from app.auth.timeutil import resolve_now
from app.db.models import RefreshToken, User
from app.db.models.refresh_token import RevokedReason

__all__ = [
    "HandleTakenError",
    "RevokedReason",
    "create_refresh_token",
    "create_user",
    "get_refresh_token",
    "get_user",
    "get_user_by_handle",
    "get_valid_refresh_token",
    "lock_user",
    "normalize_handle",
    "revoke_all_for_user",
    "revoke_refresh_token",
    "revoke_refresh_token_by_id",
    "revoke_session",
    "session_is_active",
]

#: 3-64 characters; letters, digits, underscore, dot, hyphen.
_HANDLE_PATTERN: Final = re.compile(r"[A-Za-z0-9_.-]{3,64}")


class HandleTakenError(Exception):
    """Raised when a handle is already registered."""


def normalize_handle(handle: str) -> str:
    """Strip surrounding whitespace from `handle`.

    Used by both `create_user` and `get_user_by_handle` so a handle typed
    with leading/trailing whitespace (e.g. `" alice"`) registers and later
    logs in as the same handle as `"alice"`. Handles are otherwise compared
    case-sensitively (`"Alice"` and `"alice"` are distinct) -- this is a
    deliberate simplification, not a full normalization step.
    """
    return handle.strip()


async def create_user(session: AsyncSession, handle: str, password: str) -> User:
    """Create a new user with a bcrypt-hashed password.

    `handle` is normalized (`normalize_handle`) and must be 3-64 characters
    from `[A-Za-z0-9_.-]` after normalization.

    Raises `ValueError` for an invalid handle, `PasswordPolicyError` (from
    `app.auth.security`) for an invalid password, and `HandleTakenError` if
    the handle is already registered. Checks for a duplicate up front, then
    re-verifies via a nested transaction around the insert so a concurrent
    registration of the same handle is still caught (`IntegrityError` on the
    unique constraint) instead of raising unexpectedly to the caller. Only
    flushes; the caller commits.
    """
    handle = normalize_handle(handle)
    if not _HANDLE_PATTERN.fullmatch(handle):
        raise ValueError("username must be 3-64 characters from [A-Za-z0-9_.-]")

    if await get_user_by_handle(session, handle) is not None:
        raise HandleTakenError(handle)

    password_hash = await hash_password_async(password)
    user = User(handle=handle, password_hash=password_hash)
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError as exc:
        raise HandleTakenError(handle) from exc
    return user


async def get_user_by_handle(session: AsyncSession, handle: str) -> User | None:
    """Look up a user by exact, case-sensitive, normalized handle.

    `handle` is normalized (`normalize_handle`) before comparison, so
    `" alice"` finds the same row as `"alice"`. Returns `None` if absent.
    """
    result = await session.execute(select(User).where(User.handle == normalize_handle(handle)))
    return result.scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Look up a user by id. Returns `None` if absent."""
    return await session.get(User, user_id)


async def lock_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Look up a user by id, taking a row lock (`SELECT ... FOR UPDATE`).

    Used to serialize concurrent mutations of one user's refresh tokens (see
    `app.auth.routes.refresh`/`logout`): taking this lock *before* any
    read-then-mutate on the user's refresh tokens means a later statement,
    once it acquires the lock, is guaranteed to see every refresh-token
    change a concurrent, already-committed transaction made for this user.
    Returns `None` if the user doesn't exist.
    """
    result = await session.execute(select(User).where(User.id == user_id).with_for_update())
    return result.scalar_one_or_none()


async def create_refresh_token(
    session: AsyncSession,
    *,
    token_id: uuid.UUID,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    token: str,
    expires_at: datetime,
) -> RefreshToken:
    """Persist a new refresh token, storing only its SHA-256 hash.

    `token_id` becomes the row's primary key (the refresh JWT's `jti`).
    `session_id` identifies the login session this token belongs to and stays
    the same across rotations (decision D). Raises `ValueError` if
    `expires_at` is naive. Only flushes; the caller commits.
    """
    if expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")

    refresh_token = RefreshToken(
        id=token_id,
        user_id=user_id,
        session_id=session_id,
        token_hash=hash_token(token),
        expires_at=expires_at,
    )
    session.add(refresh_token)
    await session.flush()
    return refresh_token


async def get_refresh_token(
    session: AsyncSession, token: str, *, for_update: bool = False
) -> RefreshToken | None:
    """Look up a refresh token by hash, in any state (including revoked).

    Used for reuse detection, where a revoked-but-presented token still needs
    to be found. Pass `for_update=True` to lock the row (`SELECT ... FOR
    UPDATE`) while deciding whether to rotate or revoke it.
    """
    stmt = select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_valid_refresh_token(
    session: AsyncSession,
    token: str,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
    for_update: bool = False,
) -> RefreshToken | None:
    """Look up a refresh token that is usable right now.

    Returns the row only if the hash matches, it belongs to `user_id`, it is
    not revoked, and it has not expired as of `now` (defaults to the current
    UTC time). Returns `None` otherwise.
    """
    now = resolve_now(now)
    stmt = select(RefreshToken).where(
        RefreshToken.token_hash == hash_token(token),
        RefreshToken.user_id == user_id,
        RefreshToken.revoked.is_(False),
        RefreshToken.expires_at > now,
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def revoke_refresh_token(
    session: AsyncSession, token: str, *, reason: RevokedReason, now: datetime | None = None
) -> bool:
    """Revoke a refresh token by hash. Idempotent: returns `True` only the
    first time (i.e. if this call newly revoked the row), `False` if it was
    already revoked or doesn't exist. An already-revoked row keeps its
    original `revoked_reason` -- `reason` only applies to the row this call
    newly revokes.
    """
    now = resolve_now(now)
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.token_hash == hash_token(token), RefreshToken.revoked.is_(False))
        .values(revoked=True, revoked_at=now, revoked_reason=reason)
    )
    result = cast(CursorResult[Any], await session.execute(stmt))
    return result.rowcount > 0


async def revoke_refresh_token_by_id(
    session: AsyncSession,
    token_id: uuid.UUID,
    *,
    reason: RevokedReason,
    now: datetime | None = None,
) -> bool:
    """Revoke a refresh token by its primary key (`jti`), not by re-hashing it.

    Used by `app.auth.routes.refresh`, which already holds the row (locked,
    looked up by hash) and would otherwise have to hash the raw token a
    second time just to revoke it. Same idempotency semantics as
    `revoke_refresh_token`: returns `True` only if this call newly revoked
    the row, `False` if it was already revoked or doesn't exist.
    """
    now = resolve_now(now)
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.id == token_id, RefreshToken.revoked.is_(False))
        .values(revoked=True, revoked_at=now, revoked_reason=reason)
    )
    result = cast(CursorResult[Any], await session.execute(stmt))
    return result.rowcount > 0


async def revoke_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    reason: RevokedReason = "logout",
    now: datetime | None = None,
) -> int:
    """Revoke every non-revoked refresh token for one user's session.

    Returns the number of rows revoked. Leaves the user's other sessions
    untouched. Already-revoked rows keep their original `revoked_reason`.
    """
    now = resolve_now(now)
    stmt = (
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.session_id == session_id,
            RefreshToken.revoked.is_(False),
        )
        .values(revoked=True, revoked_at=now, revoked_reason=reason)
    )
    result = cast(CursorResult[Any], await session.execute(stmt))
    return result.rowcount


async def revoke_all_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    reason: RevokedReason = "reuse_detected",
    now: datetime | None = None,
) -> int:
    """Revoke every non-revoked refresh token belonging to `user_id`.

    Returns the number of rows revoked. Used for reuse detection (decision C):
    a revoked refresh token presented again revokes every session for that
    user. Already-revoked rows keep their original `revoked_reason`.
    """
    now = resolve_now(now)
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
        .values(revoked=True, revoked_at=now, revoked_reason=reason)
    )
    result = cast(CursorResult[Any], await session.execute(stmt))
    return result.rowcount


async def session_is_active(
    session: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID, now: datetime | None = None
) -> bool:
    """Whether `session_id` has a live (non-revoked, unexpired) refresh token.

    A single indexed `EXISTS` query, used on every authenticated request to
    check the session an access token's `sid` claim refers to (decision D).
    """
    now = resolve_now(now)
    stmt = select(
        exists().where(
            RefreshToken.user_id == user_id,
            RefreshToken.session_id == session_id,
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > now,
        )
    )
    result = await session.execute(stmt)
    return bool(result.scalar_one())

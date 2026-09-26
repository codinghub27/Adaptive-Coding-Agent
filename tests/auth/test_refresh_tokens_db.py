"""Tests for `app.db.auth` refresh-token persistence against a real Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_token
from app.db.auth import (
    create_refresh_token,
    create_user,
    get_refresh_token,
    get_valid_refresh_token,
    revoke_all_for_user,
    revoke_refresh_token,
    revoke_session,
    session_is_active,
)
from app.db.models import RefreshToken, User

pytestmark = pytest.mark.db

#: A fixed reference instant so expiry comparisons in tests are deterministic.
NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = NOW + timedelta(days=7)


async def _make_user(session: AsyncSession) -> uuid.UUID:
    user = await create_user(
        session, f"user-{uuid.uuid4().hex[:12]}", "correct horse battery staple"
    )
    return user.id


async def _make_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
    token: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[RefreshToken, str, uuid.UUID]:
    session_id = session_id if session_id is not None else uuid.uuid4()
    token = token if token is not None else uuid.uuid4().hex
    expires_at = expires_at if expires_at is not None else LATER
    refresh_token = await create_refresh_token(
        session,
        token_id=uuid.uuid4(),
        user_id=user_id,
        session_id=session_id,
        token=token,
        expires_at=expires_at,
    )
    return refresh_token, token, session_id


async def test_create_refresh_token_never_stores_raw_token(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id)

    # Scoped to this test's own user: an unfiltered select assumes a globally
    # empty `refresh_tokens` table, which is false on any dev DB that has been
    # used (the fixture rolls back its own rows, not pre-existing ones).
    result = await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))
    row = result.scalar_one()

    for column in RefreshToken.__table__.columns:
        value = getattr(row, column.name)
        assert value != token
        assert token not in str(value)
    assert row.token_hash == hash_token(token)


async def test_get_valid_refresh_token_happy_path(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    refresh_token, token, _ = await _make_token(db_session, user_id=user_id)

    found = await get_valid_refresh_token(db_session, token, user_id=user_id, now=NOW)
    assert found is not None
    assert found.id == refresh_token.id


async def test_get_valid_refresh_token_wrong_user_id_returns_none(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    other_user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id)

    assert await get_valid_refresh_token(db_session, token, user_id=other_user_id, now=NOW) is None


async def test_get_valid_refresh_token_expired_at_creation_returns_none(
    db_session: AsyncSession,
) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id, expires_at=NOW - timedelta(days=1))

    assert await get_valid_refresh_token(db_session, token, user_id=user_id, now=NOW) is None


async def test_get_valid_refresh_token_now_past_expiry_returns_none(
    db_session: AsyncSession,
) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(
        db_session, user_id=user_id, expires_at=NOW + timedelta(minutes=1)
    )

    later = NOW + timedelta(hours=1)
    assert await get_valid_refresh_token(db_session, token, user_id=user_id, now=later) is None


async def test_get_valid_refresh_token_revoked_returns_none(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id)
    await revoke_refresh_token(db_session, token, reason="logout", now=NOW)

    assert await get_valid_refresh_token(db_session, token, user_id=user_id, now=NOW) is None


async def test_get_valid_refresh_token_unknown_token_returns_none(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    assert (
        await get_valid_refresh_token(db_session, "no-such-token", user_id=user_id, now=NOW) is None
    )


async def test_get_refresh_token_still_returns_revoked_row(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    refresh_token, token, _ = await _make_token(db_session, user_id=user_id)
    await revoke_refresh_token(db_session, token, reason="logout", now=NOW)

    found = await get_refresh_token(db_session, token)
    assert found is not None
    assert found.id == refresh_token.id
    assert found.revoked is True


async def test_get_refresh_token_unknown_token_returns_none(db_session: AsyncSession) -> None:
    assert await get_refresh_token(db_session, "no-such-token") is None


async def test_revoke_refresh_token_is_idempotent(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id)

    assert await revoke_refresh_token(db_session, token, reason="logout", now=NOW) is True
    assert await revoke_refresh_token(db_session, token, reason="logout", now=NOW) is False


async def test_revoke_session_revokes_only_that_session(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    session_a = uuid.uuid4()
    session_b = uuid.uuid4()
    _, token_a, _ = await _make_token(db_session, user_id=user_id, session_id=session_a)
    _, token_b, _ = await _make_token(db_session, user_id=user_id, session_id=session_b)

    count = await revoke_session(db_session, user_id=user_id, session_id=session_a, now=NOW)

    assert count == 1
    revoked_row = await get_refresh_token(db_session, token_a)
    other_row = await get_refresh_token(db_session, token_b)
    assert revoked_row is not None and revoked_row.revoked is True
    assert other_row is not None and other_row.revoked is False


async def test_revoke_all_for_user_revokes_all_and_leaves_other_users_alone(
    db_session: AsyncSession,
) -> None:
    user_id = await _make_user(db_session)
    other_user_id = await _make_user(db_session)
    _, token_1, _ = await _make_token(db_session, user_id=user_id)
    _, token_2, _ = await _make_token(db_session, user_id=user_id)
    _, other_token, _ = await _make_token(db_session, user_id=other_user_id)

    count = await revoke_all_for_user(db_session, user_id, now=NOW)

    assert count == 2
    row_1 = await get_refresh_token(db_session, token_1)
    row_2 = await get_refresh_token(db_session, token_2)
    other_row = await get_refresh_token(db_session, other_token)
    assert row_1 is not None and row_1.revoked is True
    assert row_2 is not None and row_2.revoked is True
    assert other_row is not None and other_row.revoked is False


async def test_session_is_active_true_then_false_after_revoke(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    session_id = uuid.uuid4()
    await _make_token(db_session, user_id=user_id, session_id=session_id)

    assert (
        await session_is_active(db_session, user_id=user_id, session_id=session_id, now=NOW) is True
    )

    await revoke_session(db_session, user_id=user_id, session_id=session_id, now=NOW)

    assert (
        await session_is_active(db_session, user_id=user_id, session_id=session_id, now=NOW)
        is False
    )


async def test_session_is_active_false_when_every_row_expired(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    session_id = uuid.uuid4()
    await _make_token(
        db_session, user_id=user_id, session_id=session_id, expires_at=NOW - timedelta(days=1)
    )

    assert (
        await session_is_active(db_session, user_id=user_id, session_id=session_id, now=NOW)
        is False
    )


async def test_session_is_active_unknown_session_returns_false(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    assert (
        await session_is_active(db_session, user_id=user_id, session_id=uuid.uuid4(), now=NOW)
        is False
    )


async def test_deleting_user_cascades_to_refresh_tokens(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id)

    user = await db_session.get(User, user_id)
    assert user is not None
    await db_session.delete(user)
    await db_session.flush()

    assert await get_refresh_token(db_session, token) is None


async def test_create_refresh_token_naive_expires_at_raises_value_error(
    db_session: AsyncSession,
) -> None:
    user_id = await _make_user(db_session)
    with pytest.raises(ValueError, match="timezone-aware"):
        await create_refresh_token(
            db_session,
            token_id=uuid.uuid4(),
            user_id=user_id,
            session_id=uuid.uuid4(),
            token=uuid.uuid4().hex,
            expires_at=datetime(2026, 1, 1),  # naive
        )


# --------------------------------------------------------------------------
# revoked_reason
# --------------------------------------------------------------------------


async def test_revoke_refresh_token_stores_reason(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id)

    await revoke_refresh_token(db_session, token, reason="rotated", now=NOW)

    row = await get_refresh_token(db_session, token)
    assert row is not None
    assert row.revoked_reason == "rotated"


async def test_revoke_session_stores_reason(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    session_id = uuid.uuid4()
    _, token, _ = await _make_token(db_session, user_id=user_id, session_id=session_id)

    await revoke_session(
        db_session, user_id=user_id, session_id=session_id, reason="logout", now=NOW
    )

    row = await get_refresh_token(db_session, token)
    assert row is not None
    assert row.revoked_reason == "logout"


async def test_revoke_all_for_user_stores_reason(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id)

    await revoke_all_for_user(db_session, user_id, reason="reuse_detected", now=NOW)

    row = await get_refresh_token(db_session, token)
    assert row is not None
    assert row.revoked_reason == "reuse_detected"


async def test_already_revoked_row_keeps_original_reason(db_session: AsyncSession) -> None:
    user_id = await _make_user(db_session)
    _, token, _ = await _make_token(db_session, user_id=user_id)

    assert await revoke_refresh_token(db_session, token, reason="rotated", now=NOW) is True
    assert await revoke_refresh_token(db_session, token, reason="logout", now=NOW) is False

    row = await get_refresh_token(db_session, token)
    assert row is not None
    assert row.revoked_reason == "rotated"


async def test_revoked_reason_check_constraint_rejects_bogus_value(
    db_session: AsyncSession,
) -> None:
    user_id = await _make_user(db_session)
    refresh_token, _, _ = await _make_token(db_session, user_id=user_id)

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(RefreshToken)
                .where(RefreshToken.id == refresh_token.id)
                .values(revoked=True, revoked_reason="bogus")
            )

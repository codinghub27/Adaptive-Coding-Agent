"""Tests for `app.db.auth` (user creation/lookup) against a real Postgres."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.auth import (
    HandleTakenError,
    create_user,
    get_user,
    get_user_by_handle,
    normalize_handle,
)

pytestmark = pytest.mark.db


async def test_create_user_stores_bcrypt_hash_never_raw_password(
    db_session: AsyncSession,
) -> None:
    handle = f"user-{uuid.uuid4().hex[:12]}"
    user = await create_user(db_session, handle, "correct horse battery staple")

    assert user.password_hash is not None
    assert user.password_hash != "correct horse battery staple"
    assert user.password_hash.startswith("$2b$")


async def test_create_user_duplicate_handle_raises_and_session_still_usable(
    db_session: AsyncSession,
) -> None:
    handle = f"user-{uuid.uuid4().hex[:12]}"
    await create_user(db_session, handle, "password one")

    with pytest.raises(HandleTakenError):
        await create_user(db_session, handle, "password two")

    # The session must still be usable after the caught error.
    other_handle = f"user-{uuid.uuid4().hex[:12]}"
    other = await create_user(db_session, other_handle, "password three")
    assert other.handle == other_handle


async def test_get_user_by_handle_and_get_user_round_trip(db_session: AsyncSession) -> None:
    handle = f"user-{uuid.uuid4().hex[:12]}"
    created = await create_user(db_session, handle, "correct horse battery staple")

    by_handle = await get_user_by_handle(db_session, handle)
    assert by_handle is not None
    assert by_handle.id == created.id

    by_id = await get_user(db_session, created.id)
    assert by_id is not None
    assert by_id.handle == handle


async def test_get_user_by_handle_missing_returns_none(db_session: AsyncSession) -> None:
    assert await get_user_by_handle(db_session, "no-such-handle") is None


async def test_get_user_missing_returns_none(db_session: AsyncSession) -> None:
    assert await get_user(db_session, uuid.uuid4()) is None


@pytest.mark.parametrize(
    "handle",
    [
        "ab",  # too short
        "a" * 65,  # too long
        "has spaces",
        "has/slash",
        "",
    ],
)
async def test_create_user_invalid_handle_raises_value_error(
    db_session: AsyncSession, handle: str
) -> None:
    with pytest.raises(ValueError):
        await create_user(db_session, handle, "correct horse battery staple")


# --------------------------------------------------------------------------
# handle normalization (Fix 7)
# --------------------------------------------------------------------------


def test_normalize_handle_strips_surrounding_whitespace_only() -> None:
    assert normalize_handle("  alice  ") == "alice"
    assert normalize_handle("alice") == "alice"
    # Case is untouched -- only whitespace is normalized.
    assert normalize_handle(" Alice ") == "Alice"


async def test_create_user_strips_whitespace_and_get_user_by_handle_finds_it_either_way(
    db_session: AsyncSession,
) -> None:
    bare_handle = f"user-{uuid.uuid4().hex[:12]}"
    created = await create_user(db_session, f"  {bare_handle}  ", "correct horse battery staple")

    assert created.handle == bare_handle

    by_bare = await get_user_by_handle(db_session, bare_handle)
    by_padded = await get_user_by_handle(db_session, f" {bare_handle}")

    assert by_bare is not None and by_bare.id == created.id
    assert by_padded is not None and by_padded.id == created.id

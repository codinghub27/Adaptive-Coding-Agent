"""Fixtures for `db`-marked memory tests: an isolated, rolled-back session.

Every test gets its own `AsyncSession` joined to an outer transaction that is
always rolled back, so nothing persists across tests even though the tests
exercise a real Postgres database.
"""

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import create_engine


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """A session joined to an outer transaction that is rolled back after the test.

    Nothing committed (or flushed) by the test is ever visible outside it.
    """
    engine = create_engine(get_settings())
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            try:
                yield session
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _make_user(session: AsyncSession) -> uuid.UUID:
    user = User(handle=f"test-{uuid.uuid4().hex[:12]}")
    session.add(user)
    await session.flush()
    return user.id


@pytest_asyncio.fixture
async def user_id(db_session: AsyncSession) -> uuid.UUID:
    """A freshly created user's id, scoped to the test's rolled-back transaction."""
    return await _make_user(db_session)


@pytest_asyncio.fixture
async def other_user_id(db_session: AsyncSession) -> uuid.UUID:
    """A second freshly created user's id, distinct from `user_id`."""
    return await _make_user(db_session)


__all__ = ["db_session", "other_user_id", "user_id"]

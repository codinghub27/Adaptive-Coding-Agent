"""Async SQLAlchemy engine, session factory, and FastAPI session dependency.

The engine itself is constructed by the FastAPI lifespan (a later packet) and
stored on `app.state`; this module only provides factories and the dependency
that reads from that state.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import cast

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings

logger = logging.getLogger(__name__)


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async SQLAlchemy engine from validated settings."""
    return create_async_engine(settings.database_url, pool_pre_ping=True, echo=False)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to the given engine."""
    return async_sessionmaker(bind=engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped async session.

    Reads the session factory configured on `app.state.session_factory` by
    the lifespan and rolls back the session if the request raises.
    """
    factory = getattr(request.app.state, "session_factory", None)
    if not isinstance(factory, async_sessionmaker):
        raise RuntimeError("session_factory is not configured on app.state")
    session_factory = cast("async_sessionmaker[AsyncSession]", factory)
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping_db(engine: AsyncEngine, timeout: float = 3.0) -> bool:  # noqa: ASYNC109
    """Check database connectivity by running `SELECT 1` with a timeout.

    Returns True on success and False on any exception, including a
    timeout. Never logs or returns exception details or connection info.
    """
    try:
        async with asyncio.timeout(timeout):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("database ping failed: %s", type(exc).__name__)
        return False

"""Tests for `app.db.session`."""

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory, get_session, ping_db

MakeSettings = Callable[..., Settings]


def _make_request_missing_session_factory() -> Request:
    fake_app = SimpleNamespace(state=SimpleNamespace())
    return Request(scope={"type": "http", "app": fake_app})


async def test_create_session_factory_yields_async_session_expire_on_commit_false(
    make_settings: MakeSettings,
) -> None:
    settings = make_settings()
    engine = create_engine(settings)
    try:
        factory = create_session_factory(engine)
        assert isinstance(factory, async_sessionmaker)
        session = factory()
        try:
            assert isinstance(session, AsyncSession)
            assert session.sync_session.expire_on_commit is False
        finally:
            await session.close()
    finally:
        await engine.dispose()


async def test_get_session_raises_without_session_factory_on_app_state() -> None:
    request = _make_request_missing_session_factory()
    with pytest.raises(RuntimeError, match="session_factory"):
        async for _ in get_session(request):
            pass


async def test_ping_db_returns_false_for_unreachable_engine(
    make_settings: MakeSettings,
) -> None:
    settings = make_settings(database_url="postgresql://u:p@127.0.0.1:1/x")
    engine = create_engine(settings)
    try:
        result = await ping_db(engine, timeout=1.0)
        assert result is False
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_ping_db_true_against_real_settings() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        result = await ping_db(engine)
        assert result is True
    finally:
        await engine.dispose()

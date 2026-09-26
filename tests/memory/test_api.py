"""Tests for `POST /conversations` and `GET /profile` (`app.memory.api`).

Mirrors `tests/graph/test_chat_api.py`'s style: `create_app()` without its
lifespan, `get_session` overridden with the rolled-back `db_session` fixture,
`get_current_user` overridden with a fixed `AuthUser` built from the seeded
`user_id`/`other_user_id` fixtures (`tests/memory/conftest.py`) -- these
endpoints require a real user row (a foreign key target), so every test here
is `db`-marked.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.session import get_session
from app.main import create_app
from app.memory.profile import set_learning_preferences
from app.schemas.auth import AuthUser

pytestmark = pytest.mark.db


def _client_for(
    make_settings: Any, db_session: AsyncSession, current_user: AuthUser
) -> httpx.ASGITransport:
    app = create_app(make_settings())

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_current_user] = lambda: current_user
    return httpx.ASGITransport(app=app)


async def test_create_conversation_returns_an_id_owned_by_the_caller(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/conversations", json={"title": "two sum practice"})

    assert response.status_code == 200
    body = response.json()
    conversation_id = UUID(body["conversation_id"])
    assert body["title"] == "two sum practice"

    from app.memory.conversation import get_owned_conversation

    conversation = await get_owned_conversation(db_session, user_id, conversation_id)
    assert conversation.user_id == user_id


async def test_create_conversation_requires_authentication(
    make_settings: Any, db_session: AsyncSession
) -> None:
    from app.auth.deps import get_app_settings

    settings = make_settings()
    app = create_app(settings)

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_app_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/conversations", json={})

    assert response.status_code == 401


async def test_get_profile_returns_the_callers_profile(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    await set_learning_preferences(db_session, user_id, {"prefers_hints": True})

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["learning_preferences"] == {"prefers_hints": True}


async def test_get_profile_requires_authentication(
    make_settings: Any, db_session: AsyncSession
) -> None:
    from app.auth.deps import get_app_settings

    settings = make_settings()
    app = create_app(settings)

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_app_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/profile")

    assert response.status_code == 401


async def test_one_user_cannot_see_another_users_profile(
    make_settings: Any, db_session: AsyncSession, user_id: UUID, other_user_id: UUID
) -> None:
    await set_learning_preferences(db_session, user_id, {"prefers_hints": True})

    other_user = AuthUser(id=other_user_id, handle="other-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, other_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["learning_preferences"] == {}

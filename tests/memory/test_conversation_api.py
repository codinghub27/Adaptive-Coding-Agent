"""Tests for `GET/PATCH/DELETE /conversations*` (`app.memory.api`).

Mirrors `tests/memory/test_api.py`'s style: `create_app()` without its
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

from app.auth.deps import get_app_settings, get_current_user
from app.db.session import get_session
from app.main import create_app
from app.memory.conversation import add_turn, start_conversation
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


def _unauthenticated_transport(make_settings: Any, db_session: AsyncSession) -> httpx.ASGITransport:
    settings = make_settings()
    app = create_app(settings)

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_app_settings] = lambda: settings
    return httpx.ASGITransport(app=app)


# --------------------------------------------------------------------------
# GET /conversations
# --------------------------------------------------------------------------


async def test_list_conversations_returns_the_callers_conversations(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id, title="two sum practice")

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [str(conversation_id)]
    assert body[0]["title"] == "two sum practice"
    assert body[0]["message_count"] == 0


async def test_list_conversations_requires_authentication(
    make_settings: Any, db_session: AsyncSession
) -> None:
    transport = _unauthenticated_transport(make_settings, db_session)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations")

    assert response.status_code == 401


async def test_list_conversations_never_returns_another_users(
    make_settings: Any, db_session: AsyncSession, user_id: UUID, other_user_id: UUID
) -> None:
    await start_conversation(db_session, other_user_id)

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/conversations")

    assert response.status_code == 200
    assert response.json() == []


# --------------------------------------------------------------------------
# PATCH /conversations/{id}
# --------------------------------------------------------------------------


async def test_rename_conversation_happy_path(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id, title="old")

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/conversations/{conversation_id}", json={"title": "new title"}
        )

    assert response.status_code == 200
    assert response.json()["title"] == "new title"


async def test_rename_conversation_requires_authentication(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    transport = _unauthenticated_transport(make_settings, db_session)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/conversations/{conversation_id}", json={"title": "new title"}
        )

    assert response.status_code == 401


async def test_rename_conversation_owned_by_another_user_is_404(
    make_settings: Any, db_session: AsyncSession, user_id: UUID, other_user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, other_user_id)

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/conversations/{conversation_id}", json={"title": "hijacked"}
        )

    assert response.status_code == 404


async def test_rename_conversation_blank_title_is_422(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id, title="keep me")

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(f"/conversations/{conversation_id}", json={"title": "   "})

    assert response.status_code == 422


# --------------------------------------------------------------------------
# DELETE /conversations/{id}
# --------------------------------------------------------------------------


async def test_delete_conversation_happy_path(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 204


async def test_delete_conversation_requires_authentication(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    transport = _unauthenticated_transport(make_settings, db_session)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 401


async def test_delete_conversation_owned_by_another_user_is_404(
    make_settings: Any, db_session: AsyncSession, user_id: UUID, other_user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, other_user_id)

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 404


# --------------------------------------------------------------------------
# GET /conversations/{id}/messages
# --------------------------------------------------------------------------


async def test_get_messages_happy_path_ascending(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    await add_turn(db_session, user_id, conversation_id, "user", "first")
    await add_turn(db_session, user_id, conversation_id, "assistant", "second")

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/conversations/{conversation_id}/messages")

    assert response.status_code == 200
    body = response.json()
    assert [item["seq"] for item in body] == [1, 2]


async def test_get_messages_requires_authentication(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    transport = _unauthenticated_transport(make_settings, db_session)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/conversations/{conversation_id}/messages")

    assert response.status_code == 401


async def test_get_messages_owned_by_another_user_is_404(
    make_settings: Any, db_session: AsyncSession, user_id: UUID, other_user_id: UUID
) -> None:
    conversation_id = await start_conversation(db_session, other_user_id)

    current_user = AuthUser(id=user_id, handle="test-user", session_id=uuid4())
    transport = _client_for(make_settings, db_session, current_user)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/conversations/{conversation_id}/messages")

    assert response.status_code == 404

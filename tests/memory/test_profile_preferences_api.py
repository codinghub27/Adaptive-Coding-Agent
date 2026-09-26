"""Tests for `PATCH /profile/preferences` (`app.memory.api`).

Same harness as `tests/memory/test_conversation_api.py`: `create_app()`
without its lifespan, `get_session` overridden with the rolled-back
`db_session` fixture, and `get_current_user` overridden with a fixed
`AuthUser`. These are the learner's own declared preferences -- the teaching
planner reads them -- so they are set explicitly here, never inferred.
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
from app.memory.profile import set_learning_preferences
from app.schemas.auth import AuthUser

pytestmark = pytest.mark.db

ENDPOINT = "/profile/preferences"


def _auth_user(user_id: UUID) -> AuthUser:
    return AuthUser(id=user_id, handle="learner", session_id=uuid4())


def _client_for(
    make_settings: Any, db_session: AsyncSession, current_user: AuthUser
) -> httpx.ASGITransport:
    app = create_app(make_settings())

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_current_user] = lambda: current_user
    return httpx.ASGITransport(app=app)


async def test_setting_a_preference_persists_and_returns_the_view(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    transport = _client_for(make_settings, db_session, _auth_user(user_id))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(ENDPOINT, json={"prefers_hints": True})

    assert response.status_code == 200
    assert response.json()["learning_preferences"]["prefers_hints"] is True


async def test_omitted_flags_keep_their_stored_value(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    """A partial update must merge, not replace: the planner reads several
    flags and one card must not clear the others."""
    await set_learning_preferences(
        db_session, user_id, {"prefers_hints": True, "likes_step_by_step": True}
    )

    transport = _client_for(make_settings, db_session, _auth_user(user_id))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(ENDPOINT, json={"prefers_hints": False})

    preferences = response.json()["learning_preferences"]
    assert preferences["prefers_hints"] is False
    assert preferences["likes_step_by_step"] is True


async def test_an_empty_body_is_a_no_op_returning_the_current_profile(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    await set_learning_preferences(db_session, user_id, {"prefers_hints": True})

    transport = _client_for(make_settings, db_session, _auth_user(user_id))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(ENDPOINT, json={})

    assert response.status_code == 200
    assert response.json()["learning_preferences"]["prefers_hints"] is True


async def test_unknown_keys_are_rejected_and_never_reach_the_profile(
    make_settings: Any, db_session: AsyncSession, user_id: UUID
) -> None:
    """The flags are declared fields, not a free-form mapping, so a caller
    cannot write arbitrary keys into the profile's JSON column."""
    transport = _client_for(make_settings, db_session, _auth_user(user_id))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(ENDPOINT, json={"is_admin": True})
        assert response.status_code == 422

        after = await client.get("/profile")

    assert "is_admin" not in after.json()["learning_preferences"]


async def test_requires_authentication(make_settings: Any, db_session: AsyncSession) -> None:
    settings = make_settings()
    app = create_app(settings)

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_app_settings] = lambda: settings

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(ENDPOINT, json={"prefers_hints": True})

    assert response.status_code == 401


async def test_one_users_preferences_never_touch_another(
    make_settings: Any, db_session: AsyncSession, user_id: UUID, other_user_id: Any
) -> None:
    other = AuthUser(id=other_user_id, handle="other", session_id=uuid4())

    transport = _client_for(make_settings, db_session, _auth_user(user_id))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.patch(ENDPOINT, json={"prefers_hints": True})

    other_transport = _client_for(make_settings, db_session, other)
    async with httpx.AsyncClient(transport=other_transport, base_url="http://test") as client:
        response = await client.get("/profile")

    assert response.json()["learning_preferences"].get("prefers_hints") is not True

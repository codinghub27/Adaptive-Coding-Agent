"""Tests for `GET /auth/me`.

Builds the full app (`create_app()`, matching `tests/auth/test_protected_routes.py`)
so `/auth/register`, `/auth/login`, and `/auth/me` are all reachable on the
same client, using real issued tokens rather than a stand-in `get_current_user`.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import security as security_module
from app.auth.deps import DETAIL_TOKEN_EXPIRED
from app.config import Settings
from tests.auth.helpers import auth_headers, client_for, login, make_handle, register
from tests.input.fakes import FakeLLMClient

pytestmark = pytest.mark.db

MakeSettings = Callable[..., Settings]


async def test_me_returns_the_authenticated_users_identity(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        register_resp = await register(client, handle)
        user_id = register_resp.json()["id"]
        pair = (await login(client, handle)).json()

        response = await client.get("/auth/me", headers=auth_headers(pair["access_token"]))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user_id
    assert body["username"] == handle
    assert "created_at" in body
    assert "access_token" not in body
    assert "password" not in body


async def test_me_without_token_returns_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        response = await client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_with_expired_token_returns_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        pair = (await login(client, handle)).json()

        claims = security_module.decode_token(
            pair["access_token"], expected_type="access", settings=settings
        )
        expired = security_module.issue_access_token(
            user_id=claims.sub,
            session_id=claims.sid,
            settings=settings,
            now=datetime.now(UTC) - timedelta(minutes=46),
        )

        response = await client.get("/auth/me", headers=auth_headers(expired.token))

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_TOKEN_EXPIRED

"""Tests for `app.auth.deps.get_current_user`.

A minimal FastAPI app (not `create_app()`) with a single `GET /me` route
depending on `get_current_user` is enough to exercise every branch: no real
routes exist yet (Login/register land in a later packet). `get_session` is
overridden to yield the rolled-back `db_session` fixture and `get_app_settings`
is overridden directly, so every test here runs against a real Postgres
transaction that is never committed.
"""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import (
    DETAIL_INVALID_CREDENTIALS,
    DETAIL_NOT_AUTHENTICATED,
    DETAIL_SESSION_REVOKED,
    DETAIL_TOKEN_EXPIRED,
    get_app_settings,
    get_current_user,
)
from app.auth.security import issue_access_token, issue_refresh_token
from app.config import Settings
from app.db.auth import create_refresh_token, create_user, revoke_all_for_user, revoke_session
from app.db.models import User
from app.db.session import get_session
from app.schemas.auth import AuthUser, TokenPair

MakeSettings = Callable[..., Settings]


@asynccontextmanager
async def _client_for(
    settings: Settings, db_session: AsyncSession
) -> AsyncGenerator[httpx.AsyncClient]:
    app = FastAPI()

    @app.get("/me", response_model=AuthUser)
    async def me(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
        return user

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_app_settings] = lambda: settings

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _seed_user(db_session: AsyncSession, *, handle: str | None = None) -> User:
    return await create_user(
        db_session, handle or f"user-{uuid4().hex[:12]}", "correct horse battery staple"
    )


async def _seed_session(
    db_session: AsyncSession, settings: Settings, *, user_id: UUID, session_id: UUID | None = None
) -> tuple[UUID, str]:
    """Create a refresh-token row for a login session; return `(session_id, raw_refresh_token)`."""
    session_id = session_id if session_id is not None else uuid4()
    refresh = issue_refresh_token(user_id=user_id, session_id=session_id, settings=settings)
    await create_refresh_token(
        db_session,
        token_id=refresh.jti,
        user_id=user_id,
        session_id=session_id,
        token=refresh.token,
        expires_at=refresh.expires_at,
    )
    return session_id, refresh.token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.db
async def test_valid_token_returns_auth_user(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    user = await _seed_user(db_session)
    session_id, _ = await _seed_session(db_session, settings, user_id=user.id)
    access = issue_access_token(user_id=user.id, session_id=session_id, settings=settings)

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(access.token))

    assert response.status_code == 200
    body = response.json()
    assert body == {"id": str(user.id), "handle": user.handle, "session_id": str(session_id)}


@pytest.mark.db
async def test_missing_authorization_header_returns_not_authenticated(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me")

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_NOT_AUTHENTICATED
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.db
async def test_garbage_bearer_token_returns_invalid_credentials(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers={"Authorization": "Bearer garbage"})

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_INVALID_CREDENTIALS
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.db
async def test_expired_access_token_returns_token_expired(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    user = await _seed_user(db_session)
    session_id, _ = await _seed_session(db_session, settings, user_id=user.id)
    issued_at = datetime.now(UTC) - timedelta(minutes=46)
    access = issue_access_token(
        user_id=user.id, session_id=session_id, settings=settings, now=issued_at
    )

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(access.token))

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_TOKEN_EXPIRED
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.db
async def test_refresh_token_presented_as_access_returns_invalid_credentials(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    user = await _seed_user(db_session)
    _, refresh_token = await _seed_session(db_session, settings, user_id=user.id)

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(refresh_token))

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_INVALID_CREDENTIALS


@pytest.mark.db
async def test_token_signed_with_different_secret_returns_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    other_settings = make_settings(jwt_secret_key="z" * 32)
    user = await _seed_user(db_session)
    session_id, _ = await _seed_session(db_session, settings, user_id=user.id)
    forged = issue_access_token(user_id=user.id, session_id=session_id, settings=other_settings)

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(forged.token))

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_INVALID_CREDENTIALS


@pytest.mark.db
async def test_token_for_nonexistent_user_returns_invalid_credentials(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    access = issue_access_token(user_id=uuid4(), session_id=uuid4(), settings=settings)

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(access.token))

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_INVALID_CREDENTIALS


@pytest.mark.db
async def test_revoked_session_returns_session_revoked(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    user = await _seed_user(db_session)
    session_id, _ = await _seed_session(db_session, settings, user_id=user.id)
    await revoke_session(db_session, user_id=user.id, session_id=session_id)
    access = issue_access_token(user_id=user.id, session_id=session_id, settings=settings)

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(access.token))

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_SESSION_REVOKED


@pytest.mark.db
async def test_session_with_no_refresh_rows_returns_session_revoked(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    user = await _seed_user(db_session)
    access = issue_access_token(user_id=user.id, session_id=uuid4(), settings=settings)

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(access.token))

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_SESSION_REVOKED


@pytest.mark.db
async def test_revoke_all_for_user_returns_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    user = await _seed_user(db_session)
    session_id, _ = await _seed_session(db_session, settings, user_id=user.id)
    await revoke_all_for_user(db_session, user.id)
    access = issue_access_token(user_id=user.id, session_id=session_id, settings=settings)

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(access.token))

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_SESSION_REVOKED


@pytest.mark.db
async def test_no_401_response_body_contains_the_token(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    user = await _seed_user(db_session)
    session_id, refresh_token = await _seed_session(db_session, settings, user_id=user.id)
    access = issue_access_token(user_id=user.id, session_id=session_id, settings=settings)
    expired = issue_access_token(
        user_id=user.id,
        session_id=session_id,
        settings=settings,
        now=datetime.now(UTC) - timedelta(minutes=46),
    )
    await revoke_session(db_session, user_id=user.id, session_id=session_id)
    revoked_access = issue_access_token(user_id=user.id, session_id=session_id, settings=settings)

    tokens = [access.token, refresh_token, expired.token, revoked_access.token, "garbage"]
    async with _client_for(settings, db_session) as client:
        for presented in tokens:
            response = await client.get("/me", headers=_auth_headers(presented))
            if response.status_code == 401:
                text = response.text
                for token in tokens:
                    assert token not in text


@pytest.mark.db
async def test_get_current_user_ends_its_transaction_on_success(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """`get_current_user` commits at the end (Fix 5), so it never leaves the
    session "idle in transaction" for the rest of the route handler.
    """
    settings = make_settings()
    user = await _seed_user(db_session)
    session_id, _ = await _seed_session(db_session, settings, user_id=user.id)
    access = issue_access_token(user_id=user.id, session_id=session_id, settings=settings)
    await db_session.commit()
    assert db_session.in_transaction() is False

    async with _client_for(settings, db_session) as client:
        response = await client.get("/me", headers=_auth_headers(access.token))

    assert response.status_code == 200
    assert db_session.in_transaction() is False


def test_token_pair_repr_and_str_do_not_reveal_tokens() -> None:
    pair = TokenPair(
        access_token="super-secret-access",  # noqa: S106
        refresh_token="super-secret-refresh",  # noqa: S106
        expires_in=2700,
    )
    assert "super-secret-access" not in repr(pair)
    assert "super-secret-refresh" not in repr(pair)
    assert "super-secret-access" not in str(pair)
    assert "super-secret-refresh" not in str(pair)

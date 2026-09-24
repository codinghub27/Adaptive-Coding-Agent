"""Tests for `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`.

A minimal FastAPI app (not `create_app()`) with the auth router plus a
`GET /me` route depending on `get_current_user` is enough to exercise every
branch, including logout/reuse-detection's effect on outstanding access
tokens. `get_session` is overridden to yield the rolled-back `db_session`
fixture and `get_app_settings` is overridden directly, so every test here
runs against a real Postgres transaction that is never actually committed.
`BCRYPT_ROUNDS` is monkeypatched down for speed.
"""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.auth.routes as routes_module
import app.db.auth as db_auth_module
from app.auth import security as security_module
from app.auth.deps import DETAIL_SESSION_REVOKED, get_app_settings, get_current_user
from app.auth.routes import (
    DETAIL_LOGIN_FAILED,
    DETAIL_REFRESH_ALREADY_USED,
    DETAIL_REFRESH_EXPIRED,
    DETAIL_REFRESH_INVALID,
    DETAIL_REFRESH_REUSE,
    DETAIL_UNSUPPORTED_MEDIA_TYPE,
    DETAIL_USERNAME_TAKEN,
)
from app.auth.routes import router as auth_router
from app.auth.security import decode_token, hash_token, issue_refresh_token
from app.config import Settings
from app.db.auth import create_refresh_token, create_user, get_refresh_token
from app.db.models import RefreshToken, User
from app.db.session import get_session
from app.main import create_app
from app.schemas.auth import AuthUser

MakeSettings = Callable[..., Settings]

#: 29 UTF-8 bytes -- comfortably within the 8-72 byte password policy.
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a cheap bcrypt cost factor so password hashing in these tests is fast."""
    monkeypatch.setattr(security_module, "BCRYPT_ROUNDS", 4)


@asynccontextmanager
async def _client_for(
    settings: Settings, db_session: AsyncSession
) -> AsyncGenerator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(auth_router)

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


def _handle() -> str:
    return f"user-{uuid4().hex[:12]}"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(
    client: httpx.AsyncClient, handle: str, password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/auth/register", json={"username": handle, "password": password})


async def _login(
    client: httpx.AsyncClient, handle: str, password: str = PASSWORD
) -> httpx.Response:
    """Form-encoded login (the OAuth2 password flow shape)."""
    return await client.post("/auth/login", data={"username": handle, "password": password})


async def _login_json(
    client: httpx.AsyncClient, handle: str, password: str = PASSWORD
) -> httpx.Response:
    """JSON-body login."""
    return await client.post("/auth/login", json={"username": handle, "password": password})


# --------------------------------------------------------------------------
# register
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_register_returns_201_with_id_and_username(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        response = await _register(client, handle)

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == handle
    assert "id" in body


@pytest.mark.db
async def test_register_duplicate_username_returns_409(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        first = await _register(client, handle)
        second = await _register(client, handle)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == DETAIL_USERNAME_TAKEN


@pytest.mark.db
async def test_register_old_handle_field_name_returns_422(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """The old field name `handle` is no longer accepted -- `username` is
    required and `handle` is simply an unrecognized extra field, so this is
    a plain missing-required-field 422 (owner repro: the reverse direction).

    Only the status code here; the redacted-body variant (no password
    echoed) is covered against the full app, with its `/auth/*`
    validation-error handler, in `tests/auth/test_validation_errors.py`.
    """
    settings = make_settings()
    async with _client_for(settings, db_session) as client:
        response = await client.post(
            "/auth/register", json={"handle": _handle(), "password": PASSWORD}
        )

    assert response.status_code == 422


@pytest.mark.db
async def test_register_short_password_returns_422_and_never_echoes_it(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    short_password = "1234567"  # 7 bytes, below the 8-byte minimum
    async with _client_for(settings, db_session) as client:
        response = await _register(client, _handle(), password=short_password)

    assert response.status_code == 422
    assert short_password not in response.text


@pytest.mark.db
async def test_register_bad_handle_returns_422(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    async with _client_for(settings, db_session) as client:
        response = await _register(client, "bad handle")  # space not allowed

    assert response.status_code == 422


@pytest.mark.db
async def test_register_hashes_the_password_asynchronously(
    make_settings: MakeSettings, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_user` awaits `hash_password_async` (a worker-thread wrapper),
    not the blocking `hash_password`, so registration never blocks the event
    loop on bcrypt (Fix 4).
    """
    calls = 0
    original = db_auth_module.hash_password_async

    async def spy(password: str) -> str:
        nonlocal calls
        calls += 1
        return await original(password)

    monkeypatch.setattr(db_auth_module, "hash_password_async", spy)

    settings = make_settings()
    async with _client_for(settings, db_session) as client:
        response = await _register(client, _handle())

    assert response.status_code == 201
    assert calls == 1


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_login_returns_token_pair_with_no_store_headers(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        response = await _login(client, handle)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 2700
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"

    claims = decode_token(body["access_token"], expected_type="access", settings=settings)
    assert claims.exp - claims.iat == timedelta(minutes=45)
    assert claims.type == "access"


@pytest.mark.db
async def test_login_persists_hashed_refresh_token_without_raw_values(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        response = await _login(client, handle)

    body = response.json()
    row = (await db_session.execute(select(RefreshToken))).scalars().one()
    assert row.token_hash == hash_token(body["refresh_token"])

    user_row = (await db_session.execute(select(User).where(User.handle == handle))).scalar_one()

    for column in RefreshToken.__table__.columns:
        value = str(getattr(row, column.name))
        assert body["refresh_token"] not in value
        assert PASSWORD not in value
    for column in User.__table__.columns:
        value = str(getattr(user_row, column.name))
        assert body["refresh_token"] not in value
        assert PASSWORD not in value


@pytest.mark.db
async def test_login_wrong_password_and_unknown_handle_get_identical_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        wrong = await _login(client, handle, password="totally-wrong-password")
        unknown = await _login(client, _handle())

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json()["detail"] == DETAIL_LOGIN_FAILED
    assert unknown.json()["detail"] == DETAIL_LOGIN_FAILED
    assert wrong.headers["www-authenticate"] == "Bearer"


# --------------------------------------------------------------------------
# login: JSON body (owner repro)
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_register_and_login_json_owner_repro(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """Owner repro: `POST /auth/register` and `POST /auth/login` both accept
    a JSON body of exactly `{"username", "password"}`.
    """
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        register_resp = await _register(client, handle)
        login_resp = await _login_json(client, handle)

    assert register_resp.status_code == 201
    assert register_resp.json() == {"id": register_resp.json()["id"], "username": handle}

    assert login_resp.status_code == 200
    body = login_resp.json()
    claims = decode_token(body["access_token"], expected_type="access", settings=settings)
    assert claims.type == "access"


@pytest.mark.db
async def test_json_login_returns_same_token_pair_shape_as_form_login(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        response = await _login_json(client, handle)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 2700
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.db
async def test_json_login_wrong_password_matches_form_login_401_body(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        json_wrong = await _login_json(client, handle, password="totally-wrong-password")
        form_wrong = await _login(client, handle, password="totally-wrong-password")

    assert json_wrong.status_code == form_wrong.status_code == 401
    assert json_wrong.json() == form_wrong.json()
    assert json_wrong.json()["detail"] == DETAIL_LOGIN_FAILED


@pytest.mark.db
async def test_json_login_missing_password_returns_422(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """Only checks the status code here; the redaction of the 422 body
    (no secret echoed) is covered against the full app, with its
    `/auth/*` validation-error handler, in
    `tests/auth/test_validation_errors.py`.
    """
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        response = await client.post("/auth/login", json={"username": handle})

    assert response.status_code == 422


@pytest.mark.db
async def test_login_with_text_plain_content_type_returns_415(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        response = await client.post(
            "/auth/login",
            content=f'{{"username": "{handle}", "password": "{PASSWORD}"}}'.encode(),
            headers={"Content-Type": "text/plain"},
        )

    assert response.status_code == 415
    assert response.json()["detail"] == DETAIL_UNSUPPORTED_MEDIA_TYPE


def test_login_openapi_schema_documents_json_and_form_bodies(
    make_settings: MakeSettings,
) -> None:
    app = create_app(make_settings())
    operation = app.openapi()["paths"]["/auth/login"]["post"]
    content = operation["requestBody"]["content"]

    assert "application/json" in content
    assert "application/x-www-form-urlencoded" in content


@pytest.mark.db
async def test_login_verifies_the_password_asynchronously(
    make_settings: MakeSettings, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`login` awaits `verify_password_async` (a worker-thread wrapper), not
    the blocking `verify_password`, so login never blocks the event loop on
    bcrypt (Fix 4).
    """
    calls = 0
    original = routes_module.verify_password_async

    async def spy(password: str, password_hash: str | None) -> bool:
        nonlocal calls
        calls += 1
        return await original(password, password_hash)

    monkeypatch.setattr(routes_module, "verify_password_async", spy)

    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        response = await _login(client, handle)

    assert response.status_code == 200
    assert calls == 1


@pytest.mark.db
async def test_login_access_token_works_on_me(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        login_resp = await _login(client, handle)
        me_resp = await client.get("/me", headers=_auth_headers(login_resp.json()["access_token"]))

    assert me_resp.status_code == 200
    assert me_resp.json()["handle"] == handle


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_refresh_rotates_token_pair_and_keeps_session_id(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        old_pair = (await _login(client, handle)).json()

        refresh_resp = await client.post(
            "/auth/refresh", json={"refresh_token": old_pair["refresh_token"]}
        )
        assert refresh_resp.status_code == 200
        new_pair = refresh_resp.json()
        assert new_pair["access_token"] != old_pair["access_token"]

        me_resp = await client.get("/me", headers=_auth_headers(new_pair["access_token"]))
        assert me_resp.status_code == 200

        second_refresh = await client.post(
            "/auth/refresh", json={"refresh_token": new_pair["refresh_token"]}
        )
        assert second_refresh.status_code == 200

    old_claims = decode_token(old_pair["access_token"], expected_type="access", settings=settings)
    new_claims = decode_token(new_pair["access_token"], expected_type="access", settings=settings)
    assert old_claims.sid == new_claims.sid

    old_row = await get_refresh_token(db_session, old_pair["refresh_token"])
    assert old_row is not None
    assert old_row.revoked is True
    # Revoked via `revoke_refresh_token_by_id` (by the locked row's `id`, not
    # by re-hashing the raw token): reason is still recorded correctly.
    assert old_row.revoked_reason == "rotated"
    assert old_row.revoked_at is not None


@pytest.mark.db
async def test_refresh_with_access_token_returns_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        pair = (await _login(client, handle)).json()

        response = await client.post("/auth/refresh", json={"refresh_token": pair["access_token"]})

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_REFRESH_INVALID
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.db
async def test_refresh_with_garbage_returns_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    async with _client_for(settings, db_session) as client:
        response = await client.post("/auth/refresh", json={"refresh_token": "garbage"})

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_REFRESH_INVALID


@pytest.mark.db
async def test_refresh_expired_token_returns_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    user = await create_user(db_session, _handle(), PASSWORD)
    session_id = uuid4()
    issued_8_days_ago = datetime.now(UTC) - timedelta(days=8)
    refresh = issue_refresh_token(
        user_id=user.id, session_id=session_id, settings=settings, now=issued_8_days_ago
    )
    await create_refresh_token(
        db_session,
        token_id=refresh.jti,
        user_id=user.id,
        session_id=session_id,
        token=refresh.token,
        expires_at=refresh.expires_at,
    )
    await db_session.commit()

    async with _client_for(settings, db_session) as client:
        response = await client.post("/auth/refresh", json={"refresh_token": refresh.token})

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_REFRESH_EXPIRED


@pytest.mark.db
async def test_refresh_reuse_detection_revokes_every_session(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    # grace=0: the rotated token is presented again immediately below, which
    # is meant to exercise genuine reuse detection, not the grace-window
    # duplicate-use path (see test_refresh_reuse_within_grace_window_is_not_reuse).
    settings = make_settings(refresh_reuse_grace_seconds=0)
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        pair_a = (await _login(client, handle)).json()
        other_pair = (await _login(client, handle)).json()

        rotate_resp = await client.post(
            "/auth/refresh", json={"refresh_token": pair_a["refresh_token"]}
        )
        assert rotate_resp.status_code == 200
        pair_b = rotate_resp.json()

        reuse_resp = await client.post(
            "/auth/refresh", json={"refresh_token": pair_a["refresh_token"]}
        )
        assert reuse_resp.status_code == 401
        assert reuse_resp.json()["detail"] == DETAIL_REFRESH_REUSE

        b_again = await client.post(
            "/auth/refresh", json={"refresh_token": pair_b["refresh_token"]}
        )
        assert b_again.status_code == 401
        assert b_again.json()["detail"] == DETAIL_REFRESH_INVALID

        b_access_resp = await client.get("/me", headers=_auth_headers(pair_b["access_token"]))
        assert b_access_resp.status_code == 401
        assert b_access_resp.json()["detail"] == DETAIL_SESSION_REVOKED

        other_me = await client.get("/me", headers=_auth_headers(other_pair["access_token"]))
        assert other_me.status_code == 401
        assert other_me.json()["detail"] == DETAIL_SESSION_REVOKED

        row_a = await get_refresh_token(db_session, pair_a["refresh_token"])
        row_b = await get_refresh_token(db_session, pair_b["refresh_token"])
        assert row_a is not None and row_a.revoked_reason == "rotated"
        assert row_b is not None and row_b.revoked_reason == "reuse_detected"

        # Presenting B again after the reuse revocation is just invalid, with
        # no further side effects (no re-triggering of reuse detection).
        b_third = await client.post(
            "/auth/refresh", json={"refresh_token": pair_b["refresh_token"]}
        )
        assert b_third.status_code == 401
        assert b_third.json()["detail"] == DETAIL_REFRESH_INVALID


# --------------------------------------------------------------------------
# refresh: rotation grace window (Fix 3)
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_refresh_immediate_reuse_within_grace_window_is_not_reuse_detection(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """An immediate second use of a just-rotated token (default grace window):
    rejected as a plain "already used", with no side effects -- the successor
    pair keeps working.
    """
    settings = make_settings()  # default refresh_reuse_grace_seconds=10
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        pair = (await _login(client, handle)).json()

        rotate_resp = await client.post(
            "/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        )
        assert rotate_resp.status_code == 200
        new_pair = rotate_resp.json()

        reuse_resp = await client.post(
            "/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        )
        assert reuse_resp.status_code == 401
        assert reuse_resp.json()["detail"] == DETAIL_REFRESH_ALREADY_USED

        me_resp = await client.get("/me", headers=_auth_headers(new_pair["access_token"]))
        assert me_resp.status_code == 200


@pytest.mark.db
async def test_refresh_reuse_outside_grace_window_is_reuse_detection(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """The same scenario, but the rotation happened long enough ago (beyond
    the grace window) that presenting the rotated-away token again is
    treated as genuine reuse: every session for the user is revoked.
    """
    settings = make_settings()  # default refresh_reuse_grace_seconds=10
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        pair = (await _login(client, handle)).json()

        rotate_resp = await client.post(
            "/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        )
        assert rotate_resp.status_code == 200
        new_pair = rotate_resp.json()

        # Backdate the rotated row's `revoked_at` past the grace window, as
        # if the rotation had happened well before this reuse attempt.
        row = await get_refresh_token(db_session, pair["refresh_token"])
        assert row is not None
        row.revoked_at = datetime.now(UTC) - timedelta(seconds=100)
        await db_session.flush()

        reuse_resp = await client.post(
            "/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        )
        assert reuse_resp.status_code == 401
        assert reuse_resp.json()["detail"] == DETAIL_REFRESH_REUSE

        me_resp = await client.get("/me", headers=_auth_headers(new_pair["access_token"]))
        assert me_resp.status_code == 401
        assert me_resp.json()["detail"] == DETAIL_SESSION_REVOKED


# --------------------------------------------------------------------------
# handle normalization (Fix 7)
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_login_with_surrounding_whitespace_in_handle_succeeds(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    bare_handle = _handle()
    async with _client_for(settings, db_session) as client:
        register_resp = await _register(client, f" {bare_handle} ")
        assert register_resp.status_code == 201
        assert register_resp.json()["username"] == bare_handle

        login_with_spaces = await _login(client, f" {bare_handle}")
        login_bare = await _login(client, bare_handle)

    assert login_with_spaces.status_code == 200
    assert login_bare.status_code == 200


# --------------------------------------------------------------------------
# logout
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_logout_revokes_session_blocking_refresh_and_access(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = _handle()
    async with _client_for(settings, db_session) as client:
        await _register(client, handle)
        pair = (await _login(client, handle)).json()
        other_pair = (await _login(client, handle)).json()

        logout_resp = await client.post(
            "/auth/logout", json={"refresh_token": pair["refresh_token"]}
        )
        assert logout_resp.status_code == 204
        assert logout_resp.content == b""

        refresh_resp = await client.post(
            "/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        )
        assert refresh_resp.status_code == 401
        assert refresh_resp.json()["detail"] == DETAIL_REFRESH_INVALID

        me_resp = await client.get("/me", headers=_auth_headers(pair["access_token"]))
        assert me_resp.status_code == 401
        assert me_resp.json()["detail"] == DETAIL_SESSION_REVOKED

        other_me = await client.get("/me", headers=_auth_headers(other_pair["access_token"]))
        assert other_me.status_code == 200

        other_refresh_resp = await client.post(
            "/auth/refresh", json={"refresh_token": other_pair["refresh_token"]}
        )
        assert other_refresh_resp.status_code == 200

        again = await client.post("/auth/logout", json={"refresh_token": pair["refresh_token"]})
        assert again.status_code == 204

        garbage_logout = await client.post("/auth/logout", json={"refresh_token": "garbage-token"})
        assert garbage_logout.status_code == 204


# --------------------------------------------------------------------------
# app wiring
# --------------------------------------------------------------------------


def test_create_app_registers_auth_routes(make_settings: MakeSettings) -> None:
    app = create_app(make_settings())
    paths = set(app.openapi()["paths"])

    assert "/auth/login" in paths
    assert "/auth/register" in paths
    assert "/auth/refresh" in paths
    assert "/auth/logout" in paths
    assert "/chat" in paths
    assert "/understand" in paths

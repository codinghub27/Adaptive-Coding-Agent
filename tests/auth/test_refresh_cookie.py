"""Tests for the httpOnly refresh-token cookie (D8,
`docs/features/UI-integration.md`): `login`/`refresh` set it, `refresh`/
`logout` accept it as a fallback for the JSON body (body wins when both are
present), and `logout` always clears it.

Uses the full app (`create_app()`, matching `tests/auth/helpers.client_for`)
so `httpx.AsyncClient`'s cookie jar carries the `Set-Cookie` from `login`
through to subsequent `/auth/refresh`/`/auth/logout` calls automatically.
"""

from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import security as security_module
from app.auth.routes import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from app.config import Settings
from tests.auth.helpers import client_for, login, make_handle, register
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security_module, "BCRYPT_ROUNDS", 4)


@pytest.mark.parametrize("secure", [False, True])
async def test_login_sets_httponly_strict_refresh_cookie(
    make_settings: MakeSettings, db_session: AsyncSession, secure: bool
) -> None:
    settings = make_settings(refresh_cookie_secure=secure)
    handle = make_handle()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        await register(client, handle)
        response = await login(client, handle)

    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{REFRESH_COOKIE_NAME}=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=strict" in set_cookie.lower()
    assert f"path={REFRESH_COOKIE_PATH}" in set_cookie.lower()
    assert ("secure" in set_cookie.lower()) is secure
    assert response.json()["refresh_token"] in set_cookie


async def test_refresh_works_with_only_the_cookie(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        await register(client, handle)
        await login(client, handle)

        response = await client.post("/auth/refresh")

    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_refresh_works_with_only_a_body_regression(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """The pre-cookie contract: a bare JSON body with no cookie must still work."""
    settings = make_settings()
    handle = make_handle()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        await register(client, handle)
        pair = (await login(client, handle)).json()
        # Drop the cookie the login response set, to prove the body alone works.
        client.cookies.clear()

        response = await client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})

    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_refresh_body_wins_when_body_and_cookie_disagree(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        await register(client, handle)
        pair_a = (await login(client, handle)).json()
        # A second login gives a different session's refresh token and
        # overwrites the cookie with it.
        await login(client, handle)

        # The cookie now carries the second login's token; send pair_a's in the body.
        response = await client.post(
            "/auth/refresh", json={"refresh_token": pair_a["refresh_token"]}
        )

    assert response.status_code == 200
    old_claims = security_module.decode_token(
        pair_a["access_token"], expected_type="access", settings=settings
    )
    new_claims = security_module.decode_token(
        response.json()["access_token"], expected_type="access", settings=settings
    )
    # The rotated pair keeps pair_a's session id, proving the body (not the
    # cookie's pair_b session) was the one actually used.
    assert old_claims.sid == new_claims.sid


async def test_refresh_with_neither_body_nor_cookie_returns_the_same_422_as_today(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        response = await client.post("/auth/refresh")

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error == {"loc": ["body", "refresh_token"], "msg": "Field required", "type": "missing"}


async def test_logout_with_only_cookie_returns_204_and_clears_it(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        await register(client, handle)
        await login(client, handle)

        response = await client.post("/auth/logout")

    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{REFRESH_COOKIE_NAME}=" in set_cookie
    # A cleared cookie carries an immediate expiry / max-age=0.
    assert "max-age=0" in set_cookie.lower()


async def test_logout_clears_cookie_even_for_an_unknown_token(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        response = await client.post("/auth/logout", json={"refresh_token": "unknown-token"})

    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "")
    assert f"{REFRESH_COOKIE_NAME}=" in set_cookie
    assert "max-age=0" in set_cookie.lower()


async def test_refresh_rotated_cookie_value_changes(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        await register(client, handle)
        login_resp = await login(client, handle)
        login_cookie = login_resp.headers["set-cookie"]

        refresh_resp = await client.post("/auth/refresh")

    assert refresh_resp.status_code == 200
    refresh_cookie = refresh_resp.headers["set-cookie"]
    assert login_cookie != refresh_cookie
    assert refresh_resp.json()["refresh_token"] in refresh_cookie
    assert refresh_resp.json()["refresh_token"] not in login_cookie

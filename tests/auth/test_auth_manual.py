"""Owner's Step-4 manual test cases (`docs/features/AUTH-jwt.md`), end-to-end.

Each test drives the real HTTP surface (`/auth/register`, `/auth/login`,
`/auth/refresh`, `/auth/logout`, `/chat`) against the full app with real,
persisted tokens -- nothing here stubs `get_current_user` or fabricates a
`TokenClaims` by hand except where the manual test case explicitly calls for
a fabricated (expired/stale) token. Every test prints one `ACTUAL: ...` line
summarising what was observed, so `pytest -s` output can be pasted verbatim
into the feature doc's `Manual Test Cases` section.

Shares its app/client setup (`create_app()`, `get_session` -> the rolled-back
`db_session`, `get_llm` -> `FakeLLMClient`, cheap bcrypt, real
`/auth/register` + `/auth/login`) with `tests/auth/test_protected_routes.py`
via `tests.auth.helpers`.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import DETAIL_NOT_AUTHENTICATED, DETAIL_SESSION_REVOKED, DETAIL_TOKEN_EXPIRED
from app.auth.security import decode_token, hash_token, issue_access_token, issue_refresh_token
from app.config import Settings
from app.db.auth import create_refresh_token, get_refresh_token
from tests.auth.helpers import auth_headers, client_for, login, make_handle, register
from tests.input.fakes import FakeLLMClient

pytestmark = pytest.mark.db

MakeSettings = Callable[..., Settings]

#: A python code block + a bare traceback line -- Phase 2's rule-based intent
#: classifier routes this straight to CODE_DEBUG with zero LLM calls, so the
#: fake LLM's canned content is never actually parsed.
_DEBUG_TEXT: Final = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)

DETAIL_REFRESH_EXPIRED: Final = "refresh token expired"
DETAIL_REFRESH_INVALID: Final = "invalid refresh token"
DETAIL_REFRESH_REUSE: Final = "refresh token reuse detected"


# --------------------------------------------------------------------------
# AUTH-P9 owner repro -- JSON register/login with the public `username` field
# --------------------------------------------------------------------------


async def test_owner_repro_json_register_and_login_with_username(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """AUTH-P9 owner repro, end-to-end: `POST /auth/register` and
    `POST /auth/login` both accept `{"username", "password"}` as JSON, and
    the resulting access token works on `/chat`.

    Uses a fresh generated username (`make_handle`), not the owner's literal
    `"sravan12"`: that value already exists as a real row in the project's
    dev database (created 2026-09-24, presumably from the owner's own manual
    reproduction of this bug before filing it), so registering it here would
    just get a 409 rather than exercising the fix. The request/response
    shapes below are otherwise identical to the owner's repro.
    """
    settings = make_settings()
    fake = FakeLLMClient()
    username = make_handle()
    password = "sravan123"  # noqa: S105
    async with client_for(settings, db_session, fake) as client:
        register_resp = await client.post(
            "/auth/register", json={"username": username, "password": password}
        )
        login_resp = await client.post(
            "/auth/login", json={"username": username, "password": password}
        )
        access_token = login_resp.json()["access_token"]
        chat_resp = await client.post(
            "/chat", data={"text": _DEBUG_TEXT}, headers=auth_headers(access_token)
        )

    assert register_resp.status_code == 201
    register_body = register_resp.json()
    assert register_body == {"id": register_body["id"], "username": username}

    assert login_resp.status_code == 200
    login_body = login_resp.json()
    claims = decode_token(login_body["access_token"], expected_type="access", settings=settings)
    assert claims.type == "access"

    assert chat_resp.status_code == 200

    print(
        "ACTUAL: "
        f"register status={register_resp.status_code} body={register_body} | "
        f"login status={login_resp.status_code} token_type={login_body['token_type']} "
        f"access claims.type={claims.type} | "
        f"chat status={chat_resp.status_code} route={chat_resp.json()['route']}"
    )


# --------------------------------------------------------------------------
# Manual Test 1 -- valid login
# --------------------------------------------------------------------------


async def test_manual_1_valid_login(make_settings: MakeSettings, db_session: AsyncSession) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        response = await login(client, handle)

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 2700
    assert response.headers["cache-control"] == "no-store"

    access_claims = decode_token(body["access_token"], expected_type="access", settings=settings)
    refresh_claims = decode_token(body["refresh_token"], expected_type="refresh", settings=settings)

    access_lifetime = access_claims.exp - access_claims.iat
    refresh_lifetime = refresh_claims.exp - refresh_claims.iat
    assert access_lifetime == timedelta(minutes=45)
    assert refresh_lifetime == timedelta(days=7)

    remaining = access_claims.exp - datetime.now(UTC)
    assert timedelta(minutes=44, seconds=50) <= remaining <= timedelta(minutes=45)

    sid_equal = access_claims.sid == refresh_claims.sid
    assert sid_equal

    row = await get_refresh_token(db_session, body["refresh_token"])
    assert row is not None
    assert row.token_hash == hash_token(body["refresh_token"])
    assert row.revoked is False

    print(
        "ACTUAL: "
        f"status={response.status_code} token_type={body['token_type']} "
        f"expires_in={body['expires_in']} "
        f"access exp-iat (min)={access_lifetime.total_seconds() / 60} "
        f"refresh exp-iat (days)={refresh_lifetime.days} "
        f"sid equal={sid_equal} "
        f"db row: token_hash matches={row.token_hash == hash_token(body['refresh_token'])} "
        f"revoked={row.revoked}"
    )


# --------------------------------------------------------------------------
# Manual Test 2 -- protected route: valid / expired / missing token
# --------------------------------------------------------------------------


async def test_manual_2_protected_route(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        pair = (await login(client, handle)).json()

        valid_resp = await client.post(
            "/chat", data={"text": _DEBUG_TEXT}, headers=auth_headers(pair["access_token"])
        )

        claims = decode_token(pair["access_token"], expected_type="access", settings=settings)
        expired = issue_access_token(
            user_id=claims.sub,
            session_id=claims.sid,
            settings=settings,
            now=datetime.now(UTC) - timedelta(minutes=46),
        )
        expired_resp = await client.post(
            "/chat", data={"text": _DEBUG_TEXT}, headers=auth_headers(expired.token)
        )

        missing_resp = await client.post("/chat", data={"text": _DEBUG_TEXT})

    assert valid_resp.status_code == 200
    valid_route = valid_resp.json()["route"]

    assert expired_resp.status_code == 401
    assert expired_resp.json()["detail"] == DETAIL_TOKEN_EXPIRED

    assert missing_resp.status_code == 401
    assert missing_resp.json()["detail"] == DETAIL_NOT_AUTHENTICATED
    assert missing_resp.headers["www-authenticate"] == "Bearer"

    print(
        "ACTUAL: "
        f"valid status={valid_resp.status_code} route={valid_route} | "
        f"expired status={expired_resp.status_code} detail={expired_resp.json()['detail']} | "
        f"missing status={missing_resp.status_code} detail={missing_resp.json()['detail']} "
        f"www-authenticate={missing_resp.headers['www-authenticate']}"
    )


# --------------------------------------------------------------------------
# Manual Test 3 -- refresh: rotation, reuse detection, expiry
# --------------------------------------------------------------------------


async def test_manual_3_refresh(make_settings: MakeSettings, db_session: AsyncSession) -> None:
    # grace=0: this test means true reuse (an attacker replaying an
    # already-rotated token), not a benign duplicate presentation within the
    # rotation grace window, so the grace window is disabled here.
    settings = make_settings(refresh_reuse_grace_seconds=0)
    handle = make_handle()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        pair = (await login(client, handle)).json()

        old_refresh_token = pair["refresh_token"]
        rotate_resp = await client.post("/auth/refresh", json={"refresh_token": old_refresh_token})
        new_pair = rotate_resp.json()

        new_chat_resp = await client.post(
            "/chat", data={"text": _DEBUG_TEXT}, headers=auth_headers(new_pair["access_token"])
        )

        # The now-rotated OLD refresh token, presented again: reuse detection.
        reuse_resp = await client.post("/auth/refresh", json={"refresh_token": old_refresh_token})

        # Side effect: reuse detection revokes every session for the user,
        # including the one that was just rotated into `new_pair`.
        post_reuse_chat_resp = await client.post(
            "/chat", data={"text": _DEBUG_TEXT}, headers=auth_headers(new_pair["access_token"])
        )

        # Separately, on a fresh login: an expired refresh token.
        handle2 = make_handle()
        await register(client, handle2)
        login2 = (await login(client, handle2)).json()
        claims2 = decode_token(login2["access_token"], expected_type="access", settings=settings)
        expired_session_id = uuid4()
        expired_refresh = issue_refresh_token(
            user_id=claims2.sub,
            session_id=expired_session_id,
            settings=settings,
            now=datetime.now(UTC) - timedelta(days=8),
        )
        await create_refresh_token(
            db_session,
            token_id=expired_refresh.jti,
            user_id=claims2.sub,
            session_id=expired_session_id,
            token=expired_refresh.token,
            expires_at=expired_refresh.expires_at,
        )
        expired_resp = await client.post(
            "/auth/refresh", json={"refresh_token": expired_refresh.token}
        )

    assert rotate_resp.status_code == 200
    assert new_pair["access_token"] != pair["access_token"]
    assert new_chat_resp.status_code == 200

    assert reuse_resp.status_code == 401
    assert reuse_resp.json()["detail"] == DETAIL_REFRESH_REUSE

    assert post_reuse_chat_resp.status_code == 401
    assert post_reuse_chat_resp.json()["detail"] == DETAIL_SESSION_REVOKED

    assert expired_resp.status_code == 401
    assert expired_resp.json()["detail"] == DETAIL_REFRESH_EXPIRED

    access_differs = new_pair["access_token"] != pair["access_token"]
    reuse_detail = reuse_resp.json()["detail"]
    post_reuse_detail = post_reuse_chat_resp.json()["detail"]
    expired_detail = expired_resp.json()["detail"]
    print(
        "ACTUAL: "
        f"(refresh_reuse_grace_seconds=0, so the reuse below is genuine reuse "
        f"detection, not the grace-window duplicate-use path) "
        f"rotate status={rotate_resp.status_code} new access differs={access_differs} "
        f"new access /chat status={new_chat_resp.status_code} | "
        f"reuse status={reuse_resp.status_code} detail={reuse_detail} "
        f"(revoked all of the user's sessions -- rotated session's /chat now "
        f"status={post_reuse_chat_resp.status_code} detail={post_reuse_detail}) | "
        f"fresh-login expired-refresh status={expired_resp.status_code} detail={expired_detail}"
    )


# --------------------------------------------------------------------------
# Manual Test 4 -- logout
# --------------------------------------------------------------------------


async def test_manual_4_logout(make_settings: MakeSettings, db_session: AsyncSession) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        pair = (await login(client, handle)).json()

        refresh_token = pair["refresh_token"]
        logout_resp = await client.post("/auth/logout", json={"refresh_token": refresh_token})

        refresh_after_logout_resp = await client.post(
            "/auth/refresh", json={"refresh_token": refresh_token}
        )

        chat_after_logout_resp = await client.post(
            "/chat", data={"text": _DEBUG_TEXT}, headers=auth_headers(pair["access_token"])
        )

        # A second, independent login session for the same user.
        pair2 = (await login(client, handle)).json()
        chat_second_session_resp = await client.post(
            "/chat", data={"text": _DEBUG_TEXT}, headers=auth_headers(pair2["access_token"])
        )

    assert logout_resp.status_code == 204

    assert refresh_after_logout_resp.status_code == 401
    assert refresh_after_logout_resp.json()["detail"] == DETAIL_REFRESH_INVALID

    assert chat_after_logout_resp.status_code == 401
    assert chat_after_logout_resp.json()["detail"] == DETAIL_SESSION_REVOKED

    assert chat_second_session_resp.status_code == 200

    refresh_after_logout_detail = refresh_after_logout_resp.json()["detail"]
    chat_after_logout_detail = chat_after_logout_resp.json()["detail"]
    second_session_status = chat_second_session_resp.status_code
    print(
        "ACTUAL: "
        f"logout status={logout_resp.status_code} | "
        f"refresh-with-logged-out-token status={refresh_after_logout_resp.status_code} "
        f"detail={refresh_after_logout_detail} (plain 401, no reuse side effect) | "
        f"/chat with revoked session status={chat_after_logout_resp.status_code} "
        f"detail={chat_after_logout_detail} | "
        f"/chat with a second, independent login session status={second_session_status}"
    )

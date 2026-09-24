"""End-to-end tests that `/chat` and `/understand` are gated by real bearer tokens.

Builds the *full* app (`create_app()`, not a bare `FastAPI()` like
`tests/auth/test_routes.py`) so `/chat`, `/understand`, `/auth/*`, and
`/health` are all reachable in the same client. `get_session` is overridden
to yield the rolled-back `db_session` fixture, `get_llm` with a
`FakeLLMClient`, and `get_app_settings` with the test `Settings` directly
(the lifespan that would normally put these on `app.state` is never run).
`get_current_user` itself is never overridden here -- every token used is a
real one issued by `/auth/register` + `/auth/login`, so these tests exercise
the actual auth dependency, not a stand-in.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import security as security_module
from app.auth.deps import DETAIL_SESSION_REVOKED, DETAIL_TOKEN_EXPIRED
from app.config import Settings
from app.main import create_app
from app.memory.conversation import get_recent_context, start_conversation
from app.memory.profile import ensure_profile, set_learning_preferences
from tests.auth.helpers import auth_headers, client_for, login, make_handle, register
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]

_SLIDING_WINDOW_DEBUG_TEXT: Final = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range in my sliding window attempt\n"
)


async def _set_skill_level(session: AsyncSession, user_id: UUID, skills: dict[str, float]) -> None:
    """Directly seed `skill_levels` on the profile row (mirrors `tests/graph/test_chat_api.py`)."""
    profile = await ensure_profile(session, user_id, for_update=True)
    profile.skill_levels = dict(skills)
    await session.flush()


# --------------------------------------------------------------------------
# /chat: valid token runs the graph as that user
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_chat_with_valid_bearer_runs_as_the_token_user(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient(chat_content="unused")
    async with client_for(settings, db_session, fake) as client:
        register_resp = await register(client, handle)
        user_id = UUID(register_resp.json()["id"])
        await set_learning_preferences(db_session, user_id, {"prefers_hints": True})
        await _set_skill_level(db_session, user_id, {"sliding_window": 0.3})

        pair = (await login(client, handle)).json()
        response = await client.post(
            "/chat",
            data={"text": _SLIDING_WINDOW_DEBUG_TEXT},
            headers=auth_headers(pair["access_token"]),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "debug"
    assert body["plan"]["assistance_level"] == "hint"
    assert body["plan"]["difficulty"] == "easy"
    assert body["plan"]["topic"] == "sliding_window"


# --------------------------------------------------------------------------
# /chat: missing / expired / wrong-type tokens
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_chat_without_token_returns_401_with_bearer_challenge(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        response = await client.post("/chat", data={"text": "why does this fail?"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.db
async def test_chat_with_expired_access_token_returns_401(
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

        response = await client.post(
            "/chat",
            data={"text": "why does this fail?"},
            headers=auth_headers(expired.token),
        )

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_TOKEN_EXPIRED


@pytest.mark.db
async def test_chat_with_refresh_token_as_bearer_returns_401(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        pair = (await login(client, handle)).json()

        response = await client.post(
            "/chat",
            data={"text": "why does this fail?"},
            headers=auth_headers(pair["refresh_token"]),
        )

    assert response.status_code == 401


# --------------------------------------------------------------------------
# /chat: logout revokes the session's access tokens immediately
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_chat_after_logout_returns_401_session_revoked(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient()
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        pair = (await login(client, handle)).json()

        logout_resp = await client.post(
            "/auth/logout", json={"refresh_token": pair["refresh_token"]}
        )
        assert logout_resp.status_code == 204

        response = await client.post(
            "/chat",
            data={"text": "why does this fail?"},
            headers=auth_headers(pair["access_token"]),
        )

    assert response.status_code == 401
    assert response.json()["detail"] == DETAIL_SESSION_REVOKED


# --------------------------------------------------------------------------
# /chat: a caller-supplied `user_id` form field is inert
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_chat_ignores_a_user_id_form_field_for_another_user(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle_a, handle_b = make_handle(), make_handle()
    fake = FakeLLMClient(chat_content="unused")
    async with client_for(settings, db_session, fake) as client:
        user_a = UUID((await register(client, handle_a)).json()["id"])
        user_b = UUID((await register(client, handle_b)).json()["id"])

        # A is a weak-skill, hint-preferring learner; B is the opposite --
        # if the plan reflected B's profile instead of A's (the token
        # user's), these assertions would fail.
        await set_learning_preferences(db_session, user_a, {"prefers_hints": True})
        await _set_skill_level(db_session, user_a, {"sliding_window": 0.3})
        await _set_skill_level(db_session, user_b, {"sliding_window": 0.95})

        pair_a = (await login(client, handle_a)).json()

        response = await client.post(
            "/chat",
            data={"text": _SLIDING_WINDOW_DEBUG_TEXT, "user_id": str(user_b)},
            headers=auth_headers(pair_a["access_token"]),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["assistance_level"] == "hint"
    assert body["plan"]["difficulty"] == "easy"
    assert body["plan"]["topic"] == "sliding_window"


# --------------------------------------------------------------------------
# /chat: a conversation_id owned by another user never leaks their turns
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_chat_with_conversation_owned_by_another_user_does_not_leak(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle_a, handle_b = make_handle(), make_handle()
    fake = FakeLLMClient(chat_content="unused")
    async with client_for(settings, db_session, fake) as client:
        _user_a = UUID((await register(client, handle_a)).json()["id"])
        user_b = UUID((await register(client, handle_b)).json()["id"])

        owner_conversation_id = await start_conversation(db_session, user_b)

        pair_a = (await login(client, handle_a)).json()

        response = await client.post(
            "/chat",
            data={
                "text": "why does this fail?",
                "conversation_id": str(owner_conversation_id),
            },
            headers=auth_headers(pair_a["access_token"]),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["errors"]
    assert any(
        err["node"] in {"load_learner_profile", "update_learner_model"} for err in body["errors"]
    )

    turns = await get_recent_context(db_session, user_b, owner_conversation_id)
    assert turns == []


# --------------------------------------------------------------------------
# /understand: same gate
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_understand_requires_a_valid_token(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient(chat_content="unused")
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        pair = (await login(client, handle)).json()

        # Deterministic rule-based intent (code + traceback): no LLM call
        # needed, so the fake's canned content is never actually parsed.
        unauthenticated = await client.post(
            "/understand", data={"text": _SLIDING_WINDOW_DEBUG_TEXT}
        )
        authenticated = await client.post(
            "/understand",
            data={"text": _SLIDING_WINDOW_DEBUG_TEXT},
            headers=auth_headers(pair["access_token"]),
        )

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200


# --------------------------------------------------------------------------
# /health: stays public
# --------------------------------------------------------------------------


async def test_health_without_token_is_not_401(make_settings: MakeSettings) -> None:
    settings = make_settings()
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code != 401

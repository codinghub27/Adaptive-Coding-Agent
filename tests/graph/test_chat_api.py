"""Tests for `POST /chat` (`app.graph.api`).

Mirrors `tests/input/test_understand_api.py`'s style: the app is built via
`create_app()` without running its lifespan, `get_llm` is overridden with a
`FakeLLMClient`, and `get_session` is overridden so every non-`db` test stays
fully offline.
"""

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.session import get_session
from app.input.api import get_llm
from app.input.vision import MAX_IMAGE_BYTES
from app.main import create_app
from app.memory.conversation import get_recent_context, start_conversation
from app.memory.profile import ensure_profile, get_profile, set_learning_preferences
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]

_DEBUG_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)

_SLIDING_WINDOW_DEBUG_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range in my sliding window attempt\n"
)


class _StubSession:
    """A minimal stand-in for `AsyncSession`: only `commit()` is ever called.

    `load_learner_profile`/`update_learner_model` never touch the session
    when `user_id` is `None` (see `app.graph.nodes`), so the non-`db` tests
    below -- which never pass a `user_id` -- can use this instead of a real
    database connection.
    """

    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


async def _stub_session() -> AsyncIterator[Any]:
    yield _StubSession()


@asynccontextmanager
async def _client_for(
    make_settings: MakeSettings,
    fake: FakeLLMClient,
    session_override: Callable[..., AsyncIterator[Any]] = _stub_session,
) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app(make_settings())
    app.dependency_overrides[get_llm] = lambda: fake
    app.dependency_overrides[get_session] = session_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# --------------------------------------------------------------------------
# non-DB: debug route, deterministic rule-based intent, zero LLM calls
# --------------------------------------------------------------------------


async def test_code_and_indexerror_returns_debug_route_with_zero_llm_calls(
    make_settings: MakeSettings,
) -> None:
    fake = FakeLLMClient(chat_content="unused")
    async with _client_for(make_settings, fake) as client:
        response = await client.post("/chat", data={"text": _DEBUG_TEXT})

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "debug"
    assert body["plan"]["assistance_level"] is not None
    assert body["response"].startswith("[debug stub]")
    assert body["llm_calls"] == 0
    # No topic could be inferred (empty profile, no topic hint), so
    # `update_learner_model` never builds a learning event for this turn.
    assert body["events"] == []
    assert body["events_persisted"] == []
    assert body["errors"] == []
    assert fake.chat_calls == []


# --------------------------------------------------------------------------
# non-DB: ambiguous text + an unusable LLM response -> clarify
# --------------------------------------------------------------------------


async def test_ambiguous_text_with_unusable_llm_output_routes_to_clarify(
    make_settings: MakeSettings,
) -> None:
    fake = FakeLLMClient(chat_content="not valid json at all")
    async with _client_for(make_settings, fake) as client:
        response = await client.post("/chat", data={"text": "hmm, not sure"})

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "clarify"


# --------------------------------------------------------------------------
# validation failures
# --------------------------------------------------------------------------


async def test_no_text_and_no_image_returns_422(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        response = await client.post("/chat", data={})

    assert response.status_code == 422


async def test_blank_text_returns_422(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        response = await client.post("/chat", data={"text": "   "})

    assert response.status_code == 422


async def test_oversize_image_returns_413(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_IMAGE_BYTES + 1)
        files = {"image": ("big.png", oversized, "image/png")}
        response = await client.post("/chat", files=files)

    assert response.status_code == 413
    assert fake.vision_calls == []


async def test_conversation_id_without_user_id_returns_422(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        response = await client.post(
            "/chat",
            data={"text": "why does this fail?", "conversation_id": str(uuid4())},
        )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# db: full turn with a real profile + conversation
# --------------------------------------------------------------------------


async def _set_skill_level(session: AsyncSession, user_id: UUID, skills: dict[str, float]) -> None:
    """Directly seed `skill_levels` on the user's profile row.

    Mirrors the pattern `app.memory.profile.set_learning_preferences`/
    `set_language` use for their own fields: `ensure_profile` then a direct
    field assignment. There's no dedicated setter for `skill_levels` (it's
    normally a derived projection over learning events -- see
    `app.memory.profile`'s module docstring), so this is the same shape,
    applied to the one remaining field, only for test setup.
    """
    profile = await ensure_profile(session, user_id, for_update=True)
    profile.skill_levels = dict(skills)
    await session.flush()


@pytest.mark.db
async def test_debug_turn_persists_conversation_and_learning_event(
    make_settings: MakeSettings, db_session: AsyncSession, user_id: UUID
) -> None:
    await set_learning_preferences(db_session, user_id, {"prefers_hints": True})
    await _set_skill_level(db_session, user_id, {"sliding_window": 0.3})
    conversation_id = await start_conversation(db_session, user_id)

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    fake = FakeLLMClient(chat_content="unused")
    async with _client_for(make_settings, fake, session_override) as client:
        response = await client.post(
            "/chat",
            data={
                "text": _SLIDING_WINDOW_DEBUG_TEXT,
                "user_id": str(user_id),
                "conversation_id": str(conversation_id),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "debug"
    assert body["plan"]["assistance_level"] == "hint"
    assert body["plan"]["difficulty"] == "easy"
    assert body["plan"]["topic"] == "sliding_window"
    assert len(body["events"]) == 1
    assert body["events_persisted"] == []

    turns = await get_recent_context(db_session, user_id, conversation_id)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[1].role == "assistant"

    profile = await get_profile(db_session, user_id)
    assert profile.skill_levels == {"sliding_window": 0.3}

"""ADAPTIVE-upgrade P1: active-problem storage and the fixed ladder ceiling, on Postgres."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.conversation import (
    ConversationNotFoundError,
    get_active_problem,
    set_active_problem,
    start_conversation,
)
from app.memory.hint_progress import get_hint_progress, save_hint_progress
from app.schemas.agent_results import HintLevel
from app.schemas.input import ActiveProblem, StructuredInput

pytestmark = pytest.mark.db


def _active(topic: str | None = "trees") -> ActiveProblem:
    return ActiveProblem(
        problem=StructuredInput(source="text", problem="Return the max path sum.", question="q"),
        key="_pabc123",
        topic=topic,
    )


async def test_active_problem_round_trips(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    assert await get_active_problem(db_session, user_id, conversation_id) is None

    await set_active_problem(db_session, user_id, conversation_id, _active())
    stored = await get_active_problem(db_session, user_id, conversation_id)
    assert stored == _active()


async def test_active_problem_is_ownership_scoped(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    await set_active_problem(db_session, user_id, conversation_id, _active())
    stranger = uuid.uuid4()
    assert await get_active_problem(db_session, stranger, conversation_id) is None
    with pytest.raises(ConversationNotFoundError):
        await set_active_problem(db_session, stranger, conversation_id, _active())


async def test_ladder_ceiling_is_fixed_at_insert(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    await save_hint_progress(
        db_session, user_id, conversation_id, "_pkey", level=0, solved=False, ceiling=3
    )
    await save_hint_progress(
        db_session, user_id, conversation_id, "_pkey", level=1, solved=False, ceiling=2
    )
    progress = await get_hint_progress(db_session, user_id, conversation_id, "_pkey")
    assert progress.last_level == HintLevel.L1_WHAT_TO_TRACK
    assert progress.ceiling == HintLevel.L3_CONCRETE_IDEA

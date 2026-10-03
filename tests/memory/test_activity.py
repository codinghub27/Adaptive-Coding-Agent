"""The learning-streak source: every active hour, across every conversation.

Needs a reachable, migrated Postgres (`pytest.mark.db`).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.memory.conversation import add_turn, learning_activity_hours, start_conversation

pytestmark = pytest.mark.db


async def test_every_active_day_counts_even_in_one_conversation(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    # One conversation used on three different days: the old sidebar logic
    # (one day per conversation) reported a streak of 1 for this learner.
    conversation_id = await start_conversation(db_session, user_id)
    now = datetime.now(UTC)
    for days_ago in (2, 1, 0):
        turn = await add_turn(db_session, user_id, conversation_id, "user", f"day -{days_ago}")
        await add_turn(db_session, user_id, conversation_id, "assistant", "reply")
        await db_session.execute(
            update(Message)
            .where(Message.id == turn.id)
            .values(created_at=now - timedelta(days=days_ago))
        )
    await db_session.flush()

    hours = await learning_activity_hours(db_session, user_id)

    days = {hour.date() for hour in hours}
    assert {(now - timedelta(days=d)).date() for d in (0, 1, 2)} <= days
    assert len(hours) == 3  # assistant messages are not learner activity


async def test_other_learners_activity_is_not_counted(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    assert await learning_activity_hours(db_session, uuid.uuid4()) == []

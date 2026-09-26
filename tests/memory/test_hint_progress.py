"""DB-backed tests for `app.memory.hint_progress`.

Needs a reachable, migrated Postgres (`pytest.mark.db`).
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.hint_engine import HintProgress
from app.db.models import HintProgress as HintProgressRow
from app.memory.conversation import start_conversation
from app.memory.hint_progress import get_hint_progress, get_latest_hint_progress, save_hint_progress
from app.schemas.agent_results import HintLevel

pytestmark = pytest.mark.db


async def test_round_trip_save_then_read(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=3, solved=False)

    progress = await get_hint_progress(db_session, user_id, conversation_id, "arrays")

    assert progress.last_level == HintLevel.L3_CONCRETE_IDEA
    assert progress.solved is False


async def test_save_twice_upserts_in_place(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=0, solved=False)
    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=2, solved=False)

    stmt = select(HintProgressRow).where(
        HintProgressRow.user_id == user_id,
        HintProgressRow.conversation_id == conversation_id,
        HintProgressRow.topic == "arrays",
    )
    rows = (await db_session.execute(stmt)).scalars().all()

    assert len(rows) == 1
    assert rows[0].level == 2

    progress = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    assert progress.last_level == HintLevel.L2_DATA_STRUCTURE


async def test_different_conversation_gets_its_own_row(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_a = await start_conversation(db_session, user_id)
    conversation_b = await start_conversation(db_session, user_id)

    await save_hint_progress(db_session, user_id, conversation_a, "arrays", level=4, solved=False)

    progress_a = await get_hint_progress(db_session, user_id, conversation_a, "arrays")
    progress_b = await get_hint_progress(db_session, user_id, conversation_b, "arrays")

    assert progress_a.last_level == HintLevel.L4_PSEUDOCODE
    assert progress_b == HintProgress()


async def test_different_topic_gets_its_own_row(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=5, solved=False)

    progress_arrays = await get_hint_progress(db_session, user_id, conversation_id, "arrays")
    progress_graphs = await get_hint_progress(db_session, user_id, conversation_id, "graphs")

    assert progress_arrays.last_level == HintLevel.L5_PARTIAL
    assert progress_graphs == HintProgress()


async def test_no_row_returns_fresh_progress(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    progress = await get_hint_progress(db_session, user_id, conversation_id, "arrays")

    assert progress == HintProgress()


async def test_get_latest_returns_none_when_no_rows(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    latest = await get_latest_hint_progress(db_session, user_id, conversation_id)

    assert latest is None


async def _backdate(
    session: AsyncSession, conversation_id: uuid.UUID, topic: str, *, minutes: int
) -> None:
    """Push `topic`'s `updated_at` back by `minutes` via a raw `UPDATE`.

    `func.now()` is transaction-scoped (`transaction_timestamp()` in
    Postgres), so within one test's single wrapped transaction (see
    `tests/memory/conftest.py`'s `db_session`), two ordinary
    `save_hint_progress` calls would otherwise tie on `updated_at` -- this
    makes the intended ordering unambiguous, the way it naturally would be
    across two separate real requests.
    """
    await session.execute(
        sa_update(HintProgressRow)
        .where(
            HintProgressRow.conversation_id == conversation_id,
            HintProgressRow.topic == topic,
        )
        .values(updated_at=func.now() - timedelta(minutes=minutes))
    )


async def test_get_latest_returns_the_most_recently_updated_topic(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=0, solved=False)
    await _backdate(db_session, conversation_id, "arrays", minutes=5)
    await save_hint_progress(db_session, user_id, conversation_id, "graphs", level=1, solved=False)

    latest = await get_latest_hint_progress(db_session, user_id, conversation_id)

    assert latest is not None
    assert latest.topic == "graphs"
    assert latest.level == 1


async def test_get_latest_moves_when_an_older_topic_is_updated_again(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=0, solved=False)
    await save_hint_progress(db_session, user_id, conversation_id, "graphs", level=0, solved=False)
    await _backdate(db_session, conversation_id, "graphs", minutes=5)
    # "arrays" is upserted again -- its `updated_at` is bumped by
    # `save_hint_progress`'s explicit `SET updated_at = func.now()`, so it
    # becomes the latest again even though it was created first.
    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=1, solved=False)

    latest = await get_latest_hint_progress(db_session, user_id, conversation_id)

    assert latest is not None
    assert latest.topic == "arrays"
    assert latest.level == 1


async def test_get_latest_scoped_by_user(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=2, solved=False)

    latest_for_other_user = await get_latest_hint_progress(
        db_session, other_user_id, conversation_id
    )

    assert latest_for_other_user is None

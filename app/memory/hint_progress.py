"""Hint-ladder progress store: per-conversation-topic hint-ladder state.

This is conversation *state* (how far the hint ladder has been climbed for a
`(user, conversation, topic)` triple), not evidence about the learner -- it is
deliberately **not** stored as a `LearningEvent` (see `app.graph.nodes`'s
`resolve_hint_progress` docstring for the rationale). A row is upserted in
place on every write; there is at most one row per `(user_id,
conversation_id, topic)`.

Neither function in this module commits the session -- callers own the
transaction and must `await session.commit()` (or roll back) themselves.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.hint_engine import HintProgress
from app.db.models import HintProgress as HintProgressRow
from app.schemas.agent_results import HintLevel

__all__ = ["get_hint_progress", "get_latest_hint_progress", "save_hint_progress"]


async def get_hint_progress(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, topic: str
) -> HintProgress:
    """Return the stored hint-ladder progress for this `(user, conversation, topic)`.

    Returns a fresh `HintProgress()` (level unset, not solved) if no row exists.
    """
    stmt = select(HintProgressRow).where(
        HintProgressRow.user_id == user_id,
        HintProgressRow.conversation_id == conversation_id,
        HintProgressRow.topic == topic,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return HintProgress()
    return HintProgress(last_level=HintLevel(row.level), solved=row.solved)


async def get_latest_hint_progress(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> HintProgressRow | None:
    """Return this `(user, conversation)`'s most recently updated hint-ladder row.

    Ownership-scoped to `user_id` exactly like `get_hint_progress`, but across
    *all* topics for this conversation -- used to resolve which ladder a bare
    follow-up turn (no topic, no problem statement of its own) should
    continue climbing. Returns `None` (never raises) if no row exists for
    this pair; the caller decides the fallback.
    """
    stmt = (
        select(HintProgressRow)
        .where(
            HintProgressRow.user_id == user_id,
            HintProgressRow.conversation_id == conversation_id,
        )
        .order_by(HintProgressRow.updated_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def save_hint_progress(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    topic: str,
    level: int,
    solved: bool,
) -> None:
    """Upsert this `(user, conversation, topic)`'s hint-ladder progress.

    Inserts a new row, or updates `level`/`solved` (and `updated_at`) in
    place if a row already exists for the same `(user_id, conversation_id,
    topic)` -- there is never more than one row per triple.

    `updated_at` is set explicitly to `func.now()` in the `SET` clause below:
    the column's `onupdate=func.now()` (see `app.db.models.hint_progress`) is
    a Core/ORM `Update`-statement default and is **not** applied by this
    Postgres-specific `INSERT ... ON CONFLICT DO UPDATE` construct, so without
    this, re-upserting an existing row would silently leave `updated_at`
    stuck at its original insert time -- breaking `get_latest_hint_progress`,
    which depends on `updated_at` actually advancing on every touch.
    """
    insert_stmt = pg_insert(HintProgressRow).values(
        user_id=user_id,
        conversation_id=conversation_id,
        topic=topic,
        level=level,
        solved=solved,
    )
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[
            HintProgressRow.user_id,
            HintProgressRow.conversation_id,
            HintProgressRow.topic,
        ],
        set_={
            "level": insert_stmt.excluded.level,
            "solved": insert_stmt.excluded.solved,
            "updated_at": func.now(),
        },
    )
    await session.execute(upsert_stmt)

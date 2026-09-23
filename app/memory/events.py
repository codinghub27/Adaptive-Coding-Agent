"""Learning event store: the append-only log that drives the learner profile.

Recording an event is idempotent on `event.event_id` (client-supplied): a
resubmission of the same id is a no-op against the profile projection
(`applied=False`) and simply returns the already-stored event.

None of the functions in this module commit the session — callers own the
transaction and must `await session.commit()` (or roll back) themselves.
"""

import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LearningEvent
from app.memory.conversation import get_owned_conversation
from app.memory.profile import apply_event, ensure_profile, to_view
from app.schemas.event import (
    Difficulty,
    LearningEventCreate,
    LearningEventView,
    RecordEventResult,
    RequestedHelp,
)
from app.schemas.intent import Intent
from app.schemas.profile import LearnerProfileView

__all__ = [
    "INTENT_TO_HELP",
    "list_events",
    "rebuild_profile",
    "record_event",
    "requested_help_for",
]

INTENT_TO_HELP: Final[Mapping[Intent, RequestedHelp]] = MappingProxyType(
    {
        Intent.DSA_HINT: "hint",
        Intent.DSA_SOLVE: "solution",
        Intent.CODE_DEBUG: "debug",
        Intent.ERROR_EXPLANATION: "debug",
        Intent.CODE_EXPLAIN: "explanation",
        Intent.CONCEPT_EXPLANATION: "explanation",
        Intent.IMAGE_CODE_ANALYSIS: "explanation",
        Intent.CODE_REVIEW: "review",
        Intent.OPTIMIZATION: "review",
        Intent.TEST_CASE_ANALYSIS: "test_analysis",
        Intent.APPROACH_DISCUSSION: "approach",
    }
)


def requested_help_for(intent: Intent) -> RequestedHelp:
    """The `RequestedHelp` category implied by an intent."""
    return INTENT_TO_HELP[intent]


def _row_to_create(row: LearningEvent) -> LearningEventCreate:
    """Rebuild the input schema from a stored row, for profile replay."""
    return LearningEventCreate(
        event_id=row.id,
        conversation_id=row.conversation_id,
        intent=Intent(row.intent) if row.intent is not None else None,
        problem=row.problem,
        topic=row.topic,
        pattern=row.pattern,
        difficulty=cast("Difficulty | None", row.difficulty),
        requested_help=cast("RequestedHelp | None", row.requested_help),
        hints_used=row.hints_used,
        needed_full_solution=row.needed_full_solution,
        errors=row.errors,
        solved=row.solved,
        time_spent=row.time_spent,
        concepts=row.concepts,
    )


async def record_event(
    session: AsyncSession, user_id: uuid.UUID, event: LearningEventCreate
) -> RecordEventResult:
    """Record a learning event and fold it into the learner profile.

    Idempotent on `event.event_id`: if an event with that id already exists
    for this user, the stored event is returned unchanged with `applied=False`
    and the profile is left untouched.

    The profile row lock is acquired *before* the event insert, not after.
    `apply_event`'s EWMA update is order-dependent, so the order in which
    concurrent events are folded into the profile must match their `seq`
    (insertion) order. Locking after the insert would let two concurrent
    calls insert in one order but then race for the profile lock in the
    opposite order, silently corrupting the projection. Locking first forces
    the profile update itself to serialize in insert order.
    """
    if event.conversation_id is not None:
        await get_owned_conversation(session, user_id, event.conversation_id)

    profile = await ensure_profile(session, user_id, for_update=True)

    requested_help = event.requested_help or (
        requested_help_for(event.intent) if event.intent else None
    )

    insert_stmt = (
        pg_insert(LearningEvent)
        .values(
            id=event.event_id,
            user_id=user_id,
            conversation_id=event.conversation_id,
            intent=event.intent.value if event.intent is not None else None,
            problem=event.problem,
            topic=event.topic,
            pattern=event.pattern,
            difficulty=event.difficulty,
            requested_help=requested_help,
            hints_used=event.hints_used,
            needed_full_solution=event.needed_full_solution,
            errors=event.errors,
            solved=event.solved,
            time_spent=event.time_spent,
            concepts=event.concepts,
        )
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(LearningEvent.id)
    )
    inserted_id = (await session.execute(insert_stmt)).scalar_one_or_none()

    if inserted_id is None:
        existing_stmt = select(LearningEvent).where(
            LearningEvent.id == event.event_id, LearningEvent.user_id == user_id
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is None:
            raise ValueError("event id already used")
        view = LearningEventView.model_validate(existing)
        return RecordEventResult(event=view, applied=False)

    skills, errors = apply_event(profile.skill_levels, profile.common_errors, event)
    profile.skill_levels = skills
    profile.common_errors = errors
    await session.flush()

    load_stmt = (
        select(LearningEvent)
        .where(LearningEvent.id == inserted_id)
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(load_stmt)).scalar_one()
    view = LearningEventView.model_validate(row)
    return RecordEventResult(event=view, applied=True)


async def rebuild_profile(session: AsyncSession, user_id: uuid.UUID) -> LearnerProfileView:
    """Recompute `skill_levels` and `common_errors` from the full event log.

    Leaves `learning_preferences` and `language` untouched.
    """
    profile = await ensure_profile(session, user_id, for_update=True)

    stmt = (
        select(LearningEvent)
        .where(LearningEvent.user_id == user_id)
        .order_by(LearningEvent.seq.asc())
    )
    rows = (await session.execute(stmt)).scalars()

    skills: dict[str, float] = {}
    errors: dict[str, int] = {}
    for row in rows:
        skills, errors = apply_event(skills, errors, _row_to_create(row))

    profile.skill_levels = skills
    profile.common_errors = errors
    await session.flush()
    return to_view(profile)


async def list_events(
    session: AsyncSession, user_id: uuid.UUID, limit: int = 50
) -> list[LearningEventView]:
    """Return the user's most recent events, newest first.

    Raises `ValueError` if `limit < 1`.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    stmt = (
        select(LearningEvent)
        .where(LearningEvent.user_id == user_id)
        .order_by(LearningEvent.seq.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars()
    return [LearningEventView.model_validate(row) for row in rows]

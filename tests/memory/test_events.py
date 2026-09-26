"""DB-backed tests for `app.memory.events`.

Needs a reachable, migrated Postgres (`pytest.mark.db`); no Qdrant required.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, LearnerProfile, LearningEvent
from app.memory.conversation import ConversationNotFoundError, start_conversation
from app.memory.events import list_events, rebuild_profile, record_event
from app.memory.profile import ensure_profile, get_profile, set_learning_preferences
from app.schemas.event import LearningEventCreate
from app.schemas.intent import Intent

pytestmark = pytest.mark.db


async def test_manual_1_solved_sliding_window_increases_skill_and_is_idempotent(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    event = LearningEventCreate(
        topic="arrays",
        pattern="sliding_window",
        solved=True,
        hints_used=0,
    )

    result = await record_event(db_session, user_id, event)
    print(f"\n[Manual 1] first record: applied={result.applied}")
    print(f"[Manual 1] event id={result.event.id}")

    assert result.applied is True
    assert result.event.topic == "arrays"

    events_after_first = await list_events(db_session, user_id)
    assert len(events_after_first) == 1
    assert events_after_first[0].id == result.event.id

    profile = await get_profile(db_session, user_id)
    print(f"[Manual 1] skill_levels after first record: {profile.skill_levels}")
    assert profile.skill_levels == {"arrays": 0.6, "sliding_window": 0.6}

    # Resubmit the exact same event object.
    result2 = await record_event(db_session, user_id, event)
    print(f"[Manual 1] second record (resubmit): applied={result2.applied}")

    assert result2.applied is False
    assert result2.event.id == result.event.id

    profile_after_second = await get_profile(db_session, user_id)
    print(f"[Manual 1] skill_levels after resubmit: {profile_after_second.skill_levels}")
    assert profile_after_second.skill_levels == {"arrays": 0.6, "sliding_window": 0.6}

    events_after_second = await list_events(db_session, user_id)
    assert len(events_after_second) == 1


async def test_intent_only_event_stores_derived_requested_help(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    event = LearningEventCreate(
        topic="dp",
        solved=False,
        intent=Intent.DSA_HINT,
        requested_help=None,
    )
    result = await record_event(db_session, user_id, event)
    assert result.event.requested_help == "hint"


async def test_errors_accumulate_into_common_errors(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    events = [
        LearningEventCreate(topic="arrays", solved=False, errors=["off_by_one"]),
        LearningEventCreate(topic="arrays", solved=False, errors=["off_by_one"]),
        LearningEventCreate(topic="arrays", solved=False, errors=["edge_cases"]),
    ]
    for event in events:
        await record_event(db_session, user_id, event)

    profile = await get_profile(db_session, user_id)
    assert profile.common_errors == ["off_by_one", "edge_cases"]


async def test_rebuild_profile_reproduces_incremental_projection_and_preserves_preferences(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    await set_learning_preferences(db_session, user_id, {"prefers_hints": True})

    events = [
        LearningEventCreate(topic="arrays", pattern="sliding_window", solved=True, hints_used=0),
        LearningEventCreate(topic="dp", solved=False, errors=["off_by_one"]),
        LearningEventCreate(topic="arrays", pattern="sliding_window", solved=True, hints_used=3),
        LearningEventCreate(
            topic="dp", solved=True, needed_full_solution=True, errors=["edge_cases"]
        ),
    ]
    for event in events:
        await record_event(db_session, user_id, event)

    incremental = await get_profile(db_session, user_id)

    # Corrupt the projection.
    profile_row = await ensure_profile(db_session, user_id, for_update=True)
    profile_row.skill_levels = {"x": 0.9}
    await db_session.flush()

    corrupted = await get_profile(db_session, user_id)
    assert corrupted.skill_levels == {"x": 0.9}

    rebuilt = await rebuild_profile(db_session, user_id)

    assert rebuilt.skill_levels == incremental.skill_levels
    assert rebuilt.common_errors == incremental.common_errors
    assert rebuilt.learning_preferences == {"prefers_hints": True}


async def test_rebuild_profile_mixed_outcomes_matches_incremental(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    """A stream mixing unobserved (`solved=None`), solved, and unsolved
    events must rebuild to exactly the same profile the incremental path
    produced -- `rebuild_profile` replays through the same `apply_event`."""
    events = [
        LearningEventCreate(topic="arrays", solved=None),
        LearningEventCreate(topic="arrays", solved=True, hints_used=0),
        LearningEventCreate(topic="dp", solved=False, errors=["off_by_one"]),
        LearningEventCreate(topic="dp", solved=None),
    ]
    for event in events:
        await record_event(db_session, user_id, event)

    incremental = await get_profile(db_session, user_id)
    assert incremental.skill_levels["arrays"] > 0.5
    assert incremental.skill_levels["dp"] == pytest.approx(0.42)

    # Corrupt the projection so a real rebuild is exercised.
    profile_row = await ensure_profile(db_session, user_id, for_update=True)
    profile_row.skill_levels = {"x": 0.9}
    await db_session.flush()

    rebuilt = await rebuild_profile(db_session, user_id)

    assert rebuilt.skill_levels == incremental.skill_levels
    assert rebuilt.common_errors == incremental.common_errors


async def test_user_isolation_events_and_profile(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    event = LearningEventCreate(topic="arrays", solved=True)
    result = await record_event(db_session, user_id, event)

    other_events = await list_events(db_session, other_user_id)
    assert other_events == []

    other_profile = await get_profile(db_session, other_user_id)
    assert other_profile.skill_levels == {}

    # Reusing the same event_id as a different user must be rejected.
    reused = LearningEventCreate(event_id=result.event.id, topic="dp", solved=True)
    with pytest.raises(ValueError, match="event id already used"):
        await record_event(db_session, other_user_id, reused)


async def test_record_event_with_conversation_owned_by_another_user_raises(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    event = LearningEventCreate(topic="arrays", solved=True, conversation_id=conversation_id)

    with pytest.raises(ConversationNotFoundError):
        await record_event(db_session, other_user_id, event)


async def test_get_profile_with_no_row_returns_empty_view_and_creates_no_row(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    profile = await get_profile(db_session, user_id)

    assert profile.language is None
    assert profile.skill_levels == {}
    assert profile.learning_preferences == {}
    assert profile.common_errors == []

    rows = (
        (await db_session.execute(select(LearnerProfile).where(LearnerProfile.user_id == user_id)))
        .scalars()
        .all()
    )
    assert rows == []


async def test_list_events_limit_zero_raises_value_error(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await list_events(db_session, user_id, limit=0)


async def test_composite_fk_rejects_cross_user_conversation_id(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    """DB-level enforcement: bypass the store layer and insert the ORM row
    directly with a conversation owned by a different user."""
    conversation_id = await start_conversation(db_session, user_id)
    await db_session.flush()

    bad_event = LearningEvent(
        id=uuid.uuid4(),
        user_id=other_user_id,
        conversation_id=conversation_id,
        topic="arrays",
        solved=True,
        hints_used=0,
        needed_full_solution=False,
        errors=[],
        concepts=[],
    )
    async with db_session.begin_nested():
        db_session.add(bad_event)
        with pytest.raises(IntegrityError):
            await db_session.flush()


async def test_deleting_conversation_nulls_event_conversation_id(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    event = LearningEventCreate(topic="arrays", solved=True, conversation_id=conversation_id)
    result = await record_event(db_session, user_id, event)

    conversation = (
        await db_session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one()
    await db_session.delete(conversation)
    await db_session.flush()

    row = (
        await db_session.execute(
            select(LearningEvent)
            .where(LearningEvent.id == result.event.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert row.conversation_id is None
    assert row.user_id == user_id

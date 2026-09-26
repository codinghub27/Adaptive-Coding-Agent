"""DB-backed tests for the list/rename/delete/list-messages additions to
`app.memory.conversation`.

Needs a reachable, migrated Postgres (`pytest.mark.db`); no Qdrant required.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HintProgress as HintProgressRow
from app.db.models import Message
from app.memory.conversation import (
    ConversationNotFoundError,
    add_turn,
    delete_conversation,
    get_owned_conversation,
    list_conversations,
    list_messages,
    rename_conversation,
    start_conversation,
)
from app.memory.hint_progress import save_hint_progress

pytestmark = pytest.mark.db


# --------------------------------------------------------------------------
# list_conversations
# --------------------------------------------------------------------------


async def test_list_conversations_orders_by_last_activity(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    older_active = await start_conversation(db_session, user_id, title="older but active")
    newer_idle = await start_conversation(db_session, user_id, title="newer but idle")

    # `newer_idle`'s `created_at` should outrank `older_active`'s by wall-clock
    # time, but `older_active` gets a message afterward, so it should sort
    # first (most recent activity). Postgres's `now()` (the `created_at`
    # server default) is frozen to this transaction's start time, so every
    # row inserted here would otherwise get an identical timestamp -- the
    # message's `created_at` is forced forward explicitly to make the
    # ordering deterministic instead of relying on wall-clock time.
    message = await add_turn(db_session, user_id, older_active, "user", "hello again")
    message_row = (
        await db_session.execute(select(Message).where(Message.id == message.id))
    ).scalar_one()
    message_row.created_at = datetime.now(UTC) + timedelta(hours=1)
    await db_session.flush()

    summaries = await list_conversations(db_session, user_id)

    ids_in_order = [summary.id for summary in summaries]
    assert ids_in_order.index(older_active) < ids_in_order.index(newer_idle)


async def test_list_conversations_reports_message_count(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    await add_turn(db_session, user_id, conversation_id, "user", "one")
    await add_turn(db_session, user_id, conversation_id, "assistant", "two")

    summaries = await list_conversations(db_session, user_id)

    summary = next(s for s in summaries if s.id == conversation_id)
    assert summary.message_count == 2


async def test_list_conversations_updated_at_falls_back_to_created_at(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    conversation = await get_owned_conversation(db_session, user_id, conversation_id)

    summaries = await list_conversations(db_session, user_id)

    summary = next(s for s in summaries if s.id == conversation_id)
    assert summary.message_count == 0
    assert summary.updated_at == conversation.created_at


async def test_list_conversations_limit_zero_raises_value_error(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await list_conversations(db_session, user_id, limit=0)


async def test_list_conversations_only_returns_the_caller_own(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    mine = await start_conversation(db_session, user_id)
    await start_conversation(db_session, other_user_id)

    summaries = await list_conversations(db_session, user_id)

    assert [s.id for s in summaries] == [mine]


# --------------------------------------------------------------------------
# rename_conversation
# --------------------------------------------------------------------------


async def test_rename_conversation_happy_path(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    conversation_id = await start_conversation(db_session, user_id, title="old title")

    summary = await rename_conversation(db_session, user_id, conversation_id, "  new title  ")

    assert summary.title == "new title"
    conversation = await get_owned_conversation(db_session, user_id, conversation_id)
    assert conversation.title == "new title"


async def test_rename_conversation_not_owned_raises(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, other_user_id)

    with pytest.raises(ConversationNotFoundError):
        await rename_conversation(db_session, user_id, conversation_id, "hijacked")


async def test_rename_conversation_blank_title_raises_value_error(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id, title="keep me")

    with pytest.raises(ValueError, match="blank"):
        await rename_conversation(db_session, user_id, conversation_id, "   ")


async def test_rename_conversation_too_long_raises_value_error(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    with pytest.raises(ValueError, match="200"):
        await rename_conversation(db_session, user_id, conversation_id, "x" * 201)


# --------------------------------------------------------------------------
# delete_conversation
# --------------------------------------------------------------------------


async def test_delete_conversation_happy_path_removes_messages_and_hint_progress(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    await add_turn(db_session, user_id, conversation_id, "user", "hello")
    await save_hint_progress(db_session, user_id, conversation_id, "arrays", level=1, solved=False)

    await delete_conversation(db_session, user_id, conversation_id)

    with pytest.raises(ConversationNotFoundError):
        await get_owned_conversation(db_session, user_id, conversation_id)

    messages = (
        (
            await db_session.execute(
                select(Message).where(Message.conversation_id == conversation_id)
            )
        )
        .scalars()
        .all()
    )
    assert messages == []

    hint_rows = (
        (
            await db_session.execute(
                select(HintProgressRow).where(HintProgressRow.conversation_id == conversation_id)
            )
        )
        .scalars()
        .all()
    )
    assert hint_rows == []


async def test_delete_conversation_not_owned_raises(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, other_user_id)

    with pytest.raises(ConversationNotFoundError):
        await delete_conversation(db_session, user_id, conversation_id)

    # Untouched: still owned by, and readable by, the real owner.
    conversation = await get_owned_conversation(db_session, other_user_id, conversation_id)
    assert conversation.id == conversation_id


# --------------------------------------------------------------------------
# list_messages
# --------------------------------------------------------------------------


async def test_list_messages_ordered_ascending(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    for i in range(4):
        role = "user" if i % 2 == 0 else "assistant"
        await add_turn(db_session, user_id, conversation_id, role, f"turn {i + 1}")

    messages = await list_messages(db_session, user_id, conversation_id)

    assert [m.seq for m in messages] == [1, 2, 3, 4]


async def test_list_messages_after_seq_filters(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    for i in range(4):
        role = "user" if i % 2 == 0 else "assistant"
        await add_turn(db_session, user_id, conversation_id, role, f"turn {i + 1}")

    messages = await list_messages(db_session, user_id, conversation_id, after_seq=2)

    assert [m.seq for m in messages] == [3, 4]


async def test_list_messages_respects_limit(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    for i in range(4):
        role = "user" if i % 2 == 0 else "assistant"
        await add_turn(db_session, user_id, conversation_id, role, f"turn {i + 1}")

    messages = await list_messages(db_session, user_id, conversation_id, limit=2)

    assert [m.seq for m in messages] == [1, 2]


async def test_list_messages_limit_zero_raises_value_error(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    with pytest.raises(ValueError, match="limit"):
        await list_messages(db_session, user_id, conversation_id, limit=0)


async def test_list_messages_not_owned_raises(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, other_user_id)
    await add_turn(db_session, other_user_id, conversation_id, "user", "secret")

    with pytest.raises(ConversationNotFoundError):
        await list_messages(db_session, user_id, conversation_id)


# --------------------------------------------------------------------------
# cross-user isolation
# --------------------------------------------------------------------------


async def test_second_user_cannot_see_or_touch_first_users_conversation(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id, title="private")

    summaries = await list_conversations(db_session, other_user_id)
    assert conversation_id not in {s.id for s in summaries}

    with pytest.raises(ConversationNotFoundError):
        await rename_conversation(db_session, other_user_id, conversation_id, "stolen")
    with pytest.raises(ConversationNotFoundError):
        await delete_conversation(db_session, other_user_id, conversation_id)
    with pytest.raises(ConversationNotFoundError):
        await list_messages(db_session, other_user_id, conversation_id)

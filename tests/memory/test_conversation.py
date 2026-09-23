"""DB-backed tests for `app.memory.conversation`.

Needs a reachable, migrated Postgres (`pytest.mark.db`); no Qdrant required.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.conversation import (
    DEFAULT_CONTEXT_WINDOW,
    ConversationNotFoundError,
    add_turn,
    get_owned_conversation,
    get_recent_context,
    start_conversation,
)
from app.schemas.intent import Intent

pytestmark = pytest.mark.db


async def test_manual_2_two_turns_returned_in_order(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)

    user_turn = await add_turn(
        db_session,
        user_id,
        conversation_id,
        "user",
        "How do I find the max sum subarray of size k?",
    )
    assistant_turn = await add_turn(
        db_session,
        user_id,
        conversation_id,
        "assistant",
        "What happens to the window sum when you slide one step?",
    )
    print(
        f"\n[Manual 2] user turn: seq={user_turn.seq} role={user_turn.role!r} "
        f"content={user_turn.content!r}"
    )
    print(
        f"[Manual 2] assistant turn: seq={assistant_turn.seq} role={assistant_turn.role!r} "
        f"content={assistant_turn.content!r}"
    )

    context = await get_recent_context(db_session, user_id, conversation_id)
    print(f"[Manual 2] get_recent_context returned {len(context)} messages:")
    for message in context:
        print(f"[Manual 2]   seq={message.seq} role={message.role!r} content={message.content!r}")

    assert len(context) == 2
    assert context[0].seq == 1
    assert context[0].role == "user"
    assert context[0].content == "How do I find the max sum subarray of size k?"
    assert context[1].seq == 2
    assert context[1].role == "assistant"
    assert context[1].content == "What happens to the window sum when you slide one step?"


async def test_context_window_limits_to_last_n_in_order(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    for i in range(5):
        role = "user" if i % 2 == 0 else "assistant"
        await add_turn(db_session, user_id, conversation_id, role, f"turn {i + 1}")

    context = await get_recent_context(db_session, user_id, conversation_id, limit=3)

    assert [message.seq for message in context] == [3, 4, 5]


async def test_default_context_window_is_module_constant(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    total_turns = DEFAULT_CONTEXT_WINDOW + 5
    for i in range(total_turns):
        role = "user" if i % 2 == 0 else "assistant"
        await add_turn(db_session, user_id, conversation_id, role, f"turn {i + 1}")

    context = await get_recent_context(db_session, user_id, conversation_id)

    assert len(context) == DEFAULT_CONTEXT_WINDOW
    assert context[0].seq == total_turns - DEFAULT_CONTEXT_WINDOW + 1
    assert context[-1].seq == total_turns


async def test_get_recent_context_limit_zero_raises_value_error(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    with pytest.raises(ValueError, match="limit"):
        await get_recent_context(db_session, user_id, conversation_id, limit=0)


async def test_other_user_cannot_read_or_write_conversation(
    db_session: AsyncSession, user_id: uuid.UUID, other_user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    await add_turn(db_session, user_id, conversation_id, "user", "hello")

    with pytest.raises(ConversationNotFoundError):
        await get_recent_context(db_session, other_user_id, conversation_id)

    with pytest.raises(ConversationNotFoundError):
        await add_turn(db_session, other_user_id, conversation_id, "user", "intrude")


async def test_start_conversation_title_over_200_chars_raises(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    with pytest.raises(ValueError, match="200"):
        await start_conversation(db_session, user_id, title="x" * 201)


async def test_start_conversation_blank_title_stored_as_none(
    db_session: AsyncSession, user_id: uuid.UUID
) -> None:
    conversation_id = await start_conversation(db_session, user_id, title="   ")
    conversation = await get_owned_conversation(db_session, user_id, conversation_id)
    assert conversation.title is None


async def test_intent_round_trips_on_add_turn(db_session: AsyncSession, user_id: uuid.UUID) -> None:
    conversation_id = await start_conversation(db_session, user_id)
    message = await add_turn(
        db_session,
        user_id,
        conversation_id,
        "user",
        "hint me please",
        intent=Intent.DSA_HINT,
    )
    assert message.intent == Intent.DSA_HINT

    context = await get_recent_context(db_session, user_id, conversation_id)
    assert context[0].intent == Intent.DSA_HINT

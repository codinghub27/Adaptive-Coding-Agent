"""Conversation memory store: threads of messages owned by a user.

Every function takes `session` and `user_id` explicitly and filters all reads
and writes by `user_id`, so a caller can never read or mutate another user's
conversation. None of the functions in this module commit the session —
callers own the transaction and must `await session.commit()` (or roll back)
themselves.

`content` (message text) is **untrusted user/assistant data**: it is stored
and returned verbatim, never truncated, and never interpreted as instructions.
"""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, HintProgress, Message
from app.schemas.conversation import ConversationSummary, MessageView, Role
from app.schemas.intent import Intent

__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "ConversationNotFoundError",
    "add_turn",
    "delete_conversation",
    "get_owned_conversation",
    "get_recent_context",
    "list_conversations",
    "list_messages",
    "rename_conversation",
    "start_conversation",
]

DEFAULT_CONTEXT_WINDOW: Final = 10


class ConversationNotFoundError(LookupError):
    """Raised when a conversation is missing or not owned by the given user."""


async def start_conversation(
    session: AsyncSession, user_id: uuid.UUID, title: str | None = None
) -> uuid.UUID:
    """Create a new conversation for `user_id` and return its id.

    `title` is stripped; a blank or all-whitespace title is stored as `None`.
    Raises `ValueError` if the stripped title exceeds 200 characters.
    """
    if title is not None:
        title = title.strip() or None
    if title is not None and len(title) > 200:
        raise ValueError("title must be at most 200 characters")

    conversation = Conversation(user_id=user_id, title=title)
    session.add(conversation)
    await session.flush()
    return conversation.id


async def get_owned_conversation(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Conversation:
    """Return the conversation if it exists and is owned by `user_id`.

    Raises `ConversationNotFoundError` otherwise (missing or not owned).
    """
    stmt = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise ConversationNotFoundError(f"conversation {conversation_id} not found")
    return conversation


async def add_turn(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: Role,
    content: str,
    intent: Intent | None = None,
) -> MessageView:
    """Append a message to a conversation, assigning the next sequence number.

    Locks the conversation row to serialize `seq` assignment against
    concurrent turns on the same conversation.
    """
    await get_owned_conversation(session, user_id, conversation_id, for_update=True)

    next_seq_stmt = select(func.coalesce(func.max(Message.seq), 0) + 1).where(
        Message.conversation_id == conversation_id
    )
    next_seq = (await session.execute(next_seq_stmt)).scalar_one()

    message = Message(
        conversation_id=conversation_id,
        user_id=user_id,
        seq=next_seq,
        role=role,
        content=content,
        intent=intent.value if intent is not None else None,
    )
    session.add(message)
    await session.flush()
    await session.refresh(message)
    return MessageView.model_validate(message)


async def get_recent_context(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    limit: int = DEFAULT_CONTEXT_WINDOW,
) -> list[MessageView]:
    """Return up to `limit` most recent messages, oldest to newest.

    Raises `ConversationNotFoundError` if the conversation is missing or not
    owned by `user_id`. Raises `ValueError` if `limit < 1`.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    await get_owned_conversation(session, user_id, conversation_id)

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.user_id == user_id)
        .order_by(Message.seq.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    messages = list(result.scalars())
    messages.reverse()
    return [MessageView.model_validate(message) for message in messages]


def _to_summary(
    conversation: Conversation, last_message_at: datetime | None, message_count: int
) -> ConversationSummary:
    """Build a `ConversationSummary` from a conversation row plus its aggregates."""
    updated_at = last_message_at if last_message_at is not None else conversation.created_at
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=updated_at,
        message_count=message_count,
    )


async def list_conversations(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 100
) -> list[ConversationSummary]:
    """Return `user_id`'s conversations, most recently active first.

    "Most recently active" is the conversation's latest message `created_at`,
    falling back to the conversation's own `created_at` when it has no
    messages yet. Only ever reads/returns conversations owned by `user_id`.
    Raises `ValueError` if `limit < 1`.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    last_message_at = func.max(Message.created_at)
    message_count = func.count(Message.id)
    stmt = (
        select(Conversation, last_message_at, message_count)
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
        .group_by(Conversation.id)
        .order_by(func.coalesce(last_message_at, Conversation.created_at).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        _to_summary(conversation, last_at, count) for conversation, last_at, count in result.all()
    ]


async def rename_conversation(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, title: str
) -> ConversationSummary:
    """Rename a conversation owned by `user_id` and return its updated summary.

    Raises `ConversationNotFoundError` if the conversation is missing or not
    owned by `user_id`. Raises `ValueError` if the stripped title is blank or
    exceeds 200 characters.
    """
    conversation = await get_owned_conversation(session, user_id, conversation_id, for_update=True)

    stripped = title.strip()
    if not stripped:
        raise ValueError("title must not be blank")
    if len(stripped) > 200:
        raise ValueError("title must be at most 200 characters")

    conversation.title = stripped
    await session.flush()

    stmt = select(func.count(Message.id), func.max(Message.created_at)).where(
        Message.conversation_id == conversation_id, Message.user_id == user_id
    )
    message_count, last_message_at = (await session.execute(stmt)).one()
    return _to_summary(conversation, last_message_at, message_count)


async def delete_conversation(
    session: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> None:
    """Delete a conversation owned by `user_id`.

    Raises `ConversationNotFoundError` if the conversation is missing or not
    owned by `user_id`. Deleting the conversation row cascades to its
    `messages` automatically (the composite FK on `messages` is
    `ondelete="CASCADE"`), but `hint_progress.conversation_id` has **no**
    foreign key at all, so its rows for this `(user_id, conversation_id)`
    would otherwise survive as orphans -- they are deleted explicitly here,
    before the conversation row itself.
    """
    conversation = await get_owned_conversation(session, user_id, conversation_id, for_update=True)

    await session.execute(
        delete(HintProgress).where(
            HintProgress.user_id == user_id, HintProgress.conversation_id == conversation_id
        )
    )
    await session.delete(conversation)
    await session.flush()


async def list_messages(
    session: AsyncSession,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    limit: int = 500,
    after_seq: int | None = None,
) -> list[MessageView]:
    """Return up to `limit` messages of a conversation, oldest to newest.

    Raises `ConversationNotFoundError` if the conversation is missing or not
    owned by `user_id`. Raises `ValueError` if `limit < 1`. When `after_seq`
    is given, only messages with `seq > after_seq` are returned.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    await get_owned_conversation(session, user_id, conversation_id)

    stmt = select(Message).where(
        Message.conversation_id == conversation_id, Message.user_id == user_id
    )
    if after_seq is not None:
        stmt = stmt.where(Message.seq > after_seq)
    stmt = stmt.order_by(Message.seq.asc()).limit(limit)

    result = await session.execute(stmt)
    return [MessageView.model_validate(message) for message in result.scalars()]

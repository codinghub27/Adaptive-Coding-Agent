"""`/conversations` and `/profile` -- thin, auth-scoped wrappers over the
Phase 03 conversation/profile stores.

The acting user always comes from `get_current_user` (bearer token); there is
never a caller-supplied `user_id`, mirroring `app.graph.api`'s convention.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.session import get_session
from app.memory.conversation import (
    ConversationNotFoundError,
    delete_conversation,
    list_conversations,
    list_messages,
    rename_conversation,
    start_conversation,
)
from app.memory.profile import get_profile
from app.schemas.auth import AuthUser
from app.schemas.conversation import (
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationRenameRequest,
    ConversationSummary,
    MessageView,
)
from app.schemas.profile import LearnerProfileView

__all__ = ["router"]

router = APIRouter(tags=["memory"])

#: Fixed detail string -- never interpolate a caller-supplied conversation id
#: into an error response.
DETAIL_CONVERSATION_NOT_FOUND = "conversation not found"


@router.post("/conversations", response_model=ConversationCreateResponse)
async def create_conversation(
    body: ConversationCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> ConversationCreateResponse:
    """Start a new conversation owned by the authenticated user.

    A UI otherwise has no way to obtain a `conversation_id` to pass to
    `/chat` -- this is that entry point.
    """
    title = body.title.strip() or None if body.title is not None else None
    conversation_id = await start_conversation(session, current_user.id, title=title)
    await session.commit()
    return ConversationCreateResponse(conversation_id=conversation_id, title=title)


@router.get("/profile", response_model=LearnerProfileView)
async def read_profile(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> LearnerProfileView:
    """Return the authenticated user's learner profile."""
    return await get_profile(session, current_user.id)


@router.get("/conversations", response_model=list[ConversationSummary])
async def read_conversations(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> list[ConversationSummary]:
    """List the authenticated user's conversations, most recently active first."""
    return await list_conversations(session, current_user.id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation_route(
    conversation_id: UUID,
    body: ConversationRenameRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> ConversationSummary:
    """Rename a conversation owned by the authenticated user."""
    try:
        summary = await rename_conversation(session, current_user.id, conversation_id, body.title)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail=DETAIL_CONVERSATION_NOT_FOUND) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await session.commit()
    return summary


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation_route(
    conversation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> Response:
    """Delete a conversation owned by the authenticated user."""
    try:
        await delete_conversation(session, current_user.id, conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail=DETAIL_CONVERSATION_NOT_FOUND) from None
    await session.commit()
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageView])
async def read_conversation_messages(
    conversation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
) -> list[MessageView]:
    """Return a conversation's messages, oldest to newest.

    `after_seq`, when given, returns only messages with `seq` strictly
    greater than it -- for incremental reload rather than a full refetch.
    """
    try:
        return await list_messages(
            session, current_user.id, conversation_id, limit=limit, after_seq=after_seq
        )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail=DETAIL_CONVERSATION_NOT_FOUND) from None

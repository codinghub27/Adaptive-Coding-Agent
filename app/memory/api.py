"""`/conversations` and `/profile` -- thin, auth-scoped wrappers over the
Phase 03 conversation/profile stores.

The acting user always comes from `get_current_user` (bearer token); there is
never a caller-supplied `user_id`, mirroring `app.graph.api`'s convention.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.session import get_session
from app.memory.conversation import start_conversation
from app.memory.profile import get_profile
from app.schemas.auth import AuthUser
from app.schemas.conversation import ConversationCreateRequest, ConversationCreateResponse
from app.schemas.profile import LearnerProfileView

__all__ = ["router"]

router = APIRouter(tags=["memory"])


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
